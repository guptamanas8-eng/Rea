#!/usr/bin/env python3
"""Site Manager: attendance and payroll for multi-site contractors.

Python 3.9+ standard library only (SQLite database, no packages to install).
  python3 app.py                     # serve on http://0.0.0.0:8080 (data in ./data)
  python3 app.py --port 9000 --data /srv/site-manager
Run it behind HTTPS in production: phones only share GPS location with secure (https) pages.
"""
import argparse
import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import payroll  # noqa: E402
import qr  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:                      # no tz database: India has no daylight saving, so a fixed offset is exact
    IST = timezone(timedelta(hours=5, minutes=30))

DATA = os.environ.get("SITE_MANAGER_DATA", os.path.join(HERE, "data"))
SESSION_DAYS = 30
PIN_TRIES = 5                          # wrong PINs per worker per 15 minutes
_db_lock = threading.Lock()
_pin_fails = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','supervisor')),
  pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_sites (user_id INTEGER NOT NULL, site_id INTEGER NOT NULL, PRIMARY KEY (user_id, site_id));
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, client TEXT DEFAULT '', address TEXT DEFAULT '',
  lat REAL, lng REAL, radius_m INTEGER NOT NULL DEFAULT 200, qr_token TEXT UNIQUE NOT NULL,
  bill_rate REAL DEFAULT 0, bill_ot_rate REAL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workers (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, phone TEXT DEFAULT '', designation TEXT DEFAULT '',
  site_id INTEGER, wage_type TEXT NOT NULL DEFAULT 'daily' CHECK(wage_type IN ('daily','monthly')), rate REAL NOT NULL DEFAULT 0,
  pin_hash TEXT, uan TEXT DEFAULT '', esic_no TEXT DEFAULT '', pf_member INTEGER NOT NULL DEFAULT 1, esic_member INTEGER NOT NULL DEFAULT 1,
  bank_account TEXT DEFAULT '', ifsc TEXT DEFAULT '', aadhaar_last4 TEXT DEFAULT '', joined TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attendance (
  id INTEGER PRIMARY KEY, worker_id INTEGER NOT NULL, site_id INTEGER NOT NULL, day TEXT NOT NULL,
  in_time TEXT, out_time TEXT, in_lat REAL, in_lng REAL, in_dist INTEGER, out_lat REAL, out_lng REAL, out_dist INTEGER,
  method TEXT NOT NULL DEFAULT 'qr', selfie TEXT, status TEXT NOT NULL DEFAULT 'P', status_manual INTEGER NOT NULL DEFAULT 0,
  ot_override REAL, review TEXT NOT NULL DEFAULT 'ok' CHECK(review IN ('ok','flagged','approved','rejected')),
  flag_reason TEXT DEFAULT '', marked_by INTEGER, UNIQUE(worker_id, day));
CREATE TABLE IF NOT EXISTS advances (
  id INTEGER PRIMARY KEY, worker_id INTEGER NOT NULL, day TEXT NOT NULL, amount REAL NOT NULL, note TEXT DEFAULT '', created_by INTEGER);
CREATE TABLE IF NOT EXISTS payroll_runs (month TEXT PRIMARY KEY, status TEXT NOT NULL, lines TEXT NOT NULL, created TEXT NOT NULL, finalized TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, action TEXT NOT NULL, detail TEXT);
"""


# ------------------------------------------------------------------ helpers
def now_ist():
    return datetime.now(IST)


def today_ist():
    return now_ist().date().isoformat()


def db():
    conn = sqlite3.connect(os.path.join(DATA, "site-manager.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def init_db():
    os.makedirs(os.path.join(DATA, "selfies"), exist_ok=True)
    try:
        os.chmod(DATA, 0o700)
    except OSError:
        pass
    c = db()
    try:
        c.executescript(SCHEMA)
    finally:
        c.close()


def settings(c):
    s = dict(payroll.DEFAULT_SETTINGS)
    for r in c.execute("SELECT key, value FROM settings"):
        if r["key"] in s:
            s[r["key"]] = json.loads(r["value"])
    return s


def hash_secret(secret, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def check_secret(secret, stored):
    try:
        _, salt, h = stored.split("$")
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(hash_secret(secret, salt).split("$")[2], h)


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def audit(c, user_id, action, detail=""):
    c.execute("INSERT INTO audit (at, user_id, action, detail) VALUES (?,?,?,?)",
              (now_ist().isoformat(timespec="seconds"), user_id, action, json.dumps(detail)[:2000] if not isinstance(detail, str) else detail[:2000]))


def clean(v, n=120):
    return re.sub(r"[\x00-\x1f]", "", str(v if v is not None else "")).strip()[:n]


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def need(cond, status, message):
    if not cond:
        raise ApiError(status, message)


# ------------------------------------------------------------------ payroll from the database
def month_lines(c, month, s):
    start, end, _ = payroll.month_bounds(month)
    workers = rows(c.execute("SELECT * FROM workers ORDER BY code"))
    att = {}
    for r in rows(c.execute("SELECT * FROM attendance WHERE day BETWEEN ? AND ? AND review IN ('ok','approved')",
                            (start.isoformat(), end.isoformat()))):
        att.setdefault(r["worker_id"], []).append(r)
    adv = {}
    for r in c.execute("SELECT worker_id, amount FROM advances WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat())):
        adv.setdefault(r["worker_id"], []).append(r["amount"])
    lines = []
    for w in workers:
        if not att.get(w["id"]) and not adv.get(w["id"]) and not w["active"]:
            continue
        line = payroll.compute_worker(w, att.get(w["id"], []), adv.get(w["id"], []), s, month)
        if line["days"] or line["advance"] or line["advance_carried"]:
            lines.append(line)
    return lines


def site_report(c, month, s):
    """Mandays, labour cost, client bill and margin per site for a month."""
    start, end, _ = payroll.month_bounds(month)
    lines = {l["worker_id"]: l for l in month_lines(c, month, s)}
    sites = {r["id"]: dict(r, mandays=0.0, ot_hours=0.0, cost=0.0) for r in c.execute("SELECT * FROM sites")}
    for r in rows(c.execute("SELECT * FROM attendance WHERE day BETWEEN ? AND ? AND review IN ('ok','approved')",
                            (start.isoformat(), end.isoformat()))):
        site = sites.get(r["site_id"])
        line = lines.get(r["worker_id"])
        if not site or not line or not line["days"]:
            continue
        d, o = payroll.day_credit(r, s)
        site["mandays"] += d
        site["ot_hours"] += o
        # this day's share of the worker's cost to the company (wages plus employer PF and ESIC)
        company_cost = line["gross"] + line["eps_employer"] + line["epf_employer"] + line["esic_employer"]
        site["cost"] += company_cost * d / line["days"]
    out = []
    for site in sites.values():
        if not site["mandays"]:
            continue
        bill = site["mandays"] * (site["bill_rate"] or 0) + site["ot_hours"] * (site["bill_ot_rate"] or 0)
        out.append({"site_id": site["id"], "site": site["name"], "client": site["client"], "mandays": site["mandays"],
                    "ot_hours": site["ot_hours"], "labour_cost": round(site["cost"]), "bill": round(bill),
                    "gst": round(bill * s["gst_rate"] / 100), "margin": round(bill - site["cost"]),
                    "bill_rate": site["bill_rate"], "bill_ot_rate": site["bill_ot_rate"]})
    return sorted(out, key=lambda x: x["site"])


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "SiteManager"

    def log_message(self, fmt, *args):
        pass

    # -- responses
    def send(self, status, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if not ctype.startswith("image/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def page(self, name):
        with open(os.path.join(HERE, "web", name), "rb") as f:
            self.send(200, f.read(), "text/html",
                      {"Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'"})

    def download(self, text, filename, ctype="text/csv"):
        self.send(200, text, ctype, {"Content-Disposition": f'attachment; filename="{filename}"'})

    # -- auth
    def is_https(self):
        return self.headers.get("X-Forwarded-Proto", "") == "https" or os.environ.get("SITE_MANAGER_HTTPS") == "1"

    def current_user(self, c):
        m = re.search(r"(?:^|;\s*)sm_session=([A-Za-z0-9_\-]+)", self.headers.get("Cookie", ""))
        if not m:
            return None
        r = c.execute("SELECT u.*, s.expires FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND u.active = 1",
                      (m.group(1),)).fetchone()
        if not r or r["expires"] < now_ist().isoformat():
            return None
        u = dict(r)
        u["sites"] = [x["site_id"] for x in c.execute("SELECT site_id FROM user_sites WHERE user_id = ?", (u["id"],))]
        return u

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        need(n <= 3_000_000, 413, "Request too large")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(400, "Bad JSON")

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_DELETE(self):
        self.handle_any("DELETE")

    def handle_any(self, method):
        u = urlparse(self.path)
        path, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            # pages
            if method == "GET" and path in ("/", "/app"):
                return self.page("admin.html")
            if method == "GET" and re.fullmatch(r"/c/[A-Za-z0-9_\-]{8,40}", path):
                return self.page("checkin.html")
            if method == "GET" and path == "/favicon.ico":
                return self.send(204, b"", "image/x-icon")
            if not path.startswith("/api/"):
                raise ApiError(404, "Not found")
            # the JSON API: state-changing calls must come from our own pages (a custom header blocks cross-site forms)
            if method != "GET" and self.headers.get("X-Requested-With") != "site-manager":
                raise ApiError(403, "Missing request header")
            with _db_lock if method != "GET" else _nolock:
                c = db()
                try:
                    with c:                      # commits on success, rolls back on error
                        result = self.route(c, method, path, q)
                finally:
                    c.close()
            if result is not None:
                self.send(200, result)
        except ApiError as e:
            self.send(e.status, {"error": e.message})
        except (sqlite3.IntegrityError,) as e:
            self.send(409, {"error": "That clashes with an existing record (" + str(e).split(":")[-1].strip() + ")"})
        except (KeyError, ValueError, TypeError) as e:
            self.send(400, {"error": f"Bad request: {e}"})

    # ------------------------------------------------------------------ routes
    def route(self, c, method, path, q):
        # ---- public: first-run setup, login, worker check-in
        if path == "/api/status" and method == "GET":
            s = settings(c)
            return {"setup_needed": not c.execute("SELECT 1 FROM users LIMIT 1").fetchone(), "company": s["company_name"]}
        if path == "/api/setup" and method == "POST":
            need(not c.execute("SELECT 1 FROM users LIMIT 1").fetchone(), 403, "Already set up")
            b = self.body()
            need(len(b.get("password", "")) >= 8, 400, "Password needs at least 8 characters")
            c.execute("INSERT INTO users (email, name, role, pw_hash, created) VALUES (?,?,?,?,?)",
                      (clean(b["email"]).lower(), clean(b["name"]), "admin", hash_secret(b["password"]), now_ist().isoformat()))
            c.execute("INSERT OR REPLACE INTO settings VALUES ('company_name', ?)", (json.dumps(clean(b.get("company") or "My Company")),))
            return self.login(c, b["email"], b["password"])
        if path == "/api/login" and method == "POST":
            b = self.body()
            return self.login(c, b.get("email", ""), b.get("password", ""))
        if path == "/api/public/site" and method == "GET":
            site = c.execute("SELECT name, client, address, radius_m, lat IS NOT NULL AS fenced FROM sites WHERE qr_token = ? AND active = 1",
                             (q.get("t", ""),)).fetchone()
            need(site, 404, "This QR code isn't active. Ask your supervisor.")
            return {**dict(site), "company": settings(c)["company_name"]}
        if path == "/api/public/checkin" and method == "POST":
            return self.public_checkin(c, self.body())

        user = self.current_user(c)
        need(user, 401, "Please log in")
        admin = user["role"] == "admin"

        def site_ok(site_id):
            need(admin or int(site_id) in user["sites"], 403, "You don't have access to that site")

        if path == "/api/logout" and method == "POST":
            m = re.search(r"sm_session=([A-Za-z0-9_\-]+)", self.headers.get("Cookie", ""))
            c.execute("DELETE FROM sessions WHERE token = ?", (m.group(1) if m else "",))
            self.send(200, {"ok": True}, headers={"Set-Cookie": "sm_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
            return None
        if path == "/api/me" and method == "GET":
            return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"], "sites": user["sites"],
                    "company": settings(c)["company_name"]}

        # ---- settings and users (admin)
        if path == "/api/settings":
            if method == "GET":
                return settings(c)
            need(admin, 403, "Admins only")
            b = self.body()
            for k, v in b.items():
                if k in payroll.DEFAULT_SETTINGS:
                    default = payroll.DEFAULT_SETTINGS[k]
                    v = clean(v, 300) if isinstance(default, str) else float(v) if isinstance(default, float) else int(float(v))
                    c.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (k, json.dumps(v)))
            audit(c, user["id"], "settings", b)
            return settings(c)
        if path == "/api/users":
            need(admin, 403, "Admins only")
            if method == "GET":
                us = rows(c.execute("SELECT id, email, name, role, active FROM users ORDER BY name"))
                for x in us:
                    x["sites"] = [r["site_id"] for r in c.execute("SELECT site_id FROM user_sites WHERE user_id = ?", (x["id"],))]
                return us
            b = self.body()
            need(len(b.get("password", "")) >= 8, 400, "Password needs at least 8 characters")
            need(b.get("role") in ("admin", "supervisor"), 400, "Role must be admin or supervisor")
            cur = c.execute("INSERT INTO users (email, name, role, pw_hash, created) VALUES (?,?,?,?,?)",
                            (clean(b["email"]).lower(), clean(b["name"]), b["role"], hash_secret(b["password"]), now_ist().isoformat()))
            for sid in b.get("sites", []):
                c.execute("INSERT INTO user_sites VALUES (?, ?)", (cur.lastrowid, int(sid)))
            audit(c, user["id"], "user.add", {"email": b["email"], "role": b["role"]})
            return {"id": cur.lastrowid}
        m = re.fullmatch(r"/api/users/(\d+)", path)
        if m and method == "PUT":
            need(admin, 403, "Admins only")
            uid, b = int(m.group(1)), self.body()
            if "active" in b:
                need(uid != user["id"] or b["active"], 400, "You can't deactivate yourself")
                c.execute("UPDATE users SET active = ? WHERE id = ?", (1 if b["active"] else 0, uid))
                if not b["active"]:
                    c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
            if b.get("password"):
                need(len(b["password"]) >= 8, 400, "Password needs at least 8 characters")
                c.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_secret(b["password"]), uid))
            if "sites" in b:
                c.execute("DELETE FROM user_sites WHERE user_id = ?", (uid,))
                for sid in b["sites"]:
                    c.execute("INSERT INTO user_sites VALUES (?, ?)", (uid, int(sid)))
            audit(c, user["id"], "user.edit", {"id": uid, **{k: v for k, v in b.items() if k != "password"}})
            return {"ok": True}

        # ---- sites
        if path == "/api/sites":
            if method == "GET":
                ss = rows(c.execute("SELECT * FROM sites ORDER BY active DESC, name"))
                return [x for x in ss if admin or x["id"] in user["sites"]]
            need(admin, 403, "Admins only")
            b = self.body()
            cur = c.execute("INSERT INTO sites (name, client, address, lat, lng, radius_m, qr_token, bill_rate, bill_ot_rate, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (clean(b["name"]), clean(b.get("client")), clean(b.get("address"), 300), b.get("lat") or None, b.get("lng") or None,
                             int(b.get("radius_m") or 200), secrets.token_urlsafe(12), float(b.get("bill_rate") or 0),
                             float(b.get("bill_ot_rate") or 0), now_ist().isoformat()))
            audit(c, user["id"], "site.add", b)
            return {"id": cur.lastrowid}
        m = re.fullmatch(r"/api/sites/(\d+)(/rotate-qr|/qr\.svg)?", path)
        if m:
            sid = int(m.group(1))
            site_ok(sid)
            site = c.execute("SELECT * FROM sites WHERE id = ?", (sid,)).fetchone()
            need(site, 404, "No such site")
            if m.group(2) == "/qr.svg" and method == "GET":
                base = q.get("base") or f"{'https' if self.is_https() else 'http'}://{self.headers.get('Host', 'localhost')}"
                need(re.fullmatch(r"https?://[A-Za-z0-9.\-:\[\]]+", base), 400, "bad base")
                self.send(200, qr.svg(f"{base}/c/{site['qr_token']}"), "image/svg+xml")
                return None
            need(admin, 403, "Admins only")
            if m.group(2) == "/rotate-qr" and method == "POST":
                c.execute("UPDATE sites SET qr_token = ? WHERE id = ?", (secrets.token_urlsafe(12), sid))
                audit(c, user["id"], "site.rotate_qr", {"id": sid})
                return {"ok": True}
            if method == "PUT":
                b = self.body()
                fields = {"name": clean(b.get("name", site["name"])), "client": clean(b.get("client", site["client"])),
                          "address": clean(b.get("address", site["address"]), 300),
                          "lat": b.get("lat", site["lat"]) or None, "lng": b.get("lng", site["lng"]) or None,
                          "radius_m": int(b.get("radius_m", site["radius_m"]) or 200),
                          "bill_rate": float(b.get("bill_rate", site["bill_rate"]) or 0),
                          "bill_ot_rate": float(b.get("bill_ot_rate", site["bill_ot_rate"]) or 0),
                          "active": 1 if b.get("active", site["active"]) else 0}
                c.execute("UPDATE sites SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ?", (*fields.values(), sid))
                audit(c, user["id"], "site.edit", {"id": sid, **b})
                return {"ok": True}

        # ---- workers
        if path == "/api/workers":
            if method == "GET":
                ws = rows(c.execute("SELECT * FROM workers ORDER BY active DESC, code"))
                for w in ws:
                    w["has_pin"] = bool(w.pop("pin_hash"))
                    if not admin:                 # supervisors don't see bank or ID details
                        for k in ("bank_account", "ifsc", "uan", "esic_no", "aadhaar_last4", "rate"):
                            w.pop(k, None)
                return ws
            need(admin, 403, "Admins only")
            return {"id": self.save_worker(c, None, self.body(), user)}
        if path == "/api/workers/import" and method == "POST":
            need(admin, 403, "Admins only")
            return self.import_workers(c, self.body().get("csv", ""), user)
        m = re.fullmatch(r"/api/workers/(\d+)", path)
        if m and method == "PUT":
            need(admin, 403, "Admins only")
            self.save_worker(c, int(m.group(1)), self.body(), user)
            return {"ok": True}

        # ---- attendance
        if path == "/api/dashboard" and method == "GET":
            day = q.get("date") or today_ist()
            s = settings(c)
            out = []
            for site in rows(c.execute("SELECT * FROM sites WHERE active = 1 ORDER BY name")):
                if not admin and site["id"] not in user["sites"]:
                    continue
                recs = rows(c.execute("SELECT a.*, w.name, w.code, w.rate, w.wage_type FROM attendance a JOIN workers w ON w.id = a.worker_id WHERE a.site_id = ? AND a.day = ?",
                                      (site["id"], day)))
                counted = [r for r in recs if r["review"] in ("ok", "approved") and r["status"] != "A"]
                cost = sum((r["rate"] if r["wage_type"] == "daily" else r["rate"] / s["month_days"]) for r in counted)
                out.append({"site_id": site["id"], "site": site["name"], "present": len(counted),
                            "on_site_now": len([r for r in counted if r["in_time"] and not r["out_time"]]),
                            "flagged": len([r for r in recs if r["review"] == "flagged"]), "wage_estimate": round(cost)})
            flagged = rows(c.execute("""SELECT a.id, a.day, a.in_time, a.in_dist, a.flag_reason, a.selfie IS NOT NULL AS has_selfie,
                                        w.name, w.code, s.name AS site, a.site_id FROM attendance a JOIN workers w ON w.id = a.worker_id
                                        JOIN sites s ON s.id = a.site_id WHERE a.review = 'flagged' ORDER BY a.day DESC, a.in_time DESC LIMIT 200"""))
            flagged = [f for f in flagged if admin or f["site_id"] in user["sites"]]
            total_workers = c.execute("SELECT COUNT(*) FROM workers WHERE active = 1").fetchone()[0]
            return {"date": day, "sites": out, "flagged": flagged, "active_workers": total_workers}
        if path == "/api/attendance" and method == "GET":
            day = q.get("date") or today_ist()
            recs = rows(c.execute("""SELECT a.*, w.name, w.code, s.name AS site FROM attendance a JOIN workers w ON w.id = a.worker_id
                                     JOIN sites s ON s.id = a.site_id WHERE a.day = ? ORDER BY s.name, w.code""", (day,)))
            return [r for r in recs if admin or r["site_id"] in user["sites"]]
        if path == "/api/register" and method == "GET":
            month = q["month"]
            start, end, dim = payroll.month_bounds(month)
            s = settings(c)
            recs = rows(c.execute("SELECT * FROM attendance WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat())))
            grid = {}
            for r in recs:
                if q.get("site") and int(q["site"]) != r["site_id"]:
                    continue
                if not admin and r["site_id"] not in user["sites"]:
                    continue
                d, o = payroll.day_credit(r, s) if r["review"] in ("ok", "approved") else (0, 0)
                mark = "F" if r["review"] == "flagged" else "X" if r["review"] == "rejected" else ("P" if d == 1 else "H" if d == 0.5 else "A")
                grid.setdefault(str(r["worker_id"]), {})[r["day"][-2:]] = {"id": r["id"], "mark": mark, "ot": o}
            ws = rows(c.execute("SELECT id, code, name, designation, site_id FROM workers WHERE active = 1 OR id IN (SELECT worker_id FROM attendance WHERE day BETWEEN ? AND ?) ORDER BY code",
                                (start.isoformat(), end.isoformat())))
            if q.get("site"):
                ws = [w for w in ws if str(w["id"]) in grid or w["site_id"] == int(q["site"])]
            return {"month": month, "days": dim, "workers": ws, "grid": grid}
        m = re.fullmatch(r"/api/attendance/(\d+)(/selfie)?", path)
        if m:
            rec = c.execute("SELECT * FROM attendance WHERE id = ?", (int(m.group(1)),)).fetchone()
            need(rec, 404, "No such record")
            site_ok(rec["site_id"])
            if m.group(2) and method == "GET":
                need(rec["selfie"], 404, "No selfie")
                path_ = os.path.join(DATA, "selfies", rec["selfie"])
                need(os.path.isfile(path_) and os.path.realpath(path_).startswith(os.path.realpath(os.path.join(DATA, "selfies"))), 404, "No selfie")
                with open(path_, "rb") as f:
                    self.send(200, f.read(), "image/jpeg")
                return None
            if method == "PUT":
                b = self.body()
                upd = {}
                if b.get("review") in ("approved", "rejected", "ok"):
                    upd["review"] = b["review"]
                if b.get("status") in ("P", "H", "A"):
                    upd["status"], upd["status_manual"] = b["status"], 1
                if "ot_override" in b:
                    upd["ot_override"] = None if b["ot_override"] in (None, "") else float(b["ot_override"])
                need(upd, 400, "Nothing to change")
                c.execute("UPDATE attendance SET " + ", ".join(f"{k} = ?" for k in upd) + " WHERE id = ?", (*upd.values(), rec["id"]))
                audit(c, user["id"], "attendance.edit", {"id": rec["id"], **upd})
                return {"ok": True}
        if path == "/api/attendance/mark" and method == "POST":
            # a supervisor or admin marks workers in/out (workers without smartphones), or corrects a day
            b = self.body()
            site_ok(b["site_id"])
            day = b.get("date") or today_ist()
            need(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day), 400, "bad date")
            done = 0
            for wid in b.get("worker_ids", []):
                res = self.mark(c, int(wid), int(b["site_id"]), day, b.get("action", "in"), "supervisor",
                                b.get("lat"), b.get("lng"), None, user["id"], status=b.get("status"))
                done += 1 if res else 0
            audit(c, user["id"], "attendance.mark", {k: b.get(k) for k in ("site_id", "date", "action", "status", "worker_ids")})
            return {"marked": done}

        # ---- advances
        if path == "/api/advances":
            need(admin, 403, "Admins only")
            if method == "GET":
                start, end, _ = payroll.month_bounds(q["month"])
                return rows(c.execute("""SELECT a.*, w.name, w.code FROM advances a JOIN workers w ON w.id = a.worker_id
                                         WHERE a.day BETWEEN ? AND ? ORDER BY a.day""", (start.isoformat(), end.isoformat())))
            b = self.body()
            need(float(b["amount"]) > 0, 400, "Amount must be more than 0")
            cur = c.execute("INSERT INTO advances (worker_id, day, amount, note, created_by) VALUES (?,?,?,?,?)",
                            (int(b["worker_id"]), b.get("date") or today_ist(), float(b["amount"]), clean(b.get("note")), user["id"]))
            audit(c, user["id"], "advance.add", b)
            return {"id": cur.lastrowid}
        m = re.fullmatch(r"/api/advances/(\d+)", path)
        if m and method == "DELETE":
            need(admin, 403, "Admins only")
            c.execute("DELETE FROM advances WHERE id = ?", (int(m.group(1)),))
            audit(c, user["id"], "advance.delete", {"id": int(m.group(1))})
            return {"ok": True}

        # ---- payroll and reports (admin)
        m = re.fullmatch(r"/api/payroll/(\d{4}-\d{2})(/run|/finalize|/reopen|/bank\.csv|/ecr\.txt|/form17\.csv|/slips)?", path)
        if m:
            need(admin, 403, "Admins only")
            month, action = m.group(1), m.group(2)
            s = settings(c)
            run = c.execute("SELECT * FROM payroll_runs WHERE month = ?", (month,)).fetchone()
            if action == "/run" and method == "POST":
                need(not run or run["status"] != "final", 409, "This month is finalized; reopen it first")
                lines = month_lines(c, month, s)
                c.execute("INSERT OR REPLACE INTO payroll_runs (month, status, lines, created) VALUES (?, 'draft', ?, ?)",
                          (month, json.dumps(lines), now_ist().isoformat()))
                audit(c, user["id"], "payroll.run", {"month": month, "workers": len(lines)})
                return {"month": month, "status": "draft", "lines": lines, "totals": totals(lines)}
            if action in ("/finalize", "/reopen") and method == "POST":
                need(run, 404, "Run the payroll first")
                c.execute("UPDATE payroll_runs SET status = ?, finalized = ? WHERE month = ?",
                          ("final" if action == "/finalize" else "draft", now_ist().isoformat() if action == "/finalize" else None, month))
                audit(c, user["id"], "payroll" + action.replace("/", "."), {"month": month})
                return {"ok": True}
            if method == "GET" and not action:
                if not run:
                    return {"month": month, "status": "none", "lines": [], "totals": totals([])}
                lines = json.loads(run["lines"])
                return {"month": month, "status": run["status"], "lines": lines, "totals": totals(lines), "created": run["created"]}
            need(run, 404, "Run the payroll first")
            lines = json.loads(run["lines"])
            if action == "/bank.csv":
                return self.download(payroll.bank_csv(lines, month), f"bank-neft-{month}.csv")
            if action == "/ecr.txt":
                return self.download(payroll.ecr_text(lines), f"epfo-ecr-{month}.txt", "text/plain")
            if action == "/form17.csv":
                return self.download(payroll.form17_csv(lines), f"form-xvii-{month}.csv")
            if action == "/slips":
                lang = q.get("lang") or s["slip_language"]
                return {"month": month, "company": s["company_name"], "address": s["company_address"],
                        "slips": [dict(l, text=payroll.slip_text(l, month, s["company_name"], lang)) for l in lines]}
        if path == "/api/reports/sites" and method == "GET":
            need(admin, 403, "Admins only")
            s = settings(c)
            return {"month": q["month"], "gst_rate": s["gst_rate"], "sites": site_report(c, q["month"], s)}
        if path == "/api/audit" and method == "GET":
            need(admin, 403, "Admins only")
            return rows(c.execute("SELECT a.*, u.name AS user FROM audit a LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT 300"))
        raise ApiError(404, "Not found")

    # ------------------------------------------------------------------ actions
    def login(self, c, email, password):
        u = c.execute("SELECT * FROM users WHERE email = ? AND active = 1", (clean(email).lower(),)).fetchone()
        if not u or not check_secret(password, u["pw_hash"]):
            time.sleep(0.5)
            raise ApiError(401, "Wrong email or password")
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token, u["id"], (now_ist() + timedelta(days=SESSION_DAYS)).isoformat()))
        c.execute("DELETE FROM sessions WHERE expires < ?", (now_ist().isoformat(),))
        cookie = f"sm_session={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Lax" + ("; Secure" if self.is_https() else "")
        self.send(200, {"ok": True, "name": u["name"], "role": u["role"]}, headers={"Set-Cookie": cookie})
        return None

    def save_worker(self, c, wid, b, user):
        need(clean(b.get("name")), 400, "Name is required")
        need(b.get("wage_type", "daily") in ("daily", "monthly"), 400, "Wage type must be daily or monthly")
        ifsc = clean(b.get("ifsc"), 11).upper()
        need(not ifsc or re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", ifsc), 400, "IFSC should look like SBIN0001234")
        uan = re.sub(r"\D", "", str(b.get("uan") or ""))
        need(not uan or len(uan) == 12, 400, "UAN has 12 digits")
        aad = re.sub(r"\D", "", str(b.get("aadhaar_last4") or ""))[-4:]
        f = {"code": clean(b.get("code"), 20) or None, "name": clean(b["name"]), "phone": re.sub(r"[^\d+]", "", str(b.get("phone") or ""))[:15],
             "designation": clean(b.get("designation"), 60), "site_id": int(b["site_id"]) if b.get("site_id") else None,
             "wage_type": b.get("wage_type", "daily"), "rate": float(b.get("rate") or 0), "uan": uan,
             "esic_no": clean(b.get("esic_no"), 17), "pf_member": 1 if b.get("pf_member", True) else 0,
             "esic_member": 1 if b.get("esic_member", True) else 0, "bank_account": re.sub(r"\s", "", clean(b.get("bank_account"), 20)),
             "ifsc": ifsc, "aadhaar_last4": aad, "joined": clean(b.get("joined"), 10), "active": 1 if b.get("active", True) else 0}
        if wid is None:
            if not f["code"]:
                n = c.execute("SELECT COUNT(*) FROM workers").fetchone()[0] + 1
                f["code"] = f"W{n:04d}"
                while c.execute("SELECT 1 FROM workers WHERE code = ?", (f["code"],)).fetchone():
                    n += 1
                    f["code"] = f"W{n:04d}"
            cur = c.execute("INSERT INTO workers (" + ", ".join(f) + ", created) VALUES (" + ",".join("?" * len(f)) + ", ?)",
                            (*f.values(), now_ist().isoformat()))
            wid = cur.lastrowid
        else:
            if not f["code"]:
                f.pop("code")
            c.execute("UPDATE workers SET " + ", ".join(f"{k} = ?" for k in f) + " WHERE id = ?", (*f.values(), wid))
        if b.get("pin"):
            need(re.fullmatch(r"\d{4}", str(b["pin"])), 400, "PIN must be 4 digits")
            c.execute("UPDATE workers SET pin_hash = ? WHERE id = ?", (hash_secret(str(b["pin"])), wid))
        audit(c, user["id"], "worker.save", {"id": wid, "code": f.get("code"), "name": f["name"]})
        return wid

    def import_workers(self, c, text, user):
        """CSV from Excel: code, name, phone, designation, site, wage_type, rate, uan, esic_no, bank_account, ifsc, pin"""
        reader = csv.DictReader(io.StringIO(text.strip()))
        need(reader.fieldnames and "name" in [h.strip().lower() for h in reader.fieldnames], 400, "The first row must be headings and include 'name'")
        sites = {r["name"].strip().lower(): r["id"] for r in c.execute("SELECT id, name FROM sites")}
        added, updated, errors = 0, 0, []
        for i, raw in enumerate(reader, 2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            try:
                if row.get("site"):
                    row["site_id"] = sites.get(row["site"].lower())
                    need(row["site_id"], 400, f"unknown site '{row['site']}'")
                row["pf_member"] = row.get("pf_member", "yes").lower() not in ("no", "0", "n")
                row["esic_member"] = row.get("esic_member", "yes").lower() not in ("no", "0", "n")
                existing = c.execute("SELECT id FROM workers WHERE code = ?", (row.get("code") or "",)).fetchone() if row.get("code") else None
                self.save_worker(c, existing["id"] if existing else None, row, user)
                updated += 1 if existing else 0
                added += 0 if existing else 1
            except ApiError as e:
                errors.append(f"Row {i}: {e.message}")
        return {"added": added, "updated": updated, "errors": errors[:50]}

    def mark(self, c, wid, site_id, day, action, method, lat, lng, selfie, user_id, dist=None, flag="", status=None):
        """Record a check-in or check-out (one attendance row per worker per day)."""
        t = now_ist().isoformat(timespec="seconds")
        rec = c.execute("SELECT * FROM attendance WHERE worker_id = ? AND day = ?", (wid, day)).fetchone()
        review = "flagged" if flag else "ok"
        if status in ("P", "H", "A"):
            if rec:
                c.execute("UPDATE attendance SET status = ?, status_manual = 1, review = CASE WHEN review = 'flagged' THEN 'approved' ELSE review END WHERE id = ?", (status, rec["id"]))
            else:
                c.execute("INSERT INTO attendance (worker_id, site_id, day, in_time, method, status, status_manual, marked_by) VALUES (?,?,?,?,?,?,1,?)",
                          (wid, site_id, day, t if day == today_ist() else None, method, status, user_id))
            return "status"
        if not rec:
            c.execute("""INSERT INTO attendance (worker_id, site_id, day, in_time, in_lat, in_lng, in_dist, method, selfie, review, flag_reason, marked_by)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (wid, site_id, day, t, lat, lng, dist, method, selfie, review, flag, user_id))
            return "in"
        if action == "in" and method == "supervisor":
            return None
        gap = settings(c)["checkin_gap_minutes"]
        if rec["in_time"] and (now_ist() - datetime.fromisoformat(rec["in_time"])).total_seconds() < gap * 60:
            return "repeat"
        if rec["out_time"] and method != "supervisor":
            return "done"
        upd_review = "flagged" if (flag or rec["review"] == "flagged") else rec["review"]
        reason = "; ".join(x for x in [rec["flag_reason"], ("OUT: " + flag) if flag else ""] if x)
        c.execute("UPDATE attendance SET out_time = ?, out_lat = ?, out_lng = ?, out_dist = ?, review = ?, flag_reason = ? WHERE id = ?",
                  (t, lat, lng, dist, upd_review, reason, rec["id"]))
        return "out"

    def public_checkin(self, c, b):
        site = c.execute("SELECT * FROM sites WHERE qr_token = ? AND active = 1", (str(b.get("token", "")),)).fetchone()
        need(site, 404, "This QR code isn't active. Ask your supervisor.")
        who = clean(b.get("worker"), 20)
        digits = re.sub(r"\D", "", who)
        w = c.execute("SELECT * FROM workers WHERE active = 1 AND (UPPER(code) = UPPER(?) OR (? != '' AND phone LIKE ?))",
                      (who, digits, f"%{digits[-10:]}" if len(digits) >= 10 else "")).fetchone()
        need(w, 404, "Worker not found. Check your worker code or phone number.")
        key = w["id"]
        fails = [t for t in _pin_fails.get(key, []) if time.time() - t < 900]
        need(len(fails) < PIN_TRIES, 429, "Too many wrong PINs. Wait 15 minutes or ask your supervisor.")
        need(w["pin_hash"], 403, "No PIN set for you yet. Ask your supervisor.")
        if not check_secret(str(b.get("pin", "")), w["pin_hash"]):
            _pin_fails[key] = fails + [time.time()]
            raise ApiError(403, "Wrong PIN")
        _pin_fails.pop(key, None)
        flag = []
        dist = None
        lat, lng = b.get("lat"), b.get("lng")
        if site["lat"] is not None and site["lng"] is not None:
            if lat is None or lng is None:
                flag.append("no GPS")
            else:
                dist = round(haversine_m(float(lat), float(lng), site["lat"], site["lng"]))
                acc = float(b.get("accuracy") or 0)
                if dist > site["radius_m"] + min(acc, 100):
                    flag.append(f"{dist / 1000:.1f} km from site" if dist >= 1000 else f"{dist} m from site")
        selfie = None
        img = str(b.get("selfie") or "")
        if img.startswith("data:image/jpeg;base64,"):
            raw = base64.b64decode(img.split(",", 1)[1], validate=True)
            need(len(raw) < 1_500_000 and raw[:3] == b"\xff\xd8\xff", 400, "Selfie must be a JPEG photo")
            month_dir = now_ist().strftime("%Y-%m")
            os.makedirs(os.path.join(DATA, "selfies", month_dir), exist_ok=True)
            selfie = f"{month_dir}/{w['id']}-{secrets.token_hex(6)}.jpg"
            with open(os.path.join(DATA, "selfies", selfie), "wb") as f:
                f.write(raw)
        else:
            flag.append("no selfie")
        day = today_ist()
        res = self.mark(c, w["id"], site["id"], day, "auto", "qr", lat, lng, selfie, None, dist=dist, flag=", ".join(flag))
        msg = {"in": "Check-in accepted", "out": "Check-out recorded", "repeat": "Already checked in a moment ago",
               "done": "You have already checked out today"}[res]
        return {"result": res, "message": msg, "name": w["name"], "site": site["name"], "time": now_ist().strftime("%H:%M"),
                "distance_m": dist, "flagged": bool(flag) and res in ("in", "out"), "flag_reason": ", ".join(flag)}


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_nolock = _NoLock()


def totals(lines):
    keys = ["days", "ot_hours", "basic", "ot_pay", "gross", "pf_employee", "eps_employer", "epf_employer",
            "esic_employee", "esic_employer", "advance", "net"]
    t = {k: sum(l[k] for l in lines) for k in keys}
    t["workers"] = len(lines)
    t["company_cost"] = t["gross"] + t["eps_employer"] + t["epf_employer"] + t["esic_employer"]
    return t


def main():
    global DATA
    ap = argparse.ArgumentParser(description="Site Manager server")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data", default=DATA)
    a = ap.parse_args()
    DATA = a.data
    init_db()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"Site Manager on http://{a.host}:{a.port}  (data: {DATA})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
