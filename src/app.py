#!/usr/bin/env python3
"""Better Guesser: a tiny self-hosted geography guessing game. Python stdlib only."""
import datetime, hashlib, hmac, json, math, os, random, re, secrets, sqlite3, sys, time, traceback
import urllib.error, urllib.parse, urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("BG_DB", "game.db")
PORT = int(os.environ.get("BG_PORT", "8095"))
HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE = f"bg_session_{PORT}"  # per-instance, so two installs on one domain don't clash
ROUNDS = 5
SESSION_SECS = 30 * 86400
USERNAME_RE = r"[A-Za-z0-9_.-]{3,20}"
RANKS = [(0, "Bronze"), (100, "Silver"), (200, "Gold"), (300, "Platinum"), (400, "Diamond"), (500, "Master")]
LEGEND_RP = RANKS[-1][0]  # reaching the top rank unlocks Legend Division
WIN_RP, LOSS_RP, START_MMR, ELO_K = 25, 20, 1500, 32
SEATS = {"ranked": 2, "legend": 2, "1v1": 2, "2v2": 4}  # players per match; private lobbies are full by the time they start
DAILY_CYCLE = 7  # daily reward grows for 7 straight days, then the cycle restarts

# Cities with decent Mapillary coverage; rounds land somewhere within ~10 km of one.
SEEDS = [
    (48.857, 2.352), (51.507, -0.128), (52.520, 13.405), (40.416, -3.704), (41.903, 12.496), (52.370, 4.895),
    (50.850, 4.352), (48.208, 16.373), (50.075, 14.438), (52.230, 21.012), (47.498, 19.040), (59.329, 18.069),
    (59.913, 10.752), (60.170, 24.938), (55.676, 12.568), (53.349, -6.260), (38.722, -9.139), (37.984, 23.728),
    (44.427, 26.103), (42.698, 23.322), (45.815, 15.982), (46.056, 14.506), (54.687, 25.280), (56.950, 24.105),
    (59.437, 24.754), (47.377, 8.541), (45.464, 9.190), (43.296, 5.370), (41.385, 2.173), (53.481, -2.242),
    (55.953, -3.188), (64.146, -21.942), (40.713, -74.006), (34.052, -118.244), (41.878, -87.630), (29.760, -95.370),
    (47.606, -122.332), (37.775, -122.419), (49.283, -123.121), (43.653, -79.383), (45.502, -73.567), (25.762, -80.192),
    (39.739, -104.990), (19.433, -99.133), (20.659, -103.349), (4.711, -74.072), (-12.046, -77.043), (-33.449, -70.669),
    (-34.604, -58.382), (-23.551, -46.633), (-22.907, -43.173), (-0.180, -78.468), (-33.869, 151.209), (-37.814, 144.963),
    (-27.470, 153.026), (-31.953, 115.857), (-36.849, 174.763), (-41.287, 174.776), (35.690, 139.692), (34.694, 135.502),
    (37.567, 126.978), (25.033, 121.565), (1.352, 103.820), (13.756, 100.502), (3.139, 101.687), (14.600, 120.984),
    (-6.209, 106.846), (21.028, 105.834), (28.614, 77.209), (19.076, 72.878), (12.972, 77.595), (6.927, 79.861),
    (-33.925, 18.424), (-26.204, 28.047), (-1.292, 36.822), (5.604, -0.187), (6.524, 3.379), (33.573, -7.590),
    (30.044, 31.236), (41.008, 28.978), (32.085, 34.782), (25.205, 55.271), (43.238, 76.946), (41.716, 44.783),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, pw TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
  cheat_reveal INTEGER NOT NULL DEFAULT 0, cheat_multiplier REAL NOT NULL DEFAULT 1,
  created REAL, last_login REAL, last_ip TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires REAL);
CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  started REAL, total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rounds(game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE, n INTEGER NOT NULL,
  image_id TEXT, lat REAL, lng REAL, guess_lat REAL, guess_lng REAL, distance REAL, score INTEGER, PRIMARY KEY(game_id, n));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS lobbies(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, mode TEXT NOT NULL,
  host_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, started INTEGER NOT NULL DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS lobby_members(lobby_id INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, team TEXT, PRIMARY KEY(lobby_id, user_id));
CREATE TABLE IF NOT EXISTS lobby_rounds(lobby_id INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE, n INTEGER NOT NULL,
  image_id TEXT, lat REAL, lng REAL, PRIMARY KEY(lobby_id, n));
"""
MIGRATIONS = [
    ("games", "lobby_id", "INTEGER REFERENCES lobbies(id) ON DELETE SET NULL"),  # v0.2
    ("users", "rank_points", "INTEGER NOT NULL DEFAULT 0"),  # v0.3
    ("users", "legend", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "mmr", f"REAL NOT NULL DEFAULT {START_MMR}"),
    ("lobbies", "settled", "INTEGER NOT NULL DEFAULT 0"),
    ("lobby_members", "delta", "REAL"),
    ("users", "cheat_aim", "INTEGER NOT NULL DEFAULT 0"),  # v0.4
    ("users", "cheat_floor", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "cheat_shield", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "daily_last", "TEXT"),
    ("users", "daily_streak", "INTEGER NOT NULL DEFAULT 0"),
    ("lobbies", "matchmade", "INTEGER NOT NULL DEFAULT 0", "UPDATE lobbies SET matchmade=1 WHERE mode IN ('ranked','legend')"),
]


class Err(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with db() as c:
        c.executescript(SCHEMA)
        for table, col, ddl, *backfill in MIGRATIONS:  # columns added after v0.1, for upgraded installs
            if col not in [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                for q in backfill:
                    c.execute(q)
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS games_lobby_user ON games(lobby_id, user_id) WHERE lobby_id IS NOT NULL")
        c.execute("INSERT OR IGNORE INTO settings VALUES('registration_open','1')")


def setting(key, default=""):
    row = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return f"{salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000).hex()}"


def check_pw(pw, stored):
    return hmac.compare_digest(hash_pw(pw, stored.split("$", 1)[0]), stored)


def valid_username(name):
    if not isinstance(name, str) or not re.fullmatch(USERNAME_RE, name):
        raise Err(400, "Username must be 3-20 letters, digits, '.', '_' or '-'")
    return name


def bounded(v, lo, hi, what):
    if not lo <= v <= hi:
        raise Err(400, f"{what} must be between {lo} and {hi}")
    return v


def rank_name(rp):
    return [name for floor, name in RANKS if rp >= floor][-1]


def valid_password(pw):
    if not isinstance(pw, str) or not 6 <= len(pw) <= 200:
        raise Err(400, "Password must be 6-200 characters")
    return pw


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def score_for(km):
    return round(1000 * math.exp(-km / 1200))  # 1000 = spot on, ~430 at 1000 km, ~0 past 8000 km


def pick_location(token):
    """Return (image_id, lat, lng) of a random Mapillary image near a seed city."""
    for _ in range(15):
        lat, lng = random.choice(SEEDS)
        lat, lng, d = lat + random.uniform(-0.1, 0.1), lng + random.uniform(-0.1, 0.1), 0.02
        q = urllib.parse.urlencode({"access_token": token, "fields": "id,geometry,is_pano", "limit": 20,
                                    "bbox": f"{lng - d},{lat - d},{lng + d},{lat + d}"})
        try:
            with urllib.request.urlopen(f"https://graph.mapillary.com/images?{q}", timeout=10) as r:
                imgs = json.load(r).get("data", [])
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise Err(502, f"Mapillary rejected the request (HTTP {e.code}) - check the token in the admin panel")
            continue
        except (OSError, ValueError):
            continue
        if imgs:
            img = next((i for i in imgs if i.get("is_pano")), imgs[0])  # prefer 360° panoramas
            g_lng, g_lat = img["geometry"]["coordinates"]
            return img["id"], g_lat, g_lng
    raise Err(502, "Could not find street imagery, try again")


# ---------- API handlers: fn(handler, body, *url_groups) -> dict | (dict, set_cookie) ----------

def new_session(h, uid):
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with db() as c:
        c.execute("DELETE FROM sessions WHERE expires<?", (now,))
        c.execute("INSERT INTO sessions VALUES(?,?,?)", (tok, uid, now + SESSION_SECS))
        c.execute("UPDATE users SET last_login=?, last_ip=? WHERE id=?", (now, h.client_ip(), uid))
    secure = "; Secure" if h.headers.get("X-Forwarded-Proto") == "https" else ""
    return {"ok": True}, f"{COOKIE}={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECS}{secure}"


def api_register(h, b):
    if setting("registration_open") != "1":
        raise Err(403, "Sign-ups are closed")
    name, pw = valid_username(b.get("username")), valid_password(b.get("password"))
    try:
        with db() as c:
            uid = c.execute("INSERT INTO users(username,pw,created) VALUES(?,?,?)", (name, hash_pw(pw), time.time())).lastrowid
    except sqlite3.IntegrityError:
        raise Err(409, "Username taken")
    return new_session(h, uid)


def api_login(h, b):
    u = db().execute("SELECT * FROM users WHERE username=?", (str(b.get("username")),)).fetchone()
    if not u or not check_pw(str(b.get("password")), u["pw"]):
        raise Err(401, "Wrong username or password")
    if u["banned"]:
        raise Err(403, "This account is banned")
    return new_session(h, u["id"])


def api_logout(h, b):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (h.token(),))
    return {"ok": True}, f"{COOKIE}=; Path=/; Max-Age=0"


def api_me(h, b):
    u = h.user()
    nxt = next((floor for floor, _ in RANKS if floor > u["rank_points"]), None)
    today = utc_today()
    claimed = u["daily_last"] == today.isoformat()
    return {"username": u["username"], "is_admin": bool(u["is_admin"]),
            "cheat_reveal": bool(u["cheat_reveal"]), "cheat_aim": bool(u["cheat_aim"]),
            "daily_available": not claimed,
            "daily_next": daily_reward(u["daily_streak"] + 1 if claimed else next_streak(u, today), u["legend"]),
            "daily_streak": u["daily_streak"] if u["daily_last"] in (today.isoformat(), str(today - datetime.timedelta(days=1))) else 0,
            "rank": rank_name(u["rank_points"]), "rank_points": u["rank_points"], "next_rank_at": nxt,
            "legend": bool(u["legend"]), "legend_eligible": u["rank_points"] >= LEGEND_RP, "mmr": round(u["mmr"])}


def game_state(u, gid):
    c = db()
    g = c.execute("SELECT * FROM games WHERE id=? AND user_id=?", (gid, u["id"])).fetchone()
    if not g:
        raise Err(404, "No such game")
    played = c.execute("SELECT n, score, distance FROM rounds WHERE game_id=? AND score IS NOT NULL ORDER BY n", (gid,)).fetchall()
    state = {"id": g["id"], "rounds": ROUNDS, "total": g["total"], "done": bool(g["done"]),
             "history": [dict(r) for r in played], "token": setting("mapillary_token")}
    if g["done"]:
        return state
    n = len(played) + 1
    cur = c.execute("SELECT image_id FROM rounds WHERE game_id=? AND n=?", (gid, n)).fetchone()
    if not cur:
        # network call, outside any transaction; lobby games share one set of locations
        image_id, lat, lng = lobby_location(c, g["lobby_id"], n, state["token"]) if g["lobby_id"] else pick_location(state["token"])
        with c:
            c.execute("INSERT OR IGNORE INTO rounds(game_id,n,image_id,lat,lng) VALUES(?,?,?,?,?)", (gid, n, image_id, lat, lng))
        cur = c.execute("SELECT image_id FROM rounds WHERE game_id=? AND n=?", (gid, n)).fetchone()
    return {**state, "round": n, "image_id": cur["image_id"]}


def lobby_location(c, lobby_id, n, token):
    q = "SELECT image_id, lat, lng FROM lobby_rounds WHERE lobby_id=? AND n=?"
    if not c.execute(q, (lobby_id, n)).fetchone():
        loc = pick_location(token)
        with c:
            c.execute("INSERT OR IGNORE INTO lobby_rounds VALUES(?,?,?,?,?)", (lobby_id, n, *loc))
    return tuple(c.execute(q, (lobby_id, n)).fetchone())


def api_new_game(h, b):
    u = h.user()
    if not setting("mapillary_token"):
        raise Err(503, "The admin has not set a Mapillary token yet")
    with db() as c:
        gid = c.execute("INSERT INTO games(user_id, started) VALUES(?,?)", (u["id"], time.time())).lastrowid
    return game_state(u, gid)


def api_game(h, b, gid):
    return game_state(h.user(), int(gid))


def open_round(u, gid):
    r = db().execute("SELECT r.* FROM rounds r JOIN games g ON g.id=r.game_id WHERE g.id=? AND g.user_id=? AND r.score IS NULL",
                     (int(gid), u["id"])).fetchone()
    if not r:
        raise Err(409, "No open round")
    return r


def api_guess(h, b, gid):
    u = h.user()
    lat, lng = float(b["lat"]), float(b["lng"])
    if not (-90 <= lat <= 90 and math.isfinite(lng)):
        raise Err(400, "Bad coordinates")
    lng = (lng + 180) % 360 - 180  # the map wraps around the world
    r = open_round(u, gid)
    km = haversine_km(lat, lng, r["lat"], r["lng"])
    s = round(max(score_for(km), u["cheat_floor"]) * u["cheat_multiplier"])  # cheats apply in every mode
    done = r["n"] >= ROUNDS
    with db() as c:
        if c.execute("UPDATE rounds SET guess_lat=?, guess_lng=?, distance=?, score=? WHERE game_id=? AND n=? AND score IS NULL",
                     (lat, lng, km, s, r["game_id"], r["n"])).rowcount != 1:
            raise Err(409, "Already guessed")
        c.execute("UPDATE games SET total=total+?, done=? WHERE id=?", (s, int(done), r["game_id"]))
        total = c.execute("SELECT total FROM games WHERE id=?", (r["game_id"],)).fetchone()["total"]
        if done:
            settle(c, r["game_id"])
    return {"distance_km": km, "score": s, "total": total, "done": done,
            "answer": {"lat": r["lat"], "lng": r["lng"]}, "guess": {"lat": lat, "lng": lng}}


def settle(c, game_id):
    """Award RP (ranked) or MMR (legend) once both players of a 1v1 match have finished. Runs inside the guess transaction."""
    lb = c.execute("SELECT l.* FROM lobbies l JOIN games g ON g.lobby_id=l.id WHERE g.id=?", (game_id,)).fetchone()
    if not lb or lb["mode"] not in ("ranked", "legend"):
        return
    p = c.execute("""SELECT u.id, u.rank_points, u.mmr, u.cheat_shield, COALESCE(g.total,0) total, COALESCE(g.done,0) done
        FROM lobby_members m JOIN users u ON u.id=m.user_id LEFT JOIN games g ON g.lobby_id=m.lobby_id AND g.user_id=m.user_id
        WHERE m.lobby_id=? ORDER BY u.id""", (lb["id"],)).fetchall()
    if len(p) != 2 or not all(x["done"] for x in p):
        return
    if c.execute("UPDATE lobbies SET settled=1 WHERE id=? AND settled=0", (lb["id"],)).rowcount != 1:
        return  # already settled
    a, b = p
    result = 1 if a["total"] > b["total"] else 0 if a["total"] < b["total"] else 0.5  # a's result
    for me, opp, res in ((a, b, result), (b, a, 1 - result)):
        if lb["mode"] == "legend":
            delta = ELO_K * (res - 1 / (1 + 10 ** ((opp["mmr"] - me["mmr"]) / 400)))
            delta = max(delta, 0) if me["cheat_shield"] else delta
            c.execute("UPDATE users SET mmr=mmr+? WHERE id=?", (delta, me["id"]))
        else:
            delta = WIN_RP if res == 1 else -min(LOSS_RP, me["rank_points"]) if res == 0 and not me["cheat_shield"] else 0
            c.execute("UPDATE users SET rank_points=rank_points+? WHERE id=?", (delta, me["id"]))
        c.execute("UPDATE lobby_members SET delta=? WHERE lobby_id=? AND user_id=?", (round(delta, 1), lb["id"], me["id"]))


def api_queue(h, b):
    """Matchmaking for ranked, 1v1 and 2v2. Async: you play now, and the next players in the same queue play the same 5 places."""
    u = h.user()
    mode = b.get("mode")
    if mode not in ("ranked", "1v1", "2v2"):
        raise Err(400, "Mode must be ranked, 1v1 or 2v2")
    if not setting("mapillary_token"):
        raise Err(503, "The admin has not set a Mapillary token yet")
    if mode == "ranked" and u["legend"]:
        mode = "legend"
    # ranked pairs the closest skill; casual queues just take the oldest open match
    skill, my_skill = {"ranked": ("hu.rank_points", u["rank_points"]), "legend": ("hu.mmr", u["mmr"])}.get(mode, ("0", 0))
    c = db()
    with c:
        c.execute("BEGIN IMMEDIATE")  # one matchmaker at a time, so two players can't grab the same last seat
        mine = c.execute("""SELECT l.code FROM lobbies l JOIN lobby_members m ON m.lobby_id=l.id AND m.user_id=?
            LEFT JOIN games g ON g.lobby_id=l.id AND g.user_id=m.user_id
            WHERE l.mode=? AND l.matchmade=1 AND COALESCE(g.done,0)=0 ORDER BY l.id LIMIT 1""", (u["id"], mode)).fetchone()
        if mine:
            return {"code": mine["code"]}  # finish your current match first
        # shortcut: a player who never finishes leaves the match unsettled forever; add a timeout if that bites
        open_ = c.execute(f"""SELECT l.id, l.code FROM lobbies l JOIN users hu ON hu.id=l.host_id
            WHERE l.mode=? AND l.matchmade=1 AND hu.banned=0
              AND NOT EXISTS(SELECT 1 FROM lobby_members x WHERE x.lobby_id=l.id AND x.user_id=?)
              AND (SELECT COUNT(*) FROM lobby_members x WHERE x.lobby_id=l.id) < ?
            ORDER BY ABS({skill}-?), l.id LIMIT 1""", (mode, u["id"], SEATS[mode], my_skill)).fetchone()
        if not open_:
            return {"code": new_lobby(c, u["id"], mode, matchmade=1)}
        team = None
        if mode == "2v2":
            teams = [r["team"] for r in c.execute("SELECT team FROM lobby_members WHERE lobby_id=?", (open_["id"],))]
            team = min("AB", key=teams.count)
        c.execute("INSERT INTO lobby_members(lobby_id,user_id,team) VALUES(?,?,?)", (open_["id"], u["id"], team))
        return {"code": open_["code"]}


def utc_today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def next_streak(u, today):
    """Streak you'd have after claiming today: continues only if you claimed yesterday."""
    return u["daily_streak"] + 1 if u["daily_last"] == str(today - datetime.timedelta(days=1)) else 1


def daily_reward(streak, legend):
    day = (streak - 1) % DAILY_CYCLE + 1
    return {"day": day, "amount": day if legend else 5 * day, "unit": "MMR" if legend else "RP"}  # legends get a small MMR bump


def api_daily(h, b):
    u, today = h.user(), utc_today()
    streak = next_streak(u, today)
    reward = daily_reward(streak, u["legend"])
    col = "mmr" if u["legend"] else "rank_points"
    with db() as c:
        if c.execute(f"""UPDATE users SET {col}={col}+?, daily_streak=?, daily_last=?
                WHERE id=? AND (daily_last IS NULL OR daily_last!=?)""",
                (reward["amount"], streak, today.isoformat(), u["id"], today.isoformat())).rowcount != 1:
            raise Err(409, "Already claimed today. Come back tomorrow!")
    return {**reward, "streak": streak}


def api_legend_enter(h, b):
    u = h.user()
    if u["rank_points"] < LEGEND_RP:
        raise Err(403, f"Reach {RANKS[-1][1]} ({LEGEND_RP} RP) to enter Legend Division")
    with db() as c:
        c.execute("UPDATE users SET legend=1, mmr=? WHERE id=? AND legend=0", (START_MMR, u["id"]))
    return {"ok": True}


def api_legends(h, b):
    """Public: no login needed."""
    rows = db().execute("""SELECT u.username, ROUND(u.mmr) mmr,
        (SELECT COUNT(*) FROM lobby_members m JOIN lobbies l ON l.id=m.lobby_id WHERE m.user_id=u.id AND l.mode='legend' AND l.settled=1) matches
        FROM users u WHERE u.legend=1 AND u.banned=0 ORDER BY u.mmr DESC, u.username LIMIT 100""").fetchall()
    return {"rows": [dict(r) for r in rows]}


def api_peek(h, b, gid):
    u = h.user()
    if not (u["cheat_reveal"] or u["cheat_aim"]):
        raise Err(403, "Nice try")
    r = open_round(u, gid)
    return {"lat": r["lat"], "lng": r["lng"]}


def api_leaderboard(h, b):
    h.user()
    rows = db().execute("""SELECT u.username, COUNT(g.id) games, COALESCE(MAX(g.total),0) best, COALESCE(SUM(g.total),0) points
        FROM users u LEFT JOIN games g ON g.user_id=u.id AND g.done=1 WHERE u.banned=0
        GROUP BY u.id ORDER BY best DESC, points DESC LIMIT 50""").fetchall()
    return {"rows": [dict(r) for r in rows]}


def get_lobby(code):
    lb = db().execute("SELECT * FROM lobbies WHERE code=?", (str(code).strip().upper(),)).fetchone()
    if not lb:
        raise Err(404, "No lobby with that code")
    return lb


def lobby_state(u, code):
    lb, c = get_lobby(code), db()
    rows = c.execute("""SELECT u.id, u.username, m.team, m.delta, g.id game_id, COALESCE(g.total,0) total, COALESCE(g.done,0) done,
        (SELECT COUNT(*) FROM rounds r WHERE r.game_id=g.id AND r.score IS NOT NULL) played
        FROM lobby_members m JOIN users u ON u.id=m.user_id
        LEFT JOIN games g ON g.lobby_id=m.lobby_id AND g.user_id=m.user_id
        WHERE m.lobby_id=? ORDER BY total DESC, u.username""", (lb["id"],)).fetchall()
    me = next((r for r in rows if r["id"] == u["id"]), None)
    if not me:
        raise Err(403, "Join this lobby first")
    teams = {}
    for r in rows:
        if r["team"]:
            teams[r["team"]] = teams.get(r["team"], 0) + r["total"]
    return {"code": lb["code"], "mode": lb["mode"], "started": bool(lb["started"]), "is_host": lb["host_id"] == u["id"],
            "rounds": ROUNDS, "teams": teams, "my_team": me["team"], "my_done": bool(me["done"]),
            "finished": bool(lb["started"]) and all(r["done"] for r in rows) and len(rows) == SEATS.get(lb["mode"], len(rows)),
            "seats": SEATS.get(lb["mode"]),
            "matchmade": bool(lb["matchmade"]),
            "members": [{k: r[k] for k in ("username", "team", "total", "played", "done", "delta")} | {"host": r["id"] == lb["host_id"]} for r in rows]}


def api_lobby_create(h, b):
    u = h.user()
    mode = b.get("mode")
    if mode not in ("1v1", "2v2"):
        raise Err(400, "Mode must be 1v1 or 2v2")
    with db() as c:
        code = new_lobby(c, u["id"], mode)
    return lobby_state(u, code)


def new_lobby(c, host_id, mode, matchmade=0):
    """Private lobbies wait for the host's Start; matchmade ones start immediately."""
    for _ in range(5):
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        try:
            lid = c.execute("INSERT INTO lobbies(code,mode,host_id,started,matchmade,created) VALUES(?,?,?,?,?,?)",
                            (code, mode, host_id, matchmade, matchmade, time.time())).lastrowid
            break
        except sqlite3.IntegrityError:
            continue
    c.execute("INSERT INTO lobby_members(lobby_id,user_id,team) VALUES(?,?,?)", (lid, host_id, "A" if mode == "2v2" else None))
    return code


def api_lobby_join(h, b):
    """Join a lobby, or (2v2) switch team. Only before the host starts it."""
    u, lb = h.user(), get_lobby(b.get("code", ""))
    if lb["matchmade"]:
        raise Err(403, "Matchmade games can only be joined through matchmaking")
    if lb["started"]:
        raise Err(409, "This lobby already started")
    c = db()
    others = c.execute("SELECT team FROM lobby_members WHERE lobby_id=? AND user_id!=?", (lb["id"], u["id"])).fetchall()
    team = None
    if lb["mode"] == "2v2":
        counts = {t: sum(r["team"] == t for r in others) for t in "AB"}
        team = b.get("team") or min("AB", key=counts.get)
        if team not in counts:
            raise Err(400, "Team must be A or B")
        if counts[team] >= 2:
            raise Err(409, f"Team {team} is full")
    elif len(others) >= (2 if lb["mode"] == "1v1" else 20):  # others excludes the joiner
        raise Err(409, "Lobby is full")
    with c:
        c.execute("INSERT INTO lobby_members(lobby_id,user_id,team) VALUES(?,?,?) ON CONFLICT(lobby_id,user_id) DO UPDATE SET team=excluded.team",
                  (lb["id"], u["id"], team))
    return lobby_state(u, lb["code"])


def api_lobby(h, b, code):
    return lobby_state(h.user(), code)


def api_lobby_start(h, b, code):
    u, lb = h.user(), get_lobby(code)
    if lb["host_id"] != u["id"]:
        raise Err(403, "Only the host can start")
    teams = [r["team"] for r in db().execute("SELECT team FROM lobby_members WHERE lobby_id=?", (lb["id"],))]
    if lb["mode"] == "2v2" and sorted(teams) != ["A", "A", "B", "B"]:
        raise Err(409, "2v2 needs exactly 2 players on each team")
    if lb["mode"] != "2v2" and len(teams) < 2:
        raise Err(409, "Need at least 2 players")
    with db() as c:
        c.execute("UPDATE lobbies SET started=1 WHERE id=?", (lb["id"],))
    return lobby_state(u, code)


def api_lobby_play(h, b, code):
    u = h.user()
    st, lb = lobby_state(u, code), get_lobby(code)  # lobby_state checks membership
    if not st["started"]:
        raise Err(409, "The host hasn't started yet")
    if not setting("mapillary_token"):
        raise Err(503, "The admin has not set a Mapillary token yet")
    with db() as c:
        c.execute("INSERT OR IGNORE INTO games(user_id, started, lobby_id) VALUES(?,?,?)", (u["id"], time.time(), lb["id"]))
        gid = c.execute("SELECT id FROM games WHERE lobby_id=? AND user_id=?", (lb["id"], u["id"])).fetchone()["id"]
    return game_state(u, gid)


def api_my_lobbies(h, b):
    u = h.user()
    rows = db().execute("""SELECT l.code, l.mode, l.started, l.settled, l.matchmade, m.delta, hu.username host,
        (SELECT COUNT(*) FROM lobby_members x WHERE x.lobby_id=l.id) players
        FROM lobby_members m JOIN lobbies l ON l.id=m.lobby_id JOIN users hu ON hu.id=l.host_id
        WHERE m.user_id=? ORDER BY l.id DESC LIMIT 20""", (u["id"],)).fetchall()
    return {"lobbies": [dict(r) for r in rows]}


def api_admin_users(h, b):
    h.user(admin=True)
    rows = db().execute("""SELECT u.id, u.username, u.is_admin, u.banned, u.cheat_reveal, u.cheat_multiplier,
        u.rank_points, u.legend, ROUND(u.mmr) mmr, u.cheat_aim, u.cheat_floor, u.cheat_shield, u.daily_streak,
        u.created, u.last_login, u.last_ip, COUNT(g.id) games, SUM(g.done) finished,
        COALESCE(MAX(CASE WHEN g.done THEN g.total END),0) best, COALESCE(SUM(g.total),0) points,
        (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id AND s.expires>?) sessions
        FROM users u LEFT JOIN games g ON g.user_id=u.id GROUP BY u.id ORDER BY u.id""", (time.time(),)).fetchall()
    return {"users": [dict(r) for r in rows]}


def api_admin_user(h, b):
    me = h.user(admin=True)
    uid, action = int(b["id"]), b.get("action")
    if not db().execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        raise Err(404, "No such user")
    self_edit = uid == me["id"]
    with db() as c:
        if action == "delete":
            if self_edit:
                raise Err(400, "You can't delete yourself")
            c.execute("DELETE FROM users WHERE id=?", (uid,))
        elif action == "reset_stats":
            c.execute("DELETE FROM games WHERE user_id=?", (uid,))
        elif action == "logout":
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        elif action == "password":
            c.execute("UPDATE users SET pw=? WHERE id=?", (hash_pw(valid_password(b.get("password"))), uid))
            if not self_edit:
                c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        elif action == "update":
            f = b["fields"]
            if self_edit and (not f.get("is_admin", True) or f.get("banned")):
                raise Err(400, "You can't demote or ban yourself")
            sets = {k: conv(f[k]) for k, conv in ADMIN_FIELDS.items() if k in f}  # only the fields sent
            if not sets:
                raise Err(400, "Nothing to update")
            try:
                c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), uid))
            except sqlite3.IntegrityError:
                raise Err(409, "Username taken")
            if f.get("banned"):
                c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        else:
            raise Err(400, "Unknown action")
    return {"ok": True}


