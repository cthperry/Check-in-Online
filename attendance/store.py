"""出勤資料與登入工作階段。時間使用 UTC Unix 秒。"""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path

TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")
DAY = 86400
ACCOUNT = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
 id INTEGER PRIMARY KEY, account TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 role TEXT NOT NULL CHECK (role IN ('staff','admin')), password_hash TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
 digest TEXT PRIMARY KEY, staff_id INTEGER NOT NULL REFERENCES staff(id), expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_staff ON sessions(staff_id);
CREATE TABLE IF NOT EXISTS shifts (
 id INTEGER PRIMARY KEY, staff_id INTEGER NOT NULL REFERENCES staff(id),
 started INTEGER NOT NULL, ended INTEGER CHECK(ended >= started)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open ON shifts(staff_id) WHERE ended IS NULL;
CREATE INDEX IF NOT EXISTS shift_date ON shifts(started,staff_id);
CREATE INDEX IF NOT EXISTS shift_staff_date ON shifts(staff_id,started);
CREATE TABLE IF NOT EXISTS requests (
 staff_id INTEGER NOT NULL REFERENCES staff(id), request_id TEXT NOT NULL,
 action TEXT NOT NULL, revision INTEGER NOT NULL, stamp INTEGER NOT NULL,
 PRIMARY KEY(staff_id,request_id)
);
CREATE TABLE IF NOT EXISTS login_events (
 id INTEGER PRIMARY KEY, account TEXT NOT NULL, at INTEGER NOT NULL,
 source TEXT NOT NULL, success INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_account ON login_events(account,at);
CREATE INDEX IF NOT EXISTS login_source ON login_events(source,at);
CREATE TABLE IF NOT EXISTS corrections (
 id INTEGER PRIMARY KEY, shift_id INTEGER NOT NULL REFERENCES shifts(id),
 prior_end INTEGER, new_end INTEGER NOT NULL, reason TEXT NOT NULL,
 operator TEXT NOT NULL, recorded INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS corrections_shift ON corrections(shift_id);
"""

class Problem(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@contextmanager
def database(path):
    con = sqlite3.connect(path, isolation_level=None, timeout=8)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
    finally:
        con.close()


@contextmanager
def transaction(con):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
        con.commit()
    except BaseException:
        con.rollback()
        raise


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with database(path) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)


def password_digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1,
                          maxmem=192 * 1024 * 1024, dklen=32)


def encode_password(password):
    if not 12 <= len(password) <= 256:
        raise ValueError("密碼長度需為 12 至 256 個字元。")
    salt = secrets.token_bytes(16)
    return salt.hex() + "." + password_digest(password, salt).hex()


def add_staff(con, account, name, role, password):
    account = account.strip().lower()
    if not ACCOUNT.fullmatch(account) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("請確認帳號格式與姓名長度。")
    con.execute("INSERT INTO staff(account,name,role,password_hash) VALUES(?,?,?,?)",
                (account, name.strip(), role, encode_password(password)))


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def login(con, account, password, remember, source, now):
    account = account.strip().lower()
    if len(account) > 48 or len(password) > 256:
        raise Problem(401, "帳號或密碼有誤。")
    with transaction(con):
        con.execute("DELETE FROM login_events WHERE at < ?", (now - 90 * DAY,))
        limiting = con.execute("""SELECT
          (SELECT count(*) FROM login_events WHERE account=? AND at>? AND success=0) >= 8
          OR (SELECT count(*) FROM login_events WHERE source=? AND at>? AND success=0) >= 120""",
                               (account, now - 600, source, now - 600)).fetchone()[0]
        if limiting:
            raise Problem(429, "登入嘗試次數過多，請稍後再試。")
        row = con.execute("SELECT * FROM staff WHERE account=?", (account,)).fetchone()
        # 先保留失敗紀錄，使同時送達的嘗試也計入限制。
        event = con.execute("INSERT INTO login_events(account,at,source,success) VALUES(?,?,?,0)",
                            (account, now, source)).lastrowid
    stored = row["password_hash"] if row else "00"*16 + "." + "00"*32
    salt, expected = stored.split(".")
    valid = hmac.compare_digest(password_digest(password, bytes.fromhex(salt)), bytes.fromhex(expected))
    if not valid or not row or not row["active"]:
        raise Problem(401, "帳號或密碼有誤。")
    token = secrets.token_urlsafe(32)
    lifetime = 30 * DAY if remember else DAY // 2
    with transaction(con):
        current = con.execute("SELECT active,password_hash FROM staff WHERE id=?", (row["id"],)).fetchone()
        if not current["active"] or current["password_hash"] != stored:
            raise Problem(401, "帳號狀態已變更，請重新登入。")
        con.execute("DELETE FROM sessions WHERE expires<=?", (now,))
        con.execute("UPDATE login_events SET success=1 WHERE id=?", (event,))
        con.execute("INSERT INTO sessions VALUES(?,?,?)", (token_digest(token), row["id"], now+lifetime))
    return token, lifetime


def current_staff(con, token, now):
    row = con.execute("""SELECT s.id,s.account,s.name,s.role,s.revision FROM staff s
        JOIN sessions x ON x.staff_id=s.id WHERE s.active=1 AND x.digest=? AND x.expires>?""",
                      (token_digest(token), now)).fetchone()
    if row is None:
        raise Problem(401, "請登入以使用打卡服務。")
    return row


def open_shift(con, staff_id):
    return con.execute("SELECT id,started,ended FROM shifts WHERE staff_id=? AND ended IS NULL",
                       (staff_id,)).fetchone()


def punch(con, token, action, revision, key, clock=time.time):
    if action not in ("in","out") or type(revision) is not int or not re.fullmatch(r"[0-9a-f-]{36}", key):
        raise Problem(400, "打卡要求格式有誤，請重新整理。")
    with transaction(con):
        now = int(clock())
        staff = current_staff(con, token, now)
        sid = staff["id"]
        previous = con.execute("SELECT * FROM requests WHERE staff_id=? AND request_id=?", (sid,key)).fetchone()
        if previous:
            if (previous["action"], previous["revision"]) != (action,revision):
                raise Problem(409, "操作識別碼已使用，請重新整理。")
            return previous["stamp"]
        opened = open_shift(con, sid)
        if staff["revision"] != revision or (action == "in") == bool(opened):
            raise Problem(409, "出勤狀態已更新，請確認最新紀錄。")
        if action == "in":
            con.execute("INSERT INTO shifts(staff_id,started) VALUES(?,?)", (sid,now))
        else:
            if now < opened["started"]:
                raise Problem(409, "伺服器時間早於上班時間，請聯絡管理員。")
            con.execute("UPDATE shifts SET ended=? WHERE id=?", (now,opened["id"]))
        con.execute("UPDATE staff SET revision=revision+1 WHERE id=?", (sid,))
        con.execute("INSERT INTO requests VALUES(?,?,?,?,?)", (sid,key,action,revision,now))
        return now


def date_range(start, end):
    try:
        if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", x) for x in (start,end)):
            raise ValueError
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if not 0 <= (last-first).days < 366:
            raise ValueError
        return tuple(int(datetime.combine(d, day_time.min, TAIPEI).timestamp())
                     for d in (first,last+timedelta(days=1)))
    except (ValueError, OverflowError, OSError):
        raise Problem(400, "請選擇有效日期，單次查詢最多 366 天。") from None


def records(con, staff, start, end, scope, page=None):
    low, high = date_range(start,end)
    if scope not in ("mine","all"):
        raise Problem(400, "查詢範圍無效。")
    if scope == "all" and staff["role"] != "admin":
        raise Problem(403, "此帳號只能查詢個人紀錄。")
    sql = """SELECT t.id,s.account,s.name,t.started,t.ended,
        EXISTS(SELECT 1 FROM corrections c WHERE c.shift_id=t.id) corrected
        FROM shifts t JOIN staff s ON s.id=t.staff_id WHERE t.started>=? AND t.started<?"""
    args = [low,high]
    if scope == "mine":
        sql += " AND t.staff_id=?"
        args.append(staff["id"])
    sql += " ORDER BY t.started DESC,t.id DESC"
    if page is not None:
        sql += " LIMIT 31 OFFSET ?"
        args.append((page-1)*30)
    return con.execute(sql,args)


def close_missing(con, shift_id, ended, reason, operator, now):
    if not reason.strip() or not operator.strip():
        raise ValueError("請填寫原因及經手人。")
    with transaction(con):
        row = con.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if row is None or row["ended"] is not None:
            raise ValueError("請指定尚未下班的班次編號。")
        if not row["started"] <= ended <= now:
            raise ValueError("補登時間必須介於上班時間與現在之間。")
        con.execute("INSERT INTO corrections(shift_id,prior_end,new_end,reason,operator,recorded) VALUES(?,NULL,?,?,?,?)",
                    (shift_id, ended, reason.strip(), operator.strip(), now))
        con.execute("UPDATE shifts SET ended=? WHERE id=?", (ended,shift_id))
        con.execute("UPDATE staff SET revision=revision+1 WHERE id=?", (row["staff_id"],))
