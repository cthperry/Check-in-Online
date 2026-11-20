import io
import json
import secrets
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from wsgiref.util import setup_testing_defaults

from openpyxl import load_workbook

from attendance.store import DAY, TAIPEI, add_staff, close_missing, database, transaction
from attendance.web import application

ORIGIN = "https://attendance.example.test"


class AttendanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.template = Path(cls.fixture.name)/"template.sqlite3"
        cls.secret = secrets.token_urlsafe(24)
        application(cls.template,ORIGIN)
        with database(cls.template) as con:
            for name,role in (("a","staff"),("b","staff"),("manager","admin")):
                add_staff(con,name,"=測試"+name,role,cls.secret)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"test.sqlite3"
        self.path.write_bytes(self.template.read_bytes())
        self.now = int(datetime(2026,11,30,23,55,tzinfo=TAIPEI).timestamp())
        self.app = application(self.path,ORIGIN,lambda:self.now)

    def call(self,path,body=None,cookie="",origin=ORIGIN):
        env = {}
        setup_testing_defaults(env)
        route,_,query = path.partition("?")
        env.update(PATH_INFO=route, QUERY_STRING=query, HTTP_COOKIE=cookie, HTTP_ORIGIN=origin)
        if body is not None:
            data = json.dumps(body).encode()
            env.update(REQUEST_METHOD="POST",CONTENT_TYPE="application/json",CONTENT_LENGTH=str(len(data)))
            env["wsgi.input"] = io.BytesIO(data)
        response = {}
        def start(status,headers):
            response.update(code=int(status[:3]),headers=dict(headers))
        buf = b"".join(self.app(env,start))
        response["data"] = json.loads(buf) if response["headers"]["Content-Type"].startswith("application/json") else buf
        return response

    def session(self,account="a",remember=True):
        result = self.call("/api/login",{"account":account,"password":self.secret,"remember":remember})
        self.assertEqual(result["code"],200,result["data"])
        return result["headers"]["Set-Cookie"]

    def punch(self,cookie,action="in",revision=0,key=None):
        return self.call("/api/punch",{"action":action,"revision":revision,"key":key or str(uuid4())},cookie)

    def test_session_lifetime_logout_and_reload(self):
        header = self.session()
        for attribute in ("Secure","HttpOnly","Max-Age=2592000","SameSite=Lax"):
            self.assertIn(attribute,header)
        session = header.split(";")[0]
        self.app = application(self.path,ORIGIN,lambda:self.now)
        self.assertEqual(self.call("/api/state",cookie=session)["data"]["user"]["account"],"a")
        short = self.session(remember=False)
        self.assertNotIn("Max-Age",short)
        self.now += DAY
        self.assertEqual(self.call("/api/state",cookie=short.split(";")[0])["code"],401)
        self.assertEqual(self.call("/api/logout",{},session)["code"],200)
        self.assertEqual(self.call("/api/state",cookie=session)["code"],401)
        fresh = self.session().split(";")[0]
        self.now += 30*DAY
        self.assertEqual(self.call("/api/state",cookie=fresh)["code"],401)

    def test_two_devices_racing_and_retry(self):
        phone,desktop = [self.session().split(";")[0] for _ in range(2)]
        keys = [str(uuid4()),str(uuid4())]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda pair:self.punch(pair[0],key=pair[1]),
                                    zip((phone,desktop),keys)))
        self.assertEqual(sorted(r["code"] for r in results),[200,409])
        key = keys[next(i for i,r in enumerate(results) if r["code"]==200)]
        self.now += 600
        self.assertEqual(self.punch(desktop,"out",1)["code"],200)
        self.assertEqual(self.punch(phone,key=key)["data"]["stamp"],self.now-600)
        self.assertEqual(self.punch(phone,"out",1,key)["code"],409)
        self.assertEqual(self.call("/api/state",cookie=phone)["data"]["user"]["revision"],2)
        with database(self.path) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM shifts").fetchone()[0],1)

    def test_midnight_permissions_export_and_corrected_cells(self):
        a,b,manager = [self.session(who).split(";")[0] for who in ("a","b","manager")]
        self.punch(a); self.punch(b)
        self.now += 1200
        self.punch(a,"out",1)
        query = "?from=2026-11-30&to=2026-11-30&scope="
        with database(self.path) as con:
            sid = con.execute("SELECT id FROM shifts WHERE ended IS NULL").fetchone()[0]
            close_missing(con,sid,self.now-100,"=漏打下班","測試經手人",self.now)
        for path in ("/api/records","/api/export"):
            self.assertEqual(self.call(path+query+"all",cookie=b)["code"],403)
            self.assertEqual(self.call(path+query+"mine")["code"],401)
        mine = self.call("/api/export"+query+"mine",cookie=a)
        book = load_workbook(io.BytesIO(mine["data"]))
        sheet = book.active
        self.assertEqual(sheet.max_row,2)
        self.assertEqual(sheet["F2"].value.day,1)
        self.assertEqual(sheet["G2"].value,timedelta(minutes=20))
        self.assertEqual(sheet["C2"].data_type,"s")
        self.assertEqual(book["補登紀錄"].max_row,1)
        all_book = load_workbook(io.BytesIO(self.call("/api/export"+query+"all",cookie=manager)["data"]))
        self.assertEqual(all_book.active.max_row,3)
        self.assertEqual(all_book["補登紀錄"]["F2"].data_type,"s")
        self.assertIsNone(all_book["補登紀錄"]["D2"].value)
        self.assertEqual(self.call("/api/records?from=2026-12-01&to=2026-12-01",cookie=a)["data"]["rows"],[])

    def test_admin_edits_cross_midnight_and_audits(self):
        staff, admin = [self.session(who).split(";")[0] for who in ("a", "manager")]
        self.punch(staff)
        self.now += 1200
        query = "?from=2026-11-30&to=2026-11-30"
        row = self.call("/api/records"+query,cookie=staff)["data"]["rows"][0]
        edit = {"id":row["id"],"priorStart":row["started"],"priorEnd":None,
                "start":"2026-11-30T23:50:15","end":"2026-12-01T00:02:10","reason":"=漏打下班"}
        route = "/api/records/edit"
        self.assertEqual(self.call(route,edit)["code"],401)
        other_staff = self.session("b").split(";")[0]
        self.assertEqual(self.call(route,edit,other_staff)["code"],404)
        self.assertEqual(self.call(route,edit,admin,"https://other.example.test")["code"],403)
        self.assertEqual(self.call(route,{**edit,"reason":" "},admin)["code"],400)
        self.assertEqual(self.call(route,{**edit,"start":"2026-11-31T00:00"},admin)["code"],400)
        self.assertEqual(self.call(route,{**edit,"end":"2026-11-30T22:00"},admin)["code"],400)
        self.assertEqual(self.call(route,{**edit,"end":"2026-12-01T00:16"},admin)["code"],400)
        self.assertEqual(self.call(route,edit,admin)["code"],200)
        self.assertEqual(self.call(route,edit,admin)["code"],409)
        state = self.call("/api/state",cookie=staff)["data"]
        self.assertIsNone(state["open"])
        self.assertEqual(state["user"]["revision"],2)
        self.assertEqual(self.punch(staff,revision=1)["code"],409)
        data = self.call("/api/export"+query,cookie=staff)["data"]
        book = load_workbook(io.BytesIO(data))
        self.assertEqual(book.active["E2"].value, datetime(2026,11,30,23,50,15))
        self.assertEqual(book.active["F2"].value, datetime(2026,12,1,0,2,10))
        audit = book["補登紀錄"]
        self.assertEqual(audit.max_row,2)
        self.assertIsNone(audit["D2"].value)
        self.assertEqual(audit["F2"].value,"=漏打下班")
        self.assertEqual(audit["F2"].data_type,"s")
        self.assertEqual(audit["G2"].value,"manager")
        self.assertEqual(audit["I2"].value, datetime(2026,11,30,23,55))
        self.assertEqual(audit["J2"].value, datetime(2026,11,30,23,50,15))

        corrected = self.call("/api/records"+query,cookie=staff)["data"]["rows"][0]
        self.assertEqual(self.punch(staff,revision=2)["code"],200)
        self.assertEqual(self.call(route,{**edit,"priorStart":corrected["started"],
                                          "priorEnd":corrected["ended"],"end":""},admin)["code"],409)
        self.now += 60
        self.assertEqual(self.call(route,{**edit,"priorStart":corrected["started"],
                                          "priorEnd":corrected["ended"],"end":"2026-12-01T00:15:30"},admin)["code"],409)

    def test_staff_can_edit_own_shift_with_note(self):
        staff = self.session().split(";")[0]
        self.punch(staff)
        self.now += 600
        self.punch(staff, "out", 1)
        query = "?from=2026-11-30&to=2026-11-30"
        row = self.call("/api/records"+query,cookie=staff)["data"]["rows"][0]
        edit = {"id":row["id"],"priorStart":row["started"],"priorEnd":row["ended"],
                "start":"2026-11-30T23:50","end":"2026-12-01T00:00","reason":"  修正上下班時間  "}
        route = "/api/records/edit"
        self.assertEqual(self.call(route,{**edit,"reason":" "},staff)["code"],400)
        self.assertEqual(self.call(route,edit,staff)["code"],200)
        edited = self.call("/api/records"+query,cookie=staff)["data"]["rows"][0]
        self.assertTrue(edited["corrected"])
        self.assertEqual(edited["started"], row["started"]-300)
        self.assertEqual(edited["ended"], row["ended"]-300)
        with database(self.path) as con:
            self.assertEqual(tuple(con.execute("SELECT operator,reason FROM shift_edits").fetchone()),
                             ("a","修正上下班時間"))

    def test_validation_revocation_and_audit(self):
        a = self.session().split(";")[0]
        self.assertEqual(self.call("/api/logout",{},a,"https://other.example.test")["code"],403)
        self.assertEqual(self.call("/api/records?from=2026-11-31&to=2026-12-02",cookie=a)["code"],400)
        self.assertEqual(self.call("/api/records?from=2024-01-01&to=2026-12-02",cookie=a)["code"],400)
        self.assertEqual(self.punch(a,"out")["code"],409)
        with database(self.path) as con:
            con.execute("UPDATE staff SET active=0 WHERE account='a'")
        self.assertEqual(self.call("/api/state",cookie=a)["code"],401)
        self.assertEqual(self.punch(a)["code"],401)
        self.assertEqual(self.call("/api/login",{"account":"a","password":self.secret,"remember":True})["code"],401)
        with database(self.path) as con:
            rows = list(con.execute("SELECT success FROM login_events ORDER BY id"))
            self.assertEqual([row[0] for row in rows],[1,0])

    def test_new_shift_open_duration_and_pagination(self):
        a = self.session().split(";")[0]
        for idx in range(31):
            self.assertEqual(self.punch(a,"in",idx*2)["code"],200)
            if idx != 30:
                self.assertEqual(self.punch(a,"out",idx*2+1)["code"],200)
        query = "?from=2026-11-30&to=2026-11-30"
        first = self.call("/api/records"+query,cookie=a)["data"]
        self.assertTrue(first["hasMore"]); self.assertEqual(len(first["rows"]),30)
        second = self.call("/api/records"+query+"&page=2",cookie=a)["data"]
        self.assertFalse(second["hasMore"]); self.assertEqual(len(second["rows"]),1)
        sheet = load_workbook(io.BytesIO(self.call("/api/export"+query,cookie=a)["data"])).active
        self.assertEqual(sheet.max_row,32); self.assertIsNone(sheet["F2"].value)
        self.assertIsNone(sheet["G2"].value); self.assertEqual(sheet["H2"].value,"待下班")

if __name__ == "__main__":
    unittest.main()
