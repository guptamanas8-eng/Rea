#!/usr/bin/env python3
"""Phone-side TV remote and live sketch board for tv.sh.

Runs on the phone (Termux) or computer that already has the TV connected
over adb. Open http://127.0.0.1:8765 on that same device for the remote and
the sketch pad. The TV's Sketch Board app reads the drawing from
http://<this-device-ip>:8765/board. Only this device can press keys or
draw; other devices on the network can only view the board.
"""
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import qr
from games import Games

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("TVCTL_STATE", os.path.join(os.path.dirname(HERE), "state"))
PORT = int(os.environ.get("TVREMOTE_PORT", "8765"))
LOG = os.path.join(STATE, "commands.log")  # same log ./tv.sh monitor follows
SKETCH_FILE = os.path.join(STATE, "sketch.json")
YT = "com.google.android.youtube.tv"
BOARD_PKG = "com.guptamanas8.sketchboard"

KEYS = {
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER", "BACK", "HOME",
    "MENU", "SETTINGS", "SEARCH", "VOLUME_UP", "VOLUME_DOWN", "VOLUME_MUTE",
    "MEDIA_PLAY_PAUSE", "MEDIA_NEXT", "MEDIA_PREVIOUS", "MEDIA_FAST_FORWARD",
    "MEDIA_REWIND", "POWER", "WAKEUP", "SLEEP", "TV_INPUT", "CHANNEL_UP",
    "CHANNEL_DOWN", "DEL", "ENTER", "CAPTIONS", "GUIDE", "INFO", "APP_SWITCH",
}
# never force-stopped from the remote
NO_CLOSE = {"com.google.android.apps.tv.launcherx", "com.android.systemui", "android"}
FRIENDLY = {
    YT: "YouTube", "com.netflix.ninja": "Netflix",
    "com.amazon.amazonvideo.livingroom": "Prime Video", "in.startv.hotstar": "Hotstar",
    BOARD_PKG: "Sketch Board", "com.phlox.tvwebbrowser": "TV Bro",
    "com.android.vending": "Play Store", "com.android.tv.settings": "Settings",
    "com.google.android.apps.tv.launcherx": "Home", "com.tcl.tv": "Live TV",
}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PKG_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+$")


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def find_adb():
    for c in (shutil.which("adb"), os.path.join(STATE, "platform-tools", "adb")):
        if c and os.access(c, os.X_OK):
            return c
    sys.exit("adb not found. In Termux: pkg install android-tools")


def serial():
    s = os.environ.get("TV")
    if not s:
        try:
            with open(os.path.join(STATE, "device")) as f:
                s = f.read().strip()
        except OSError:
            pass
    if not s:
        sys.exit("No TV yet. Run: bash tv.sh connect <tv-ip>")
    return s


ADB = find_adb()
SERIAL = serial()
_log_lock = threading.Lock()


OFFLINE_RE = re.compile(r"not found|offline|no devices|unauthorized|closed|cannot connect|Connection refused")


def adb(*args, timeout=20, reconnect=True):
    """Run adb against the TV, logged like tv.sh so the monitor shows it.

    When the TV has dropped off (switched off, Wi-Fi reconnect), reconnect once and retry.
    """
    cid = f"{os.getpid()}-{time.monotonic_ns() % 1000000}"
    line = " ".join(args)
    with _log_lock, open(LOG, "a") as f:
        f.write(f"START {cid} {ts()} adb {line}\n")
    try:
        r = subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        out, rc = (r.stdout + r.stderr).replace("\r", "").strip(), r.returncode
    except subprocess.TimeoutExpired:
        out, rc = "timed out", 124
    with _log_lock, open(LOG, "a") as f:
        f.write(f"END {cid} {ts()} rc={rc}\n")
    if rc != 0 and reconnect and args[0] != "connect" and OFFLINE_RE.search(out):
        if tv_reconnect():
            return adb(*args, timeout=timeout, reconnect=False)
        out = "The TV isn't reachable. Turn it on (and check it's on Wi-Fi), then try again."
    return rc, out


