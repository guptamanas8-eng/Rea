"""Draw & Guess and Quiz buzzer: the TV shows the game, phones on the same Wi-Fi are the controllers.

One game at a time. The host (the phone running server.py) picks the game and
starts it from the remote's Games tab; players join from /play with a name and
get a secret token. Each connected screen gets its own view over a
Server-Sent Events stream, so the drawer's word never reaches guessers or the TV.
"""
import random
import re
import secrets
import threading
import time
import unicodedata

from games_data import QUESTIONS, WORDS

PLAYER_COLORS = ["#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa", "#00897b",
                 "#d81b60", "#6d4c41", "#3949ab", "#c0ca33"]
MAX_PLAYERS = 10
REVEAL_SECONDS = 6


def _norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if c.isalnum())


def _close(a, b):
    """One letter off (typo-level) for words of 4+ letters."""
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 1:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] == 1


def parse_words(text):
    return [w.strip()[:30] for w in re.split(r"[,\n]", text or "") if w.strip()][:300]


def parse_questions(text):
    """One per line: Question | correct answer | wrong answer | wrong answer | wrong answer."""
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip()[:80] for p in line.split("|") if p.strip()]
        if len(parts) >= 3:
            out.append(tuple(parts[:5]))
    return out[:200]


class Games:
    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.events = []          # (seq, event) for drawing strokes; "state" means re-render views
        self.players = {}         # token -> player
        self.mode = None          # "draw" | "quiz"
        self.phase = "idle"       # idle | lobby | turn | question | reveal | over
        self.settings = {"draw_rounds": 2, "draw_seconds": 80, "quiz_count": 10, "quiz_seconds": 20}
        self.custom_words, self.custom_questions = [], []
        self.feed = []
        self.deadline = None
        self.connections = {}     # token -> open event streams
        threading.Thread(target=self._ticker, daemon=True).start()

    # ------------------------------------------------------------- plumbing
    def _publish(self, ev=None):
        self.seq += 1
        self.events.append((self.seq, ev or {"type": "state"}))
        del self.events[:-2000]
        self.cond.notify_all()

    def _say(self, text, kind="info"):
        self.feed.append({"text": text, "kind": kind})
        del self.feed[:-8]

    def _player(self, token):
        p = self.players.get(str(token or ""))
        if not p:
            raise PermissionError("You're not in the game; join again.")
        return p

    def _seconds_left(self):
        return max(0, round(self.deadline - time.time())) if self.deadline else None

    def _ticker(self):
        while True:
            time.sleep(0.25)
            with self.cond:
                if self.deadline and time.time() >= self.deadline:
                    self.deadline = None
                    self._timeout()
                elif self.phase == "turn":
                    self._maybe_hint()

    def _timeout(self):
        if self.phase == "turn":
            self._end_turn()
        elif self.phase == "question":
            self._end_question()
        elif self.phase == "reveal":
            self._next()

    # -------------------------------------------------------------- players
    def join(self, name, token=None):
        with self.cond:
            if token and token in self.players:
                return {"token": token, "id": self.players[token]["id"]}
            if self.phase == "idle":
                raise ValueError("No game is open yet; ask the host to pick one on the TV.")
            name = re.sub(r"[\x00-\x1f<>]", "", str(name)).strip()[:16]
            if not name:
                raise ValueError("Type your name")
            if len(self.players) >= MAX_PLAYERS:
                raise ValueError("The game is full")
            taken = {p["name"].lower() for p in self.players.values()}
            base, n = name, 2
            while name.lower() in taken:
                name, n = f"{base} {n}", n + 1
            token = secrets.token_urlsafe(16)
            used = {p["color"] for p in self.players.values()}
            color = next((c for c in PLAYER_COLORS if c not in used), PLAYER_COLORS[0])
            self.players[token] = {"id": secrets.token_hex(4), "name": name, "score": 0, "color": color,
                                   "joined": time.time(), "last_guess": 0}
            self._say(f"{name} joined", "join")
            self._publish()
            return {"token": token, "id": self.players[token]["id"]}

    def leave(self, token):
        with self.cond:
            p = self.players.pop(str(token), None)
            if p:
                self._say(f"{p['name']} left", "join")
                self._player_gone(token)
                self._publish()

    def kick(self, pid):
        with self.cond:
            token = next((t for t, p in self.players.items() if p["id"] == pid), None)
            if token:
                self.leave(token)

    def _player_gone(self, token):
        if self.phase == "turn":
            if token == self.drawer:
                self._say("The drawer left; next turn", "info")
                self._end_turn()
            elif self._all_guessed():
                self._end_turn()
        elif self.phase == "question" and self._all_answered():
            self._end_question()

    # ----------------------------------------------------------------- host
    def open(self, mode, settings=None, words=None, questions=None):
        if mode not in ("draw", "quiz"):
            raise ValueError("unknown game")
        with self.cond:
            self.mode, self.phase, self.deadline = mode, "lobby", None
            for k, v in (settings or {}).items():
                if k in self.settings:
                    self.settings[k] = int(v)
            s = self.settings
            s["draw_rounds"] = max(1, min(5, s["draw_rounds"]))
            s["draw_seconds"] = max(30, min(180, s["draw_seconds"]))
            s["quiz_count"] = max(3, min(30, s["quiz_count"]))
            s["quiz_seconds"] = max(5, min(60, s["quiz_seconds"]))
            if words is not None:
                self.custom_words = parse_words(words)
            if questions is not None:
                self.custom_questions = parse_questions(questions)
            for p in self.players.values():
                p["score"] = 0
            self.feed = []
            self._say("Scan the code to join", "info")
            self._publish()

    def start(self):
        with self.cond:
            if self.phase not in ("lobby", "over"):
                raise ValueError("A game is already running")
            need = 2 if self.mode == "draw" else 1
            if len(self.players) < need:
                raise ValueError(f"Need at least {need} players")
            for p in self.players.values():
                p["score"] = 0
            self.feed = []
            if self.mode == "draw":
                order = sorted(self.players, key=lambda t: self.players[t]["joined"])
                self.turns = order * self.settings["draw_rounds"]
                self.turn_i = -1
                pool = self.custom_words or WORDS
                self.word_pool = random.sample(pool, len(pool))
            else:
                qs = list(self.custom_questions) or []
                if len(qs) < self.settings["quiz_count"]:
                    extra = [q for q in QUESTIONS if q not in qs]
                    qs += random.sample(extra, min(len(extra), self.settings["quiz_count"] - len(qs)))
                random.shuffle(qs)
                self.questions = qs[:self.settings["quiz_count"]]
                self.q_i = -1
            self._next()

    def skip(self):
        with self.cond:
            if self.phase == "turn":
                self._end_turn()
            elif self.phase == "question":
                self._end_question()
            elif self.phase == "reveal":
                self._next()

    def end(self):
        with self.cond:
            self.mode, self.phase, self.deadline = None, "idle", None
            self.players.clear()
            self.feed = []
            self._publish()

    def _next(self):
        if self.mode == "draw":
            self._next_turn()
        else:
            self._next_question()

    def _finish(self):
        self.phase, self.deadline = "over", None
        ranked = sorted(self.players.values(), key=lambda p: -p["score"])
        if ranked:
            self._say(f"🏆 {ranked[0]['name']} wins!", "win")
        self._publish()

    # --------------------------------------------------------- draw & guess
    def _next_turn(self):
        while True:
            self.turn_i += 1
            if self.turn_i >= len(self.turns):
                return self._finish()
            if self.turns[self.turn_i] in self.players:
                break
        if not self.word_pool:
            pool = self.custom_words or WORDS
            self.word_pool = random.sample(pool, len(pool))
        self.drawer = self.turns[self.turn_i]
        self.word = self.word_pool.pop()
        self.guessed = {}
        self.strokes = []
        self.hints = set()
        self.skips_left = 2
        self.turn_points = {}
        self.phase = "turn"
        self.started = time.time()
        self.deadline = self.started + self.settings["draw_seconds"]
        self._say(f"{self.players[self.drawer]['name']} is drawing", "info")
        self._publish({"type": "strokes", "strokes": []})
        self._publish()

    def _hint_text(self, reveal_all=False):
        return " ".join("  " if c == " " else (c.upper() if reveal_all or i in self.hints or not c.isalpha() else "_")
                        for i, c in enumerate(self.word))

    def _maybe_hint(self):
        total = self.settings["draw_seconds"]
        elapsed = time.time() - self.started
        letters = [i for i, c in enumerate(self.word) if c.isalpha() and i not in self.hints]
        want = (elapsed > total * 0.5) + (elapsed > total * 0.75)
        if len(self.hints) < want and len(letters) > 2:
            self.hints.add(random.choice(letters))
            self._publish()

    def _all_guessed(self):
        guessers = [t for t in self.players if t != self.drawer]
        return bool(guessers) and all(t in self.guessed for t in guessers)

    def guess(self, token, text):
        with self.cond:
            p = self._player(token)
            if self.phase != "turn":
                return {"ok": False, "out": "Not guessing right now"}
            if token == self.drawer:
                return {"ok": False, "out": "You're drawing!"}
            if token in self.guessed:
                return {"ok": False, "out": "You already got it"}
            now = time.time()
            if now - p["last_guess"] < 0.3:
                return {"ok": False, "out": "Slow down a little"}
            p["last_guess"] = now
            text = re.sub(r"[\x00-\x1f]", "", str(text)).strip()[:40]
            if not text:
                return {"ok": False, "out": "Type a guess"}
            g, w = _norm(text), _norm(self.word)
            if g == w:
                left = max(0.0, self.deadline - now) / self.settings["draw_seconds"]
                pts = max(20, round(100 * left)) + (20 if not self.guessed else 0)
                self.guessed[token] = pts
                p["score"] += pts
                self.players[self.drawer]["score"] += 30
                self.turn_points[token] = self.turn_points.get(token, 0) + pts
                self.turn_points[self.drawer] = self.turn_points.get(self.drawer, 0) + 30
                self._say(f"✔ {p['name']} guessed it! +{pts}", "correct")
                if self._all_guessed():
                    self._end_turn()
                else:
                    self._publish()
                return {"ok": True, "result": "correct", "points": pts}
            if _close(g, w):  # near misses stay private, or everyone else would get the word
                return {"ok": True, "result": "close"}
            self._say(f"{p['name']}: {text}", "guess")
            self._publish()
            return {"ok": True, "result": "wrong"}

    def draw(self, token, sid, color, width, points):
        with self.cond:
            self._player(token)
            if self.phase != "turn" or token != self.drawer:
                return {"ok": False, "out": "It's not your turn to draw"}
            stroke = next((s for s in reversed(self.strokes) if s["id"] == sid), None)
            if stroke is None:
                if len(self.strokes) >= 2000:
                    return {"ok": False, "out": "That's a lot of drawing! Clear some first."}
                stroke = {"id": sid, "color": color, "width": width, "points": []}
                self.strokes.append(stroke)
            if len(stroke["points"]) + len(points) > 5000:
                return {"ok": False, "out": "Line too long; lift your finger and keep going"}
            stroke["points"].extend(points)
            self._publish({"type": "points", "id": sid, "color": stroke["color"], "width": stroke["width"],
                           "points": points})
            return {"ok": True}

    def draw_edit(self, token, action):
        with self.cond:
            self._player(token)
            if self.phase != "turn" or token != self.drawer:
                return {"ok": False, "out": "It's not your turn to draw"}
            if action == "undo" and self.strokes:
                self.strokes.pop()
            elif action == "clear":
                self.strokes = []
            elif action == "skip":
                if self.skips_left <= 0:
                    return {"ok": False, "out": "No skips left"}
                self.skips_left -= 1
                if not self.word_pool:
                    self.word_pool = random.sample(self.custom_words or WORDS, len(self.custom_words or WORDS))
                self.word = self.word_pool.pop()
                self.hints = set()
                self.strokes = []
                self._publish()
            self._publish({"type": "strokes", "strokes": self.strokes})
            return {"ok": True}

    def _end_turn(self):
        self.phase = "reveal"
        self.deadline = time.time() + REVEAL_SECONDS
        self._say(f"The word was {self.word.upper()}", "reveal")
        self._publish()

    # ---------------------------------------------------------- quiz buzzer
    def _next_question(self):
        self.q_i += 1
        if self.q_i >= len(self.questions):
            return self._finish()
        q = self.questions[self.q_i]
        options = list(q[1:])
        random.shuffle(options)
        self.q = {"text": q[0], "options": options, "answer": options.index(q[1])}
        self.answers = {}
        self.turn_points = {}
        self.phase = "question"
        self.started = time.time()
        self.deadline = self.started + self.settings["quiz_seconds"]
        self._publish()

    def _all_answered(self):
        return bool(self.players) and all(t in self.answers for t in self.players)

    def answer(self, token, choice):
        with self.cond:
            self._player(token)
            if self.phase != "question":
                return {"ok": False, "out": "No question right now"}
            if token in self.answers:
                return {"ok": False, "out": "Answer locked in"}
            choice = int(choice)
            if not 0 <= choice < len(self.q["options"]):
                raise ValueError("bad choice")
            self.answers[token] = (choice, time.time())
            if self._all_answered():
                self._end_question()
            else:
                self._publish()
            return {"ok": True}

    def _end_question(self):
        total = self.settings["quiz_seconds"]
        right = sorted((t for t, (c, _) in self.answers.items() if c == self.q["answer"] and t in self.players),
                       key=lambda t: self.answers[t][1])
        for i, t in enumerate(right):
            left = max(0.0, self.started + total - self.answers[t][1]) / total
            pts = 500 + round(500 * left) + (100 if i == 0 else 0)
            self.players[t]["score"] += pts
            self.turn_points[t] = pts
        self.fastest = right[0] if right else None
        self.phase = "reveal"
        self.deadline = time.time() + REVEAL_SECONDS
        self._publish()

    # ---------------------------------------------------------------- views
    def _public_players(self):
        return [{"id": p["id"], "name": p["name"], "score": p["score"], "color": p["color"],
                 "online": self.connections.get(t, 0) > 0}
                for t, p in sorted(self.players.items(), key=lambda kv: -kv[1]["score"])]

    def view(self, token=None, join_url=None):
        """What one screen may see: the TV (no token) or a player."""
        with self.cond:
            v = {"mode": self.mode, "phase": self.phase, "seconds": self._seconds_left(),
                 "players": self._public_players(), "feed": self.feed[-8:], "join_url": join_url,
                 "settings": self.settings}
            me = self.players.get(token) if token else None
            if token and not me:
                v["you"] = None
                return v
            if me:
                v["you"] = {"id": me["id"], "name": me["name"], "score": me["score"], "color": me["color"]}
            if self.mode == "draw" and self.phase in ("turn", "reveal"):
                d = self.players.get(self.drawer)
                v["draw"] = {"drawer": d["name"] if d else "—", "drawer_id": d["id"] if d else None,
                             "hint": self._hint_text(reveal_all=self.phase == "reveal"),
                             "turn": self.turn_i + 1, "turns": len(self.turns),
                             "guessed": [self.players[t]["name"] for t in self.guessed if t in self.players],
                             "points": {self.players[t]["id"]: pts for t, pts in self.turn_points.items()
                                        if t in self.players}}
                if self.phase == "reveal":
                    v["draw"]["word"] = self.word
                if me:
                    v["draw"]["you_draw"] = token == self.drawer
                    v["draw"]["you_guessed"] = token in self.guessed
                    if token == self.drawer:
                        v["draw"]["word"] = self.word
                        v["draw"]["skips_left"] = self.skips_left
            if self.mode == "quiz" and self.phase in ("question", "reveal"):
                v["quiz"] = {"index": self.q_i + 1, "total": len(self.questions), "question": self.q["text"],
                             "options": self.q["options"], "answered": len(self.answers),
                             "answered_ids": [self.players[t]["id"] for t in self.answers if t in self.players]}
                if self.phase == "reveal":
                    v["quiz"]["answer"] = self.q["answer"]
                    v["quiz"]["points"] = {self.players[t]["id"]: pts for t, pts in self.turn_points.items()
                                           if t in self.players}
                    f = self.players.get(self.fastest) if self.fastest else None
                    v["quiz"]["fastest"] = f["name"] if f else None
                if me and token in self.answers:
                    v["quiz"]["your_choice"] = self.answers[token][0]
            return v

    def strokes_now(self):
        with self.cond:
            return list(self.strokes) if self.mode == "draw" and self.phase in ("turn", "reveal") else []

    def stream(self, token, write, join_url):
        """Feed one screen until it disconnects. `write(dict)` sends one event."""
        with self.cond:
            seq = self.seq
            if token:
                self.connections[token] = self.connections.get(token, 0) + 1
                self._publish()
        try:
            write({"type": "view", "view": self.view(token, join_url)})
            write({"type": "strokes", "strokes": self.strokes_now()})
            last_beat = time.time()
            while True:
                with self.cond:
                    self.cond.wait_for(lambda: self.seq > seq, 1.0)
                    evs = [e for s, e in self.events if s > seq]
                    stale = self.events and seq < self.events[0][0] - 1
                    seq = self.seq
                if stale:
                    write({"type": "strokes", "strokes": self.strokes_now()})
                    evs = [e for e in evs if e["type"] == "state"][:1]
                state_changed = False
                for e in evs:
                    if e["type"] == "state":
                        state_changed = True
                    else:
                        write(e)
                # once a second the countdown changes, so refresh the view then too
                if state_changed or (self.deadline and time.time() - last_beat >= 1):
                    write({"type": "view", "view": self.view(token, join_url)})
                    last_beat = time.time()
                elif time.time() - last_beat >= 15:
                    write(None)  # keepalive
                    last_beat = time.time()
        finally:
            with self.cond:
                if token:
                    self.connections[token] = self.connections.get(token, 1) - 1
                    self._publish()