flag = lambda v: int(bool(v))  # noqa: E731
ADMIN_FIELDS = {  # column -> validator; keys are fixed, so safe to put in SQL
    "username": valid_username, "is_admin": flag, "banned": flag, "cheat_reveal": flag, "legend": flag,
    "cheat_multiplier": lambda v: bounded(float(v), 0, 100, "Multiplier"),
    "rank_points": lambda v: bounded(int(v), 0, 100_000, "RP"),
    "mmr": lambda v: bounded(float(v), 0, 10_000, "MMR"),
    "cheat_aim": flag, "cheat_shield": flag,
    "cheat_floor": lambda v: bounded(int(v), 0, 1000, "Score floor"),
}


def api_admin_settings(h, b):
    h.user(admin=True)
    if h.command == "POST":
        set_setting("mapillary_token", str(b.get("mapillary_token", "")).strip())
        set_setting("registration_open", "1" if b.get("registration_open") else "0")
    return {"mapillary_token": setting("mapillary_token"), "registration_open": setting("registration_open") == "1"}


ROUTES = [
    ("POST", r"/api/register", api_register),
    ("POST", r"/api/login", api_login),
    ("POST", r"/api/logout", api_logout),
    ("GET", r"/api/me", api_me),
    ("GET", r"/api/leaderboard", api_leaderboard),
    ("POST", r"/api/game", api_new_game),
    ("GET", r"/api/game/(\d+)", api_game),
    ("POST", r"/api/game/(\d+)/guess", api_guess),
    ("GET", r"/api/game/(\d+)/peek", api_peek),
    ("GET", r"/api/lobbies", api_my_lobbies),
    ("POST", r"/api/queue", api_queue),
    ("POST", r"/api/daily", api_daily),
    ("POST", r"/api/legend/enter", api_legend_enter),
    ("GET", r"/api/legends", api_legends),
    ("POST", r"/api/lobby", api_lobby_create),
    ("POST", r"/api/lobby/join", api_lobby_join),
    ("GET", r"/api/lobby/([A-Za-z0-9]{6})", api_lobby),
    ("POST", r"/api/lobby/([A-Za-z0-9]{6})/start", api_lobby_start),
    ("POST", r"/api/lobby/([A-Za-z0-9]{6})/play", api_lobby_play),
    ("GET", r"/api/admin/users", api_admin_users),
    ("POST", r"/api/admin/user", api_admin_user),
    ("GET", r"/api/admin/settings", api_admin_settings),
    ("POST", r"/api/admin/settings", api_admin_settings),
]


