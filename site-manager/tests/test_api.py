"""End-to-end API checks against a real server on a temporary database."""
import base64
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\0" * 200).decode()


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None, header=True):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode())
        req.add_header("Content-Type", "application/json")
        if header:
            req.add_header("X-Requested-With", "site-manager")
        try:
            with self.opener.open(req) as r:
                raw = r.read()
                ctype = r.headers.get_content_type()
                return r.status, (json.loads(raw) if ctype == "application/json" else raw if ctype.startswith("image/") else raw.decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        app.DATA = cls.tmp
        app.init_db()
        cls.srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_full_flow(self):
        admin, worker_phone, sup, stranger = (Client(self.base) for _ in range(4))
        self.assertTrue(admin.call("GET", "/api/status")[1]["setup_needed"])
        self.assertEqual(admin.call("POST", "/api/setup", {"company": "Demo Infra", "name": "Owner", "email": "o@x.in", "password": "secret123"})[0], 200)
        self.assertEqual(stranger.call("POST", "/api/setup", {"company": "X", "name": "X", "email": "x@x.in", "password": "secret123"})[0], 403)
        self.assertEqual(stranger.call("GET", "/api/workers")[0], 401)
        # writes need the custom header (blocks cross-site form posts)
        self.assertEqual(admin.call("POST", "/api/sites", {"name": "X"}, header=False)[0], 403)

        _, site = admin.call("POST", "/api/sites", {"name": "Metro Depot", "client": "Metro Corp", "lat": 28.6139, "lng": 77.2090, "radius_m": 200, "bill_rate": 900})
        _, other = admin.call("POST", "/api/sites", {"name": "Mall Site"})
        _, w = admin.call("POST", "/api/workers", {"name": "Ramesh Kumar", "phone": "9876543210", "site_id": site["id"], "rate": 650, "pin": "1234",
                                                   "uan": "100012345678", "bank_account": "12345678901", "ifsc": "SBIN0001234"})
        self.assertEqual(admin.call("POST", "/api/workers", {"name": "Bad", "ifsc": "XYZ"})[0], 400)
        imp = admin.call("POST", "/api/workers/import", {"csv": "code,name,phone,site,rate,pin\nW0100,Sunita Devi,9811111111,Metro Depot,500,4321\nW0101,Nobody,1,Nowhere,1,1111\n"})[1]
        self.assertEqual((imp["added"], len(imp["errors"])), (1, 1))

        token = next(s["qr_token"] for s in admin.call("GET", "/api/sites")[1] if s["id"] == site["id"])
        self.assertEqual(worker_phone.call("GET", f"/api/public/site?t={token}")[1]["name"], "Metro Depot")
        # wrong PIN, then a good check-in 30 m from the gate
        self.assertEqual(worker_phone.call("POST", "/api/public/checkin", {"token": token, "worker": "9876543210", "pin": "0000"})[0], 403)
        st, r = worker_phone.call("POST", "/api/public/checkin", {"token": token, "worker": "9876543210", "pin": "1234", "lat": 28.6141, "lng": 77.2091, "accuracy": 10, "selfie": JPEG})
        self.assertEqual((st, r["result"], r["flagged"]), (200, "in", False))
        self.assertLess(r["distance_m"], 50)
        # a repeat scan straight away is ignored
        self.assertEqual(worker_phone.call("POST", "/api/public/checkin", {"token": token, "worker": "W0001", "pin": "1234", "lat": 28.6141, "lng": 77.2091, "selfie": JPEG})[1]["result"], "repeat")
        # Sunita scans from 1.4 km away: recorded but flagged
        st, r = worker_phone.call("POST", "/api/public/checkin", {"token": token, "worker": "W0100", "pin": "4321", "lat": 28.6265, "lng": 77.2090, "selfie": JPEG})
        self.assertTrue(r["flagged"])
        self.assertIn("km from site", r["flag_reason"])
        # too many wrong PINs lock the worker out for a while
        for _ in range(5):
            worker_phone.call("POST", "/api/public/checkin", {"token": token, "worker": "W0100", "pin": "9999"})
        self.assertEqual(worker_phone.call("POST", "/api/public/checkin", {"token": token, "worker": "W0100", "pin": "4321"})[0], 429)

        dash = admin.call("GET", "/api/dashboard")[1]
        metro = next(s for s in dash["sites"] if s["site"] == "Metro Depot")
        self.assertEqual((metro["present"], metro["flagged"]), (1, 1))
        flagged_id = dash["flagged"][0]["id"]
        self.assertEqual(admin.call("GET", f"/api/attendance/{flagged_id}/selfie")[0], 200)

        # supervisor: only their site, no pay data
        admin.call("POST", "/api/users", {"name": "Sup", "email": "s@x.in", "password": "secret123", "role": "supervisor", "sites": [site["id"]]})
        self.assertEqual(sup.call("POST", "/api/login", {"email": "s@x.in", "password": "secret123"})[0], 200)
        ws = sup.call("GET", "/api/workers")[1]
        self.assertNotIn("bank_account", ws[0])
        self.assertEqual(sup.call("GET", "/api/payroll/2026-10")[0], 403)
        self.assertEqual(sup.call("POST", "/api/attendance/mark", {"site_id": other["id"], "worker_ids": [w["id"]]})[0], 403)
        self.assertEqual(sup.call("PUT", f"/api/attendance/{flagged_id}", {"review": "approved"})[0], 200)

        # payroll for this month from the approved attendance
        month = app.today_ist()[:7]
        admin.call("POST", "/api/advances", {"worker_id": w["id"], "amount": 200})
        _, p = admin.call("POST", f"/api/payroll/{month}/run")
        ramesh = next(l for l in p["lines"] if l["code"] == "W0001")
        self.assertEqual((ramesh["days"], ramesh["advance"]), (1, 200))
        self.assertEqual(ramesh["net"], 650 - 78 - 5 - 200)     # PF 12 % of 650, ESIC 0.75 % rounded up
        self.assertIn("Ramesh Kumar,12345678901,SBIN0001234,367", admin.call("GET", f"/api/payroll/{month}/bank.csv")[1])
        self.assertIn("100012345678#~#RAMESH KUMAR", admin.call("GET", f"/api/payroll/{month}/ecr.txt")[1])
        self.assertEqual(admin.call("POST", f"/api/payroll/{month}/finalize")[0], 200)
        self.assertEqual(admin.call("POST", f"/api/payroll/{month}/run")[0], 409)
        rep = admin.call("GET", f"/api/reports/sites?month={month}")[1]["sites"]
        self.assertEqual(rep[0]["bill"], 1800)                   # 2 mandays x 900
        self.assertEqual(admin.call("GET", f"/api/sites/{site['id']}/qr.svg")[0], 200)
        self.assertGreater(len(admin.call("GET", "/api/audit")[1]), 5)


if __name__ == "__main__":
    unittest.main()
