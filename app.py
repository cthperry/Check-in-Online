"""手機打卡服務和授權主機操作指令。"""
import argparse
import getpass
import os
import sqlite3
import sys
import time
from datetime import datetime

from attendance.store import TAIPEI, add_staff, close_missing, database, encode_password, initialize, transaction
from attendance.web import application


def main():
    parser = argparse.ArgumentParser(description="手機上下班打卡管理")
    parser.add_argument("--db", default=os.getenv("ATTENDANCE_DB","data/attendance.sqlite3"))
    commands = parser.add_subparsers(dest="action", required=True)
    serve = commands.add_parser("serve", help="啟動服務")
    serve.add_argument("--port",type=int, default=int(os.getenv("PORT","8000")))
    serve.add_argument("--host",default=os.getenv("BIND_HOST","127.0.0.1"))
    serve.add_argument("--dev",action="store_true",help="本機開發伺服器")
    add = commands.add_parser("add-user", help="新增員工或管理員")
    add.add_argument("account")
    add.add_argument("name")
    add.add_argument("--role",choices=("staff","admin"),default="staff")
    for action in ("reset-password","disable"):
        commands.add_parser(action).add_argument("account")
    missing = commands.add_parser("close-shift", help="補登遺漏的下班時間")
    missing.add_argument("shift",type=int)
    missing.add_argument("end",help="台灣時間 YYYY-MM-DDTHH:MM")
    missing.add_argument("--reason",required=True)
    missing.add_argument("--operator",required=True)
    commands.add_parser("open-shifts",help="列出待下班紀錄")
    args = parser.parse_args()
    if args.action == "serve":
        origin = os.getenv("ATTENDANCE_ORIGIN",f"http://127.0.0.1:{args.port}")
        if args.host not in ("127.0.0.1","localhost","::1") and (args.dev or not origin.startswith("https://")):
            parser.error("對外綁定需使用正式伺服器及 HTTPS 原始網址。")
        app = application(args.db,origin)
        if args.dev:
            from wsgiref.simple_server import make_server
            with make_server(args.host,args.port,app) as server:
                print(f"本機測試已啟動 {origin}",flush=True)
                server.serve_forever()
        else:
            from waitress import serve as run
            run(app,host=args.host,port=args.port,max_request_body_size=4096)
        return
    initialize(args.db)
    with database(args.db) as con:
        if args.action == "open-shifts":
            for row in con.execute("""SELECT t.id,s.account,s.name,t.started FROM shifts t
                    JOIN staff s ON t.staff_id=s.id WHERE ended IS NULL ORDER BY started"""):
                print(row["id"],row["account"],row["name"],datetime.fromtimestamp(row["started"],TAIPEI).isoformat())
        elif args.action == "close-shift":
            ended = datetime.strptime(args.end,"%Y-%m-%dT%H:%M").replace(tzinfo=TAIPEI)
            close_missing(con,args.shift,int(ended.timestamp()),args.reason,args.operator,int(time.time()))
        else:
            if args.action != "disable":
                secret = getpass.getpass("請輸入新密碼（至少 12 個字元） ")
                if secret != getpass.getpass("請再次輸入新密碼 "):
                    raise ValueError("兩次密碼不相同。")
            with transaction(con):
                if args.action == "add-user":
                    add_staff(con,args.account,args.name,args.role,secret)
                else:
                    row = con.execute("SELECT id FROM staff WHERE account=?",(args.account.strip().lower(),)).fetchone()
                    if not row:
                        raise ValueError("沒有這個帳號。")
                    if args.action == "disable":
                        con.execute("UPDATE staff SET active=0 WHERE id=?", (row["id"],))
                    else:
                        con.execute("UPDATE staff SET password_hash=? WHERE id=?",(encode_password(secret),row["id"]))
                    con.execute("DELETE FROM sessions WHERE staff_id=?", (row["id"],))
            print("帳號設定已更新。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.IntegrityError) as exc:
        print("操作失敗："+str(exc),file=sys.stderr)
        sys.exit(1)