class Handler(BaseHTTPRequestHandler):
    def client_ip(self):
        return self.headers.get("X-Real-IP") or self.client_address[0]  # set by the YunoHost nginx proxy

    def token(self):
        m = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
        return m.value if m else ""

    def user(self, admin=False):
        u = db().execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
                         (self.token(), time.time())).fetchone()
        if not u or u["banned"]:
            raise Err(401, "Please log in")
        if admin and not u["is_admin"]:
            raise Err(403, "Admins only")
        return u

    def send(self, code, body, ctype="application/json", cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()

    def route(self):
        path = urllib.parse.urlsplit(self.path).path
        if self.command == "GET" and path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        for method, pattern, fn in ROUTES:
            m = re.fullmatch(pattern, path)
            if method != self.command or not m:
                continue
            try:
                body = {}
                if self.command == "POST":
                    # JSON-only POSTs + SameSite cookie = no cross-site form CSRF
                    if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                        raise Err(415, "JSON required")
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 10_000:
                        raise Err(413, "Too large")
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise Err(400, "Bad request")
                res, cookie = fn(self, body, *m.groups()), None
                if isinstance(res, tuple):
                    res, cookie = res
                return self.send(200, json.dumps(res).encode(), cookie=cookie)
            except Err as e:
                return self.send(e.code, json.dumps({"error": e.msg}).encode())
            except (KeyError, TypeError, ValueError):
                return self.send(400, json.dumps({"error": "Bad request"}).encode())
            except Exception:
                traceback.print_exc()
                return self.send(500, json.dumps({"error": "Server error"}).encode())
        self.send(404, json.dumps({"error": "Not found"}).encode())


def cli_init(admin_name, token=""):
    """Create/refresh the admin account (password read from stdin). Used by the YunoHost install script."""
    init_db()
    pw = valid_password(sys.stdin.readline().rstrip("\n"))
    with db() as c:
        c.execute("""INSERT INTO users(username,pw,is_admin,created) VALUES(?,?,1,?)
                     ON CONFLICT(username) DO UPDATE SET pw=excluded.pw, is_admin=1, banned=0""",
                  (valid_username(admin_name), hash_pw(pw), time.time()))
    if token:
        set_setting("mapillary_token", token)


if __name__ == "__main__":
    if sys.argv[1:2] == ["init"]:
        cli_init(*sys.argv[2:4])
        sys.exit()
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving on 127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
