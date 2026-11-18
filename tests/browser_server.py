"""提供瀏覽器驗收專用的暫存資料庫與隨機連接埠。"""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from wsgiref.simple_server import make_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from attendance.store import add_staff, database
from attendance.web import application

with TemporaryDirectory() as directory:
    db = Path(directory)/"browser.sqlite3"
    with make_server("127.0.0.1",0,None) as server:
        origin = f"http://127.0.0.1:{server.server_port}"
        server.set_app(application(db,origin))
        with database(db) as con:
            add_staff(con,"qa-admin","測試管理員","admin",os.environ["ATTENDANCE_TEST_PASSWORD"])
        print(origin,flush=True)
        server.serve_forever()