def tv_reconnect():
    subprocess.run([ADB, "disconnect", SERIAL], capture_output=True, timeout=10)
    try:
        r = subprocess.run([ADB, "connect", SERIAL], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    if "connected to" not in r.stdout:
        return False
    st = subprocess.run([ADB, "-s", SERIAL, "get-state"], capture_output=True, text=True, timeout=10)
    return st.stdout.strip() == "device"


def shell(cmd):
    return adb("shell", cmd)


def launch(pkg):
    for cat in ("LEANBACK_LAUNCHER", "LAUNCHER"):
        _, out = shell("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                       f"-c android.intent.category.{cat} {pkg}")
        comp = out.splitlines()[-1] if out else ""
        if "/" in comp:
            rc, out = shell(f"am start -n {shlex.quote(comp)}")
            return rc == 0 and "Error" not in out, out
    return False, f"{pkg} has no TV launcher screen"


GENERIC = {"android", "app", "apps", "tv", "mobile", "client", "main", "ninja", "livingroom", "overseas", "international"}


def app_name(pkg):
    """Friendly name, else the most telling part of the package name (com.instagram.android -> Instagram)."""
    if pkg in FRIENDLY:
        return FRIENDLY[pkg]
    parts = [p for p in pkg.split(".")[1:] if p.lower() not in GENERIC] or pkg.split(".")
    return parts[-1].replace("_", " ")[:1].upper() + parts[-1].replace("_", " ")[1:]


def launchable(category):
    _, out = shell("cmd package query-activities --brief -a android.intent.action.MAIN "
                   f"-c android.intent.category.{category}")
    return {l.split("/")[0].strip() for l in out.splitlines() if "/" in l} - {""}


def tv_apps():
    """TV apps, plus phone apps installed on the TV (the Google TV home screen hides those)."""
    tv = launchable("LEANBACK_LAUNCHER")
    phone = launchable("LAUNCHER") - tv
    apps = [{"pkg": p, "name": app_name(p), "kind": "tv" if p in tv else "phone"}
            for p in tv | phone if PKG_RE.match(p)]
    return sorted(apps, key=lambda a: (a["kind"] != "tv", a["pkg"] not in FRIENDLY, a["name"].lower()))


def current_app():
    _, out = shell("dumpsys window")
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", out) or \
        re.search(r"mFocusedApp=.*? ([\w.]+)/", out)
    return m.group(1) if m else None


_screen_size = None


def screen_size():
    """Display size in the coordinates `input tap` uses."""
    global _screen_size
    if _screen_size is None:
        _, out = shell("wm size")
        sizes = re.findall(r"(\d+)x(\d+)", out)
        if not sizes:
            raise ValueError("couldn't read the TV's screen size")
        _screen_size = tuple(map(int, sizes[-1]))  # override size when set, else physical
    return _screen_size


def screencap():
    """One PNG of the TV screen (video areas come out black: DRM)."""
    cid = f"{os.getpid()}-{time.monotonic_ns() % 1000000}"
    with _log_lock, open(LOG, "a") as f:
        f.write(f"START {cid} {ts()} adb exec-out screencap -p\n")
    try:
        r = subprocess.run([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
        data, rc = r.stdout, r.returncode
    except subprocess.TimeoutExpired:
        data, rc = b"", 124
    with _log_lock, open(LOG, "a") as f:
        f.write(f"END {cid} {ts()} rc={rc}\n")
    return data if data.startswith(b"\x89PNG") else None


def to_px(x, y):
    w, h = screen_size()
    x, y = float(x), float(y)
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("coordinates must be between 0 and 1")
    return round(x * (w - 1)), round(y * (h - 1))


def local_ip():
    """The address the TV can reach this device on."""
    host = SERIAL.rsplit(":", 1)[0]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 5555))
        return s.getsockname()[0]
    finally:
        s.close()


class Sketch:
    """Strokes in 0..1 coordinates, a note line and a background colour, plus an event feed."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.events = []  # (seq, event)
        self.state = {"background": "#ffffff", "note": "", "strokes": []}
        try:
            with open(SKETCH_FILE) as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass
        self._save_timer = None

    def snapshot(self):
        with self.cond:
            return self.seq, json.loads(json.dumps(self.state))

    def _publish(self, ev):
        self.seq += 1
        self.events.append((self.seq, ev))
        del self.events[:-1000]
        self.cond.notify_all()
        if self._save_timer is None:
            self._save_timer = threading.Timer(2.0, self._save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _save(self):
        with self.cond:
            self._save_timer = None
            data = json.dumps(self.state)
        tmp = SKETCH_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(data)
        os.replace(tmp, SKETCH_FILE)

    def add_points(self, sid, color, width, points):
        with self.cond:
            stroke = next((s for s in reversed(self.state["strokes"]) if s["id"] == sid), None)
            if stroke is None:
                stroke = {"id": sid, "color": color, "width": width, "points": []}
                self.state["strokes"].append(stroke)
            stroke["points"].extend(points)
            self._publish({"type": "points", "id": sid, "color": stroke["color"],
                           "width": stroke["width"], "points": points})

    def reset_event(self):
        self._publish({"type": "reset", "state": self.state})

    def undo(self):
        with self.cond:
            if self.state["strokes"]:
                self.state["strokes"].pop()
            self.reset_event()

    def clear(self, background=None):
        with self.cond:
            self.state["strokes"] = []
            if background:
                self.state["background"] = background
            self.reset_event()

    def set_note(self, note):
        with self.cond:
            self.state["note"] = note
            self.reset_event()

    def wait(self, after, timeout=15):
        """Events newer than `after`, or None when the client fell too far behind."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after, timeout)
            if self.events and after < self.events[0][0] - 1:
                return None, self.seq
            return [e for s, e in self.events if s > after], self.seq


SKETCH = Sketch()
GAMES = Games()
# what phones other than this one may POST: joining and playing, never TV control
PLAYER_POSTS = {"/api/game/join", "/api/game/leave", "/api/game/guess", "/api/game/answer",
                "/api/game/draw", "/api/game/draw/edit", "/api/game/race/input", "/api/game/race/action"}
# what only the TV (or this phone) may POST: the race engine runs on the TV and reports back
TV_POSTS = {"/api/game/race/hud", "/api/game/race/result"}


def join_url():
    try:
        return f"http://{local_ip()}:{PORT}/play"
    except OSError:
        return None


def show_on_tv(view=None):
    """Open the Sketch Board app on the TV at the board (or the game when view="game")."""
    url = f"http://{local_ip()}:{PORT}/board" + (f"?view={view}" if view else "")
    rc, out = shell(f"am start -n {BOARD_PKG}/.MainActivity --es url {shlex.quote(url)}")
    if "does not exist" in out or "Error" in out:
        return {"ok": False, "out": "Sketch Board isn't installed on the TV. Run: "
                "bash tv.sh install-app guptamanas8-eng/Rea sketchboard"}
    return {"ok": rc == 0, "out": f"Showing {url} on the TV"}


def check_points(raw):
    if not isinstance(raw, list) or not 0 < len(raw) <= 500:
        raise ValueError("points must be a list of 1-500 [x, y] pairs")
    pts = []
    for p in raw:
        x, y = float(p[0]), float(p[1])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("points must be between 0 and 1")
        pts.append([round(x, 4), round(y, 4)])
    return pts


class Handler(BaseHTTPRequestHandler):
    server_version = "tv-remote"

    def log_message(self, fmt, *args):
        pass

    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (
            json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def page(self, name):
        with open(os.path.join(HERE, "web", name), "rb") as f:
            self.send(200, f.read(), "text/html")

    def do_GET(self):
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        if path == "/board":
            return self.page("game_tv.html" if query.get("view") == ["game"] else "board.html")
        if path == "/play":
            return self.page("play.html")
        if path == "/race.js":
            with open(os.path.join(HERE, "web", "race.js"), "rb") as f:
                return self.send(200, f.read(), "text/javascript")
        if path == "/api/game/events":
            return self.game_events((query.get("t") or [None])[0])
        if path == "/api/game/qr.svg":
            url = join_url()
            if not url:
                return self.send(503, {"error": "no network address"})
            return self.send(200, qr.svg(url), "image/svg+xml")
        if path == "/api/sketch":
            return self.send(200, SKETCH.snapshot()[1])
        if path == "/api/sketch/events":
            return self.events()
        if not self.is_local():
            return self.send(403, {"error": "only the phone running the server can use this"})
        if path in ("/", "/index.html"):
            return self.page("index.html")
        if path == "/api/apps":
            return self.send(200, {"apps": tv_apps()})
        if path == "/api/screen":
            png = screencap()
            if png is None:
                return self.send(503, {"error": "The TV isn't reachable."})
            return self.send(200, png, "image/png")
        if path == "/api/current":
            pkg = current_app()
            return self.send(200, {"pkg": pkg, "name": app_name(pkg) if pkg else None})
        if path == "/api/game/host":
            v = GAMES.view(None, join_url())
            v["words"] = "\n".join(GAMES.custom_words)
            v["questions"] = "\n".join(" | ".join(q) for q in GAMES.custom_questions)
            return self.send(200, v)
        if path == "/api/status":
            rc, out = adb("get-state")
            return self.send(200, {"tv": SERIAL, "online": rc == 0 and out == "device"})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        from_tv = self.client_address[0] == SERIAL.rsplit(":", 1)[0]
        if not self.is_local() and path not in PLAYER_POSTS and not (from_tv and path in TV_POSTS):
            return self.send(403, {"error": "only the phone running the server can control the TV"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 200000:
                raise ValueError("request too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            result = self.route(urlparse(self.path).path, body)
        except PermissionError as e:
            return self.send(403, {"ok": False, "error": str(e)})
        except (ValueError, TypeError, KeyError, IndexError) as e:
            return self.send(400, {"ok": False, "error": str(e)})
        if result is None:
            return self.send(404, {"error": "not found"})
        self.send(200 if result.get("ok", True) else 502, result)

    def route(self, path, b):
        if path == "/api/key":
            key = str(b["key"]).upper()
            if key not in KEYS:
                raise ValueError(f"unknown key {key}")
            rc, out = shell(f"input keyevent KEYCODE_{key}")
            return {"ok": rc == 0, "out": out}
        if path == "/api/text":
            text = re.sub(r"[^A-Za-z0-9 .,@_+:/?=&!-]", "", str(b["text"]))[:200]
            if not text:
                raise ValueError("adb can only type plain letters, digits and simple symbols")
            rc, out = shell("input text " + shlex.quote(text.replace(" ", "%s")))
            return {"ok": rc == 0, "out": out}
        if path == "/api/launch":
            pkg = str(b["pkg"])
            if not PKG_RE.match(pkg):
                raise ValueError("bad package name")
            ok, out = launch(pkg)
            return {"ok": ok, "out": out}
        if path == "/api/tap":
            x, y = to_px(b["x"], b["y"])
            rc, out = shell(f"input tap {x} {y}")
            return {"ok": rc == 0, "out": out}
        if path == "/api/swipe":
            x1, y1 = to_px(b["x1"], b["y1"])
            x2, y2 = to_px(b["x2"], b["y2"])
            ms = max(50, min(3000, int(b.get("ms", 300))))
            rc, out = shell(f"input swipe {x1} {y1} {x2} {y2} {ms}")
            return {"ok": rc == 0, "out": out}
        if path == "/api/close":
            pkg = current_app()
            if not pkg or pkg in NO_CLOSE:
                return {"ok": False, "out": "No app is open (the home screen can't be closed)."}
            rc, out = shell(f"am force-stop {pkg}")
            return {"ok": rc == 0, "out": f"Closed {app_name(pkg)}"}
        if path == "/api/youtube":
            q = str(b["q"]).strip()[:150]
            if not q:
                raise ValueError("type what to search for")
            rc, out = shell(f"am start -a android.intent.action.SEARCH -n {YT}/"
                            "com.google.android.apps.youtube.tv.activity.ShellActivity "
                            f"--es query {shlex.quote(q)}")
            return {"ok": rc == 0 and "Error" not in out, "out": out}
        if path == "/api/board/open":
            return show_on_tv()
        if path.startswith("/api/game/"):
            return self.game_route(path[len("/api/game/"):], b)
        if path == "/api/sketch/points":
            sid = str(b["id"])[:40]
            color = str(b["color"])
            if not COLOR_RE.match(color):
                raise ValueError("color must be #rrggbb")
            width = max(1, min(80, int(b["width"])))
            SKETCH.add_points(sid, color, width, check_points(b["points"]))
            return {"ok": True}
        if path == "/api/sketch/undo":
            SKETCH.undo()
            return {"ok": True}
        if path == "/api/sketch/clear":
            bg = b.get("background")
            if bg is not None and not COLOR_RE.match(str(bg)):
                raise ValueError("background must be #rrggbb")
            SKETCH.clear(bg)
            return {"ok": True}
        if path == "/api/sketch/note":
            SKETCH.set_note(str(b.get("note", ""))[:120])
            return {"ok": True}
        return None

    def game_route(self, action, b):
        t = b.get("t")
        if action == "join":
            return {"ok": True, **GAMES.join(b.get("name", ""), b.get("token"))}
        if action == "leave":
            GAMES.leave(t)
            return {"ok": True}
        if action == "guess":
            return GAMES.guess(t, b["text"])
        if action == "answer":
            return GAMES.answer(t, b["choice"])
        if action == "draw":
            color = str(b["color"])
            if not COLOR_RE.match(color):
                raise ValueError("color must be #rrggbb")
            return GAMES.draw(t, str(b["id"])[:40], color, max(1, min(80, int(b["width"]))),
                              check_points(b["points"]))
        if action == "race/input":
            return GAMES.race_input(t, b)
        if action == "race/action":
            return GAMES.race_action(t, b.get("action"))
        if action == "race/hud":
            return GAMES.race_hud(b.get("hud"))
        if action == "race/result":
            return GAMES.race_result(b.get("standings"))
        if action == "draw/edit":
            if b.get("action") not in ("undo", "clear", "skip"):
                raise ValueError("bad action")
            return GAMES.draw_edit(t, b["action"])
        # host only (other phones are stopped by PLAYER_POSTS in do_POST)
        if action == "open":
            GAMES.open(b["mode"], b.get("settings"), b.get("words"), b.get("questions"))
            shown = show_on_tv("game")
            return {"ok": True, "out": "Game open on the TV" if shown["ok"] else shown["out"]}
        if action == "show":
            return show_on_tv("game")
        if action == "start":
            GAMES.start()
            return {"ok": True}
        if action == "skip":
            GAMES.skip()
            return {"ok": True}
        if action == "end":
            GAMES.end()
            return {"ok": True}
        if action == "kick":
            GAMES.kick(str(b["id"]))
            return {"ok": True}
        return None

    def game_events(self, token):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def write(ev):
            self.wfile.write(b": keepalive\n\n" if ev is None else f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()
        try:
            GAMES.stream(token, write, join_url())
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            seq, state = SKETCH.snapshot()
            self.wfile.write(f"data: {json.dumps({'type': 'reset', 'state': state})}\n\n".encode())
            self.wfile.flush()
            while True:
                evs, new_seq = SKETCH.wait(seq)
                if evs is None:
                    new_seq, state = SKETCH.snapshot()
                    evs = [{"type": "reset", "state": state}]
                for ev in evs:
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                if not evs:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                seq = new_seq
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def main():
    os.makedirs(STATE, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    try:
        ip = local_ip()
    except OSError:
        ip = "<this-device-ip>"
    print(f"TV remote for {SERIAL}" + ("" if adb("get-state")[0] == 0 else
          "  (TV not reachable right now; the remote reconnects when it's back)"))
    print(f"  remote and sketch pad: http://127.0.0.1:{PORT}  (open in this phone's browser)")
    print(f"  board for the TV:      http://{ip}:{PORT}/board")
    print(f"  games: players join at http://{ip}:{PORT}/play")
    print("  Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
