"""同源手機前端與 WSGI API。"""
import json
import logging
import sqlite3
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .export import workbook
from .store import Problem, current_staff, database, initialize, login, open_shift, punch, records, token_digest

STATIC = Path(__file__).resolve().parent.parent / "static"
ASSETS = {"/":("index.html","text/html"), "/app.css":("app.css","text/css"),
          "/app.js":("app.js","text/javascript")}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def json_response(obj, code=200, headers=()):
    return code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode(), list(headers)


def read_body(env, origin):
    if env.get("HTTP_ORIGIN") != origin:
        raise Problem(403, "請從系統原網址送出操作。")
    if env.get("CONTENT_TYPE","").split(";")[0].strip() != "application/json":
        raise Problem(415, "要求必須使用 JSON 格式。")
    try:
        length = int(env.get("CONTENT_LENGTH") or 0)
        if not 0 < length <= 4096:
            raise Problem(413, "要求大小不符。")
        body = json.loads(env["wsgi.input"].read(length))
        if not isinstance(body,dict):
            raise ValueError
        return body
    except (ValueError,UnicodeError):
        raise Problem(400, "要求內容格式有誤。") from None


def session_token(env):
    cookie = SimpleCookie()
    try:
        cookie.load(env.get("HTTP_COOKIE",""))
    except CookieError:
        return ""
    item = cookie.get("attendance")
    return item.value if item else ""


def application(path, origin, clock=time.time):
    origin = origin.rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.path or parsed.query
            or parsed.fragment or parsed.username or parsed.password
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost","127.0.0.1","::1"})):
        raise ValueError("對外部署請設定完整 HTTPS 網址，本機測試可使用回送位址。")
    flags = "; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if parsed.scheme == "https" else "")
    initialize(path)

    def dispatch(env):
        method, route = env["REQUEST_METHOD"],env.get("PATH_INFO","")
        if method == "GET" and route in ASSETS:
            filename, mime = ASSETS[route]
            return 200, mime+"; charset=utf-8", (STATIC/filename).read_bytes(), []
        allowed = {"/api/login":"POST","/api/logout":"POST","/api/punch":"POST",
                   "/api/state":"GET","/api/records":"GET","/api/export":"GET"}
        if route not in allowed:
            raise Problem(404, "找不到此頁面。")
        if method != allowed[route]:
            raise Problem(405, "此操作不支援這個要求方式。")
        body = read_body(env, origin) if method == "POST" else {}
        token = session_token(env)
        now = int(clock())
        with database(path) as con:
            if route == "/api/login":
                if not all(isinstance(body.get(k),str) for k in ("account","password")) or type(body.get("remember")) is not bool:
                    raise Problem(400, "請輸入帳號與密碼。")
                token, ttl = login(con,body["account"],body["password"],body["remember"],env.get("REMOTE_ADDR",""),now)
                extra = f"; Max-Age={ttl}" if body["remember"] else ""
                return json_response({"ok":True},headers=[("Set-Cookie","attendance="+token+flags+extra)])
            if route == "/api/logout":
                con.execute("DELETE FROM sessions WHERE digest=?", (token_digest(token),))
                return json_response({"ok":True},headers=[("Set-Cookie","attendance="+flags+"; Max-Age=0")])
            if route == "/api/punch":
                if not isinstance(body.get("key"),str):
                    raise Problem(400, "請重新送出打卡。")
                stamp = punch(con,token,body.get("action"),body.get("revision"),body["key"],clock)
                return json_response({"stamp":stamp})
            # 同一個讀取快照包含員工狀態、班次與匯出附表。
            con.execute("BEGIN")
            staff = current_staff(con,token,now)
            if route == "/api/state":
                opened = open_shift(con,staff["id"])
                last = con.execute("SELECT started,ended FROM shifts WHERE staff_id=? ORDER BY id DESC LIMIT 1",
                                   (staff["id"],)).fetchone()
                return json_response({"user":dict(staff),"open":dict(opened) if opened else None,
                                      "latest":dict(last) if last else None,"now":now})
            params = parse_qs(env.get("QUERY_STRING",""),keep_blank_values=True)
            def param(key, default=""):
                return params.get(key,[default])[0]
            start,end,scope = param("from"),param("to"),param("scope","mine")
            if route == "/api/export":
                rows = records(con,staff,start,end,scope)
                return 200, XLSX, workbook(con,rows), [("Content-Disposition",
                    f'attachment; filename="attendance-{scope}-{start}-{end}.xlsx"')]
            try:
                page = int(param("page","1"))
                if not 1 <= page <= 100000:
                    raise ValueError
            except ValueError:
                raise Problem(400, "頁碼無效。") from None
            items = list(records(con,staff,start,end,scope,page))
            return json_response({"rows":[dict(r) for r in items[:30]],"hasMore":len(items)>30})

    def handle(env,start_response):
        try:
            result = dispatch(env)
        except Problem as e:
            result = json_response({"error":str(e)},e.status)
        except sqlite3.OperationalError as e:
            logging.exception("資料庫操作失敗")
            result = json_response({"error":"資料庫暫時無法使用，請重新查詢狀態。"},503)
        except Exception:
            logging.exception("處理出勤要求失敗")
            result = json_response({"error":"系統暫時無法完成操作，請重新查詢。"},500)
        code,mime,data,headers = result
        headers.extend([("Content-Type",mime),("Content-Length",str(len(data))),("Cache-Control","no-store"),
                        ("X-Content-Type-Options","nosniff"),("Referrer-Policy","no-referrer"),
                        ("Content-Security-Policy","default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")])
        start_response(f"{code} {HTTPStatus(code).phrase}",headers)
        return [data]
    return handle
