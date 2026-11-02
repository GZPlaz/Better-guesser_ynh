#!/usr/bin/env python3
"""Better Guesser: a tiny self-hosted geography guessing game. Python stdlib only."""
import datetime, hashlib, hmac, json, math, os, random, re, secrets, sqlite3, sys, threading, time, traceback
import urllib.error, urllib.parse, urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("BG_DB", "game.db")
PORT = int(os.environ.get("BG_PORT", "8095"))
HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE = f"bg_session_{PORT}"  # per-instance, so two installs on one domain don't clash
USERNAME_RE = r"[A-Za-z0-9_.-]{3,20}"
RANK_NAMES = ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Master"]
START_MMR = 1500  # schema default only; the live value is cfg("start_mmr")

# Every game rule the admin panel can change: key -> (default, min, max, group, label, help).
# The default's type (bool/int/float) is the setting's type.
CONFIG = {
    "rounds": (5, 1, 20, "Games", "Rounds per game", "New games and lobbies use this; games in progress keep theirs."),
    "max_round_score": (1000, 100, 100_000, "Games", "Max points per round", "A perfect guess scores this."),
    "score_falloff_km": (1200, 50, 20_000, "Games", "Score falloff (km)", "Bigger = more forgiving. At this distance a guess keeps ~37% of the max."),
    "rank_silver": (100, 1, 1_000_000, "Ranks", "Silver at (RP)", "Rank thresholds must go up from Silver to Master."),
    "rank_gold": (200, 1, 1_000_000, "Ranks", "Gold at (RP)", ""),
    "rank_platinum": (300, 1, 1_000_000, "Ranks", "Platinum at (RP)", ""),
    "rank_diamond": (400, 1, 1_000_000, "Ranks", "Diamond at (RP)", ""),
    "rank_master": (500, 1, 1_000_000, "Ranks", "Master at (RP)", "Reaching Master unlocks Legend Division."),
    "rp_k": (40, 1, 500, "Ranked RP", "RP swing (K)", "An even match moves K/2 RP; upsets move up to K."),
    "rp_scale": (200, 10, 10_000, "Ranked RP", "RP gap scale", "At this RP gap the higher-ranked player is a 10-to-1 favourite. Smaller = rank gaps matter more."),
    "rp_cap": (60, 1, 1000, "Ranked RP", "Max RP per match", ""),
    "rp_min": (5, 0, 1000, "Ranked RP", "Min RP per win/loss", "A win always pays at least this; a loss always costs at least this."),
    "blowout": (2500, 100, 1_000_000, "Ranked RP", "Blowout margin (points)", "Winning by this many points doubles the rating swing."),
    "elo_k": (32, 1, 500, "Legend MMR", "MMR swing (K)", ""),
    "elo_scale": (400, 10, 10_000, "Legend MMR", "MMR gap scale", ""),
    "start_mmr": (1500, 0, 10_000, "Legend MMR", "Starting MMR", "Given when a player enters Legend Division."),
    "quests_per_day": (3, 1, 12, "Daily quests", "Quests per day", "Everyone gets the same quests each day (UTC); new ones at midnight."),
    "quest_rp_easy": (10, 0, 10_000, "Daily quests", "Reward: easy quest (RP)", "Legends get a fifth of this as MMR."),
    "quest_rp_medium": (20, 0, 10_000, "Daily quests", "Reward: medium quest (RP)", ""),
    "quest_rp_hard": (35, 0, 10_000, "Daily quests", "Reward: hard quest (RP)", ""),
    "match_ttl_hours": (24, 1, 24 * 30, "Matches", "Match expiry (hours)", "Unfinished matchmade games are resolved after this: no-shows forfeit."),
    "lobby_max": (20, 2, 200, "Matches", "Max players in an old free-for-all lobby", ""),
    "login_max_fails": (10, 1, 1000, "Security", "Failed logins before lockout", "Per IP address."),
    "login_window_min": (15, 1, 1440, "Security", "Lockout window (minutes)", ""),
    "session_days": (30, 1, 365, "Security", "Stay logged in (days)", ""),
    "report_daily_limit": (10, 1, 1000, "Security", "Reports per player per day", ""),
    "city_chance": (85, 0, 100, "Locations", "City rounds (%)", "Chance a round lands in a city centre. The rest land out in the countryside around it."),
    "city_radius_km": (4, 1, 100, "Locations", "City spread (km)", "City rounds land within this distance of an area's centre."),
    "countryside_radius_km": (40, 1, 500, "Locations", "Countryside spread (km)", "Countryside rounds land within this distance of an area's centre."),
    "mode_ranked": (True, None, None, "Modes & features", "Ranked duels", ""),
    "mode_legend": (True, None, None, "Modes & features", "Legend Division", "Off: Master players can't enter (existing legends keep playing)."),
    "mode_1v1": (True, None, None, "Modes & features", "Duel matchmaking", ""),
    "mode_2v2": (True, None, None, "Modes & features", "Team duel matchmaking", ""),
    "mode_private": (True, None, None, "Modes & features", "Private lobbies", ""),
    "mode_practice": (True, None, None, "Modes & features", "Practice", ""),
    "quests_enabled": (True, None, None, "Modes & features", "Daily quests", ""),
    "reports_enabled": (True, None, None, "Modes & features", "Player reports", ""),
    "maintenance": (False, None, None, "Modes & features", "Maintenance mode", "Only admins can start new games. Games in progress can finish."),
}
REPORT_REASONS = {"cheating": "Cheating", "name": "Offensive username", "abuse": "Harassment or abuse", "other": "Other"}
SEATS = {"ranked": 2, "legend": 2, "1v1": 2, "2v2": 4}  # players per match; private lobbies are full by the time they start

# City centres with good Google Street View coverage. Most rounds land in town (see city_chance); some in the countryside around it.
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
    # UK & Ireland
    (52.486, -1.890), (55.864, -4.252), (53.408, -2.991), (53.801, -1.549), (51.454, -2.588), (51.481, -3.179), (54.597, -5.930),
    (51.899, -8.475),
    # France, Benelux
    (45.764, 4.836), (43.605, 1.444), (44.838, -0.579), (47.218, -1.554), (50.629, 3.057), (48.573, 7.752), (43.710, 7.262),
    (43.611, 3.877), (48.117, -1.678), (51.924, 4.478), (52.091, 5.122), (52.070, 4.300), (51.442, 5.470), (51.219, 4.402),
    (51.054, 3.725), (49.612, 6.130),
    # Germany, Switzerland, Austria
    (53.551, 9.994), (48.137, 11.576), (50.938, 6.960), (50.110, 8.682), (48.776, 9.183), (51.228, 6.774), (51.340, 12.375),
    (51.050, 13.738), (49.452, 11.077), (52.376, 9.732), (53.079, 8.802), (46.204, 6.143), (47.560, 7.589), (46.948, 7.447),
    (47.071, 15.439), (47.809, 13.055),
    # Iberia, Italy
    (39.470, -0.376), (37.389, -5.984), (43.263, -2.935), (36.721, -4.421), (41.649, -0.889), (41.158, -8.629), (45.070, 7.687),
    (40.852, 14.268), (43.770, 11.256), (44.494, 11.343), (38.116, 13.361), (44.405, 8.946),
    # Nordics, central & eastern Europe, Turkey, Caucasus
    (57.709, 11.975), (55.605, 13.004), (60.391, 5.322), (56.163, 10.204), (61.498, 23.761), (63.431, 10.395), (50.065, 19.945),
    (51.108, 17.039), (54.352, 18.646), (52.406, 16.925), (49.195, 16.608), (48.149, 17.107), (44.787, 20.457), (43.856, 18.413),
    (46.771, 23.624), (50.450, 30.524), (49.839, 24.030), (40.640, 22.944), (40.179, 44.499), (39.933, 32.860), (38.423, 27.143),
    # USA, Canada, Mexico
    (42.360, -71.059), (39.953, -75.165), (38.907, -77.037), (33.749, -84.388), (28.538, -81.379), (27.951, -82.457),
    (32.777, -96.797), (30.267, -97.743), (29.424, -98.494), (33.448, -112.074), (36.170, -115.140), (32.716, -117.161),
    (45.515, -122.679), (40.761, -111.891), (44.978, -93.265), (42.331, -83.046), (40.441, -79.996), (36.163, -86.781),
    (29.951, -90.072), (39.100, -94.579), (38.627, -90.199), (35.227, -80.843), (39.290, -76.612), (21.307, -157.858),
    (61.218, -149.900), (39.961, -82.999), (39.768, -86.158), (38.582, -121.494), (51.045, -114.058), (53.546, -113.494),
    (45.421, -75.697), (46.813, -71.208), (49.895, -97.138), (44.649, -63.575), (25.686, -100.316), (19.041, -98.206),
    (20.967, -89.624),
    # Central & South America
    (6.244, -75.581), (3.452, -76.532), (14.634, -90.506), (9.928, -84.091), (8.983, -79.517), (18.486, -69.931),
    (-34.901, -56.164), (-25.264, -57.576), (-16.500, -68.150), (-19.917, -43.935), (-15.794, -47.882), (-30.035, -51.218),
    (-25.429, -49.271), (-8.048, -34.877), (-12.977, -38.501), (-3.732, -38.527), (-31.420, -64.188), (-32.944, -60.651),
    (-33.047, -71.613), (-2.170, -79.922),
    # East & Southeast Asia
    (35.012, 135.768), (35.181, 136.906), (33.590, 130.402), (43.062, 141.354), (35.444, 139.638), (35.180, 129.075),
    (35.871, 128.601), (24.148, 120.674), (22.627, 120.301), (22.319, 114.169), (10.823, 106.630), (18.788, 98.985),
    (11.556, 104.928), (5.414, 100.329), (10.316, 123.885), (-6.917, 107.619), (-7.257, 112.752), (-8.650, 115.216),
    (47.886, 106.906),
    # South & West Asia, Central Asia
    (27.717, 85.324), (23.810, 90.413), (13.083, 80.271), (22.573, 88.364), (17.385, 78.487), (18.520, 73.857),
    (23.023, 72.571), (31.768, 35.214), (31.954, 35.911), (25.285, 51.531), (24.454, 54.377), (42.875, 74.570),
    # Oceania
    (-34.929, 138.601), (-35.281, 149.129), (-42.882, 147.327), (-28.017, 153.400), (-43.532, 172.637), (-12.463, 130.842),
    # Africa
    (-29.858, 31.022), (-25.747, 28.229), (-33.961, 25.615), (-22.560, 17.083), (-24.628, 25.923), (0.348, 32.582),
    (-1.944, 30.062), (14.716, -17.467), (36.806, 10.181),
    (-6.792, 39.208), (-4.043, 39.668),
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
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  target_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, reason TEXT NOT NULL, details TEXT, lobby_code TEXT,
  created REAL, status TEXT NOT NULL DEFAULT 'open', resolved_by TEXT, resolved_at REAL);
CREATE TABLE IF NOT EXISTS admin_log(id INTEGER PRIMARY KEY, ts REAL, admin TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS quest_claims(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, day TEXT NOT NULL,
  quest TEXT NOT NULL, reward REAL, PRIMARY KEY(user_id, day, quest));
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
    ("lobby_members", "result", "REAL"),  # v0.5: 1 win, 0.5 draw, 0 loss
    # v0.5: cheats became admin-only; clear any that were handed to regular players
    ("users", "cheats_admin_only", "INTEGER NOT NULL DEFAULT 1",
     "UPDATE users SET cheat_reveal=0, cheat_aim=0, cheat_shield=0, cheat_floor=0, cheat_multiplier=1 WHERE is_admin=0"),
    ("games", "rounds", "INTEGER NOT NULL DEFAULT 5"),  # v0.6: rounds are configurable, fixed per game/lobby
    ("lobbies", "rounds", "INTEGER NOT NULL DEFAULT 5"),
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


def cfg(key):
    default, lo, hi = CONFIG[key][:3]
    raw = setting("cfg_" + key, None)
    if raw is None:
        return default
    return raw == "1" if isinstance(default, bool) else type(default)(raw)


def parse_cfg(key, v):
    default, lo, hi, *_ = CONFIG[key]
    if isinstance(default, bool):
        return "1" if v in (True, 1, "1", "true", "on") else "0"
    try:
        v = type(default)(v)
    except (TypeError, ValueError):
        raise Err(400, f"{CONFIG[key][4]}: not a number")
    if not (lo <= v <= hi and math.isfinite(v)):
        raise Err(400, f"{CONFIG[key][4]} must be between {lo} and {hi}")
    return str(v)


def ranks():
    return [(0, "Bronze")] + [(cfg("rank_" + n.lower()), n) for n in RANK_NAMES[1:]]


def legend_rp():
    return cfg("rank_master")


def seeds():
    """Areas rounds are drawn around. Admin-editable; falls back to the built-in world list."""
    raw = setting("seeds", "")
    return [tuple(x) for x in json.loads(raw)] if raw else SEEDS


def audit(c, admin, action, detail=""):
    c.execute("INSERT INTO admin_log(ts,admin,action,detail) VALUES(?,?,?,?)", (time.time(), admin["username"], action, str(detail)[:500]))


def require(flag, what):
    if not cfg(flag):
        raise Err(403, f"{what} is turned off by the admin")


def playable(u):
    if cfg("maintenance") and not u["is_admin"]:
        raise Err(503, "The game is in maintenance. Check back soon!")


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
    return [name for floor, name in ranks() if rp >= floor][-1]


CHEAT_OFF = {"cheat_reveal": 0, "cheat_aim": 0, "cheat_shield": 0, "cheat_floor": 0, "cheat_multiplier": 1}


def cheat(u, key):
    """Cheats are an admin perk: for anyone else they're always off, whatever the database says."""
    return u[key] if u["is_admin"] else CHEAT_OFF[key]


def valid_password(pw):
    if not isinstance(pw, str) or not 6 <= len(pw) <= 200:
        raise Err(400, "Password must be 6-200 characters")
    return pw


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def score_for(km):
    return round(cfg("max_round_score") * math.exp(-km / cfg("score_falloff_km")))  # defaults: 1000 spot on, ~430 at 1000 km


def pick_point():
    """A random spot to look for imagery: usually a city centre, sometimes the countryside around it.
    Returns (lat, lng, search radius in metres): cities search tightly so we stay in town."""
    lat, lng = random.choice(seeds())
    city = random.random() * 100 < cfg("city_chance")
    km = cfg("city_radius_km" if city else "countryside_radius_km") * math.sqrt(random.random())  # uniform over the disc
    ang = random.uniform(0, 2 * math.pi)
    lat = max(-85, min(85, lat + km / 111 * math.cos(ang)))
    lng = (lng + km / (111 * max(0.1, math.cos(math.radians(lat)))) * math.sin(ang) + 180) % 360 - 180
    return lat, lng, 800 if city else 5000


def pick_location(key):
    """Return (pano_id, lat, lng) of an official Google Street View panorama near a random area.
    Uses the Street View metadata endpoint, which Google doesn't bill."""
    for _ in range(15):
        lat, lng, radius_m = pick_point()
        q = urllib.parse.urlencode({"location": f"{lat:.6f},{lng:.6f}", "radius": radius_m, "source": "outdoor", "key": key})
        try:
            with urllib.request.urlopen(f"https://maps.googleapis.com/maps/api/streetview/metadata?{q}", timeout=10) as r:
                meta = json.load(r)
        except (OSError, ValueError):
            continue
        st = meta.get("status")
        if st in ("REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST"):
            raise Err(502, f"Google Street View said {st}: {meta.get('error_message', 'check the API key in Admin → Settings')}")
        # skip user-uploaded photo spheres: they're often indoors or mislocated
        if st == "OK" and "Google" in meta.get("copyright", ""):
            return meta["pano_id"], meta["location"]["lat"], meta["location"]["lng"]
    raise Err(502, "Could not find Street View imagery, try again")


def browser_key():
    return setting("google_key")


def server_key():
    return setting("google_server_key") or browser_key()


def need_key():
    if not browser_key():
        raise Err(503, "The admin has not set a Google Maps API key yet")


# ---------- API handlers: fn(handler, body, *url_groups) -> dict | (dict, set_cookie) ----------

def new_session(h, uid):
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with db() as c:
        c.execute("DELETE FROM sessions WHERE expires<?", (now,))
        c.execute("INSERT INTO sessions VALUES(?,?,?)", (tok, uid, now + cfg("session_days") * 86400))
        c.execute("UPDATE users SET last_login=?, last_ip=? WHERE id=?", (now, h.client_ip(), uid))
    secure = "; Secure" if h.headers.get("X-Forwarded-Proto") == "https" else ""
    return {"ok": True}, f"{COOKIE}={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={cfg('session_days') * 86400}{secure}"


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


_fails, _fails_lock = {}, threading.Lock()  # shortcut: in-memory, resets on restart; fine for a single process


def login_throttle(ip, failed=False):
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if t > now - cfg("login_window_min") * 60] + ([now] if failed else [])
        _fails[ip] = recent
    if not failed and len(recent) >= cfg("login_max_fails"):
        raise Err(429, f"Too many failed logins. Try again in {cfg('login_window_min')} minutes.")


def api_login(h, b):
    login_throttle(h.client_ip())
    u = db().execute("SELECT * FROM users WHERE username=?", (str(b.get("username")),)).fetchone()
    if not u or not check_pw(str(b.get("password")), u["pw"]):
        login_throttle(h.client_ip(), failed=True)
        raise Err(401, "Wrong username or password")
    if u["banned"]:
        raise Err(403, "This account is banned")
    return new_session(h, u["id"])


def api_password(h, b):
    u = h.user()
    if not check_pw(str(b.get("old")), u["pw"]):
        raise Err(403, "Current password is wrong")
    with db() as c:
        c.execute("UPDATE users SET pw=? WHERE id=?", (hash_pw(valid_password(b.get("new"))), u["id"]))
        c.execute("DELETE FROM sessions WHERE user_id=? AND token!=?", (u["id"], h.token()))  # log out other devices
    return {"ok": True}


def api_logout(h, b):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (h.token(),))
    return {"ok": True}, f"{COOKIE}=; Path=/; Max-Age=0"


def api_me(h, b):
    u = h.user()
    rk = ranks()
    nxt = next((floor for floor, _ in rk if floor > u["rank_points"]), None)
    floor = [f for f, _ in rk if f <= u["rank_points"]][-1]
    return {"username": u["username"], "is_admin": bool(u["is_admin"]),
            "cheat_reveal": bool(cheat(u, "cheat_reveal")), "cheat_aim": bool(cheat(u, "cheat_aim")),
            "open_reports": db().execute("SELECT COUNT(*) FROM reports WHERE status='open'").fetchone()[0] if u["is_admin"] else 0,
            "quests_claimable": sum(q["done"] and not q["claimed"] for q in quest_state(u)) if cfg("quests_enabled") else 0,
            "rank": rank_name(u["rank_points"]), "rank_points": u["rank_points"], "next_rank_at": nxt, "rank_floor": floor,
            "legend": bool(u["legend"]), "legend_eligible": u["rank_points"] >= legend_rp() and cfg("mode_legend"),
            "mmr": round(u["mmr"]), "server": status()}


def status():
    return {"announcement": setting("announcement"), "maintenance": cfg("maintenance"), "rounds": cfg("rounds"), "max_round_score": cfg("max_round_score"), "registration_open": setting("registration_open") == "1",
            "modes": {k[5:]: cfg(k) for k in CONFIG if k.startswith("mode_")},
            "quests_enabled": cfg("quests_enabled"), "reports_enabled": cfg("reports_enabled")}


def api_status(h, b):
    """Public: what the login page needs to know."""
    return status()


def game_state(u, gid):
    c = db()
    g = c.execute("SELECT * FROM games WHERE id=? AND user_id=?", (gid, u["id"])).fetchone()
    if not g:
        raise Err(404, "No such game")
    played = c.execute("""SELECT n, score, distance, lat, lng, guess_lat, guess_lng FROM rounds
        WHERE game_id=? AND score IS NOT NULL ORDER BY n""", (gid,)).fetchall()  # only finished rounds, so no answers leak
    lb = c.execute("SELECT code, mode FROM lobbies WHERE id=?", (g["lobby_id"],)).fetchone() if g["lobby_id"] else None
    state = {"id": g["id"], "rounds": g["rounds"], "lobby": lb["code"] if lb else None, "mode": lb["mode"] if lb else "solo", "total": g["total"], "done": bool(g["done"]),
             "history": [dict(r) for r in played], "key": browser_key()}
    if g["done"]:
        return state
    n = len(played) + 1
    cur = c.execute("SELECT image_id FROM rounds WHERE game_id=? AND n=?", (gid, n)).fetchone()
    if not cur:
        # network call, outside any transaction; lobby games share one set of locations
        image_id, lat, lng = lobby_location(c, g["lobby_id"], n, server_key()) if g["lobby_id"] else pick_location(server_key())
        with c:
            c.execute("INSERT OR IGNORE INTO rounds(game_id,n,image_id,lat,lng) VALUES(?,?,?,?,?)", (gid, n, image_id, lat, lng))
        cur = c.execute("SELECT image_id FROM rounds WHERE game_id=? AND n=?", (gid, n)).fetchone()
    return {**state, "round": n, "image_id": cur["image_id"]}


def lobby_location(c, lobby_id, n, key):
    q = "SELECT image_id, lat, lng FROM lobby_rounds WHERE lobby_id=? AND n=?"
    if not c.execute(q, (lobby_id, n)).fetchone():
        loc = pick_location(key)
        with c:
            c.execute("INSERT OR IGNORE INTO lobby_rounds VALUES(?,?,?,?,?)", (lobby_id, n, *loc))
    return tuple(c.execute(q, (lobby_id, n)).fetchone())


def api_new_game(h, b):
    u = h.user()
    require("mode_practice", "Practice")
    playable(u)
    need_key()
    with db() as c:
        gid = c.execute("INSERT INTO games(user_id, started, rounds) VALUES(?,?,?)", (u["id"], time.time(), cfg("rounds"))).lastrowid
    return game_state(u, gid)


def api_game(h, b, gid):
    return game_state(h.user(), int(gid))


def open_round(u, gid):
    r = db().execute("SELECT r.*, g.rounds FROM rounds r JOIN games g ON g.id=r.game_id WHERE g.id=? AND g.user_id=? AND r.score IS NULL",
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
    s = round(max(score_for(km), cheat(u, "cheat_floor")) * cheat(u, "cheat_multiplier"))  # cheats apply in every mode
    done = r["n"] >= r["rounds"]
    with db() as c:
        if c.execute("UPDATE rounds SET guess_lat=?, guess_lng=?, distance=?, score=? WHERE game_id=? AND n=? AND score IS NULL",
                     (lat, lng, km, s, r["game_id"], r["n"])).rowcount != 1:
            raise Err(409, "Already guessed")
        c.execute("UPDATE games SET total=total+?, done=? WHERE id=?", (s, int(done), r["game_id"]))
        total = c.execute("SELECT total FROM games WHERE id=?", (r["game_id"],)).fetchone()["total"]
        lobby_id = c.execute("SELECT lobby_id FROM games WHERE id=?", (r["game_id"],)).fetchone()["lobby_id"]
        if done and lobby_id:
            settle(c, lobby_id)
    return {"distance_km": km, "score": s, "total": total, "done": done,
            "answer": {"lat": r["lat"], "lng": r["lng"]}, "guess": {"lat": lat, "lng": lng}}


def rating_delta(mode, res, me, opp, margin, shield):
    """Dynamic RP/MMR change. Elo-style expected result from the rating gap, scaled up by the points margin.
    res: 1 win / 0.5 draw / 0 loss. me/opp: users rows. margin: 0..1 (1 = blowout)."""
    legend = mode == "legend"
    mine, theirs = (me["mmr"], opp["mmr"]) if legend else (me["rank_points"], opp["rank_points"])
    expected = 1 / (1 + 10 ** ((theirs - mine) / cfg("elo_scale" if legend else "rp_scale")))
    delta = cfg("elo_k" if legend else "rp_k") * (1 + margin) * (res - expected)
    if not legend:
        cap, least = cfg("rp_cap"), min(cfg("rp_min"), cfg("rp_cap"))
        delta = round(max(-cap, min(cap, delta)))
        delta = max(delta, least) if res == 1 else min(delta, -least) if res == 0 else delta  # a result always moves you a bit
        delta = max(delta, -me["rank_points"])  # RP never goes below 0
    if shield:
        delta = max(delta, 0)
    return round(delta, 1)


def settle(c, lobby_id, force=False):
    """Record results once every seat has finished (or, with force, resolve an expired match: no-shows forfeit).
    Ranked/legend also move RP/MMR. Runs inside the caller's transaction; settles each lobby once."""
    lb = c.execute("SELECT * FROM lobbies WHERE id=?", (lobby_id,)).fetchone()
    seats = SEATS.get(lb["mode"]) if lb else None
    if not seats or lb["settled"]:
        return
    p = c.execute("""SELECT u.*, m.team, COALESCE(g.total,0) total, COALESCE(g.done,0) done
        FROM lobby_members m JOIN users u ON u.id=m.user_id LEFT JOIN games g ON g.lobby_id=m.lobby_id AND g.user_id=m.user_id
        WHERE m.lobby_id=? ORDER BY u.id""", (lobby_id,)).fetchall()
    complete = len(p) == seats and all(x["done"] for x in p)
    if not complete and not force:
        return
    if c.execute("UPDATE lobbies SET settled=1 WHERE id=? AND settled=0", (lobby_id,)).rowcount != 1:
        return
    if len(p) < seats:
        return  # never filled up: closed without a result
    side = (lambda x: x["team"]) if lb["mode"] == "2v2" else (lambda x: x["id"])
    sides = {}
    for x in p:
        sides.setdefault(side(x), []).append(x)
    # a side with a no-show forfeits (-1); otherwise its score is the sum of its players
    score = {k: sum(x["total"] for x in v) if all(x["done"] for x in v) else -1 for k, v in sides.items()}
    for x in p:
        mine = score[side(x)]
        theirs = next(v for k, v in score.items() if k != side(x))
        res = 1 if mine > theirs else 0 if mine < theirs else 0.5
        delta = None
        if lb["mode"] in ("ranked", "legend"):
            opp = next(y for y in p if y["id"] != x["id"])
            margin = min(abs(x["total"] - opp["total"]) / cfg("blowout"), 1)
            delta = rating_delta(lb["mode"], res, x, opp, margin, cheat(x, "cheat_shield"))
            col = "mmr" if lb["mode"] == "legend" else "rank_points"
            c.execute(f"UPDATE users SET {col}={col}+? WHERE id=?", (delta, x["id"]))
        c.execute("UPDATE lobby_members SET result=?, delta=? WHERE lobby_id=? AND user_id=?", (res, delta, lobby_id, x["id"]))


def match_ttl():
    return cfg("match_ttl_hours") * 3600


def expire_stale(c):
    for (lid,) in c.execute("SELECT id FROM lobbies WHERE matchmade=1 AND settled=0 AND created<?", (time.time() - match_ttl(),)).fetchall():
        settle(c, lid, force=True)


def api_queue(h, b):
    """Matchmaking for ranked, 1v1 and 2v2. Async: you play now, and the next players in the same queue play the same 5 places."""
    u = h.user()
    mode = b.get("mode")
    if mode not in ("ranked", "1v1", "2v2"):
        raise Err(400, "Mode must be ranked, 1v1 or 2v2")
    require("mode_" + mode, {"ranked": "Ranked", "1v1": "Duel matchmaking", "2v2": "Team duel matchmaking"}[mode])
    playable(u)
    need_key()
    if mode == "ranked" and u["legend"]:
        mode = "legend"
    # ranked pairs the closest skill; casual queues just take the oldest open match
    skill, my_skill = {"ranked": ("hu.rank_points", u["rank_points"]), "legend": ("hu.mmr", u["mmr"])}.get(mode, ("0", 0))
    c = db()
    with c:
        c.execute("BEGIN IMMEDIATE")  # one matchmaker at a time, so two players can't grab the same last seat
        expire_stale(c)
        mine = c.execute("""SELECT l.code FROM lobbies l JOIN lobby_members m ON m.lobby_id=l.id AND m.user_id=?
            LEFT JOIN games g ON g.lobby_id=l.id AND g.user_id=m.user_id
            WHERE l.mode=? AND l.matchmade=1 AND l.settled=0 AND COALESCE(g.done,0)=0 ORDER BY l.id LIMIT 1""", (u["id"], mode)).fetchone()
        if mine:
            return {"code": mine["code"]}  # finish your current match first
        open_ = c.execute(f"""SELECT l.id, l.code FROM lobbies l JOIN users hu ON hu.id=l.host_id
            WHERE l.mode=? AND l.matchmade=1 AND l.settled=0 AND hu.banned=0
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


# key: (title, tier, target, mode setting it needs). {n} is the target; {pts} is the points goal.
QUESTS = {
    "finish_games": ("Finish {n} games", "easy", 3, None),
    "rounds_played": ("Play {n} rounds", "easy", 10, None),
    "close_guesses": ("Land {n} guesses within 50 km", "easy", 3, None),
    "duel": ("Finish a duel", "easy", 1, "mode_1v1"),
    "team_duel": ("Finish a team duel", "easy", 1, "mode_2v2"),
    "near_perfect": ("Score a near-perfect round (99%+)", "medium", 1, None),
    "points_today": ("Earn {pts} points today", "medium", 10, None),  # target is in max-round-scores
    "win_match": ("Win a match", "medium", 1, None),
    "play_ranked": ("Finish {n} ranked matches", "medium", 2, "mode_ranked"),
    "big_game": ("Score 80%+ of the max in one game", "hard", 1, None),
    "win_two": ("Win {n} matches", "hard", 2, None),
    "win_ranked": ("Win a ranked match", "hard", 1, "mode_ranked"),
}
TIER_ORDER = {"easy": 0, "medium": 1, "hard": 2}


def todays_quests(day):
    """Same quests for everyone on a given UTC day, skipping ones for modes the admin turned off."""
    pool = [k for k, q in QUESTS.items() if not q[3] or cfg(q[3])]
    picked = random.Random(f"quests-{day.isoformat()}").sample(pool, min(cfg("quests_per_day"), len(pool)))
    return sorted(picked, key=lambda k: (TIER_ORDER[QUESTS[k][1]], k))


def quest_reward(tier, legend):
    rp = cfg("quest_rp_" + tier)
    return (max(1, round(rp / 5)) if rp else 0, "MMR") if legend else (rp, "RP")


def quest_state(u, day=None):
    day = day or utc_today()
    start = datetime.datetime.combine(day, datetime.time(), datetime.timezone.utc).timestamp()
    c, span = db(), (u["id"], start, start + 86400)
    games = c.execute("""SELECT g.total, g.done, g.rounds, l.mode FROM games g LEFT JOIN lobbies l ON l.id=g.lobby_id
        WHERE g.user_id=? AND g.started>=? AND g.started<?""", span).fetchall()
    rounds = c.execute("""SELECT r.score, r.distance FROM rounds r JOIN games g ON g.id=r.game_id
        WHERE g.user_id=? AND g.started>=? AND g.started<? AND r.score IS NOT NULL""", span).fetchall()
    results = c.execute("""SELECT l.mode, m.result FROM lobby_members m JOIN lobbies l ON l.id=m.lobby_id
        JOIN games g ON g.lobby_id=l.id AND g.user_id=m.user_id
        WHERE m.user_id=? AND g.started>=? AND g.started<? AND m.result IS NOT NULL""", span).fetchall()
    claimed = {r["quest"] for r in c.execute("SELECT quest FROM quest_claims WHERE user_id=? AND day=?", (u["id"], day.isoformat()))}
    top = cfg("max_round_score")
    done = [g for g in games if g["done"]]
    rated = ("ranked", "legend")
    progress = {
        "finish_games": len(done),
        "rounds_played": len(rounds),
        "close_guesses": sum(r["distance"] < 50 for r in rounds),
        "duel": sum(g["mode"] == "1v1" for g in done),
        "team_duel": sum(g["mode"] == "2v2" for g in done),
        "near_perfect": sum(r["score"] >= 0.99 * top for r in rounds),
        "points_today": sum(g["total"] for g in games),
        "win_match": sum(r["result"] == 1 for r in results),
        "play_ranked": sum(g["mode"] in rated for g in done),
        "big_game": sum(g["total"] >= 0.8 * g["rounds"] * top for g in done),
        "win_two": sum(r["result"] == 1 for r in results),
        "win_ranked": sum(r["result"] == 1 and r["mode"] in rated for r in results),
    }
    out = []
    for k in todays_quests(day):
        title, tier, target, _ = QUESTS[k]
        if k == "points_today":
            target *= top
        amount, unit = quest_reward(tier, u["legend"])
        out.append({"key": k, "title": title.format(n=target, pts=f"{target:,}"), "tier": tier, "target": target,
                    "progress": min(progress[k], target), "done": progress[k] >= target, "claimed": k in claimed,
                    "reward": amount, "unit": unit})
    return out


def api_quests(h, b):
    u = h.user()
    require("quests_enabled", "Daily quests")
    now = datetime.datetime.now(datetime.timezone.utc)
    midnight = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(), datetime.timezone.utc)
    return {"quests": quest_state(u), "resets_in": int((midnight - now).total_seconds())}


def api_quest_claim(h, b):
    u, day = h.user(), utc_today()
    require("quests_enabled", "Daily quests")
    q = next((q for q in quest_state(u, day) if q["key"] == b.get("key")), None)
    if not q:
        raise Err(404, "That's not one of today's quests")
    if not q["done"]:
        raise Err(409, "Finish the quest first")
    col = "mmr" if u["legend"] else "rank_points"
    try:
        with db() as c:
            c.execute("INSERT INTO quest_claims VALUES(?,?,?,?)", (u["id"], day.isoformat(), q["key"], q["reward"]))
            c.execute(f"UPDATE users SET {col}={col}+? WHERE id=?", (q["reward"], u["id"]))
    except sqlite3.IntegrityError:
        raise Err(409, "Already claimed")
    return {"reward": q["reward"], "unit": q["unit"]}


def api_legend_enter(h, b):
    u = h.user()
    require("mode_legend", "Legend Division")
    if u["rank_points"] < legend_rp():
        raise Err(403, f"Reach Master ({legend_rp()} RP) to enter Legend Division")
    with db() as c:
        c.execute("UPDATE users SET legend=1, mmr=? WHERE id=? AND legend=0", (cfg("start_mmr"), u["id"]))
    return {"ok": True}


def api_legends(h, b):
    """Public: no login needed."""
    rows = db().execute("""SELECT u.username, ROUND(u.mmr) mmr,
        (SELECT COUNT(*) FROM lobby_members m JOIN lobbies l ON l.id=m.lobby_id WHERE m.user_id=u.id AND l.mode='legend' AND l.settled=1) matches
        FROM users u WHERE u.legend=1 AND u.banned=0 ORDER BY u.mmr DESC, u.username LIMIT 100""").fetchall()
    return {"rows": [dict(r) for r in rows]}


def api_peek(h, b, gid):
    u = h.user()
    if not (cheat(u, "cheat_reveal") or cheat(u, "cheat_aim")):
        raise Err(403, "Nice try")
    r = open_round(u, gid)
    return {"lat": r["lat"], "lng": r["lng"]}


def api_leaderboard(h, b):
    h.user()
    rows = db().execute("""SELECT u.username, COUNT(g.id) games, COALESCE(MAX(g.total),0) best, COALESCE(SUM(g.total),0) points
        FROM users u LEFT JOIN games g ON g.user_id=u.id AND g.done=1 WHERE u.banned=0
        GROUP BY u.id ORDER BY best DESC, points DESC LIMIT 50""").fetchall()
    return {"rows": [dict(r) for r in rows]}


def api_ranked_board(h, b):
    """Public: top ranked players below Legend."""
    rows = db().execute("""SELECT username, rank_points FROM users WHERE legend=0 AND banned=0 AND rank_points>0
        ORDER BY rank_points DESC, username LIMIT 100""").fetchall()
    return {"rows": [{"username": r["username"], "rank_points": r["rank_points"], "rank": rank_name(r["rank_points"])} for r in rows]}


def record(rows):
    return {"w": sum(r["result"] == 1 for r in rows), "l": sum(r["result"] == 0 for r in rows), "d": sum(r["result"] == 0.5 for r in rows)}


def api_profile(h, b, name):
    h.user()
    c = db()
    u = c.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    if not u:
        raise Err(404, "No such player")
    g = c.execute("SELECT COUNT(*) n, MAX(total) best, AVG(total) avg FROM games WHERE user_id=? AND done=1", (u["id"],)).fetchone()
    rnd = c.execute("""SELECT AVG(r.distance) dist, SUM(r.score >= 990) perfect FROM rounds r JOIN games g ON g.id=r.game_id
        WHERE g.user_id=? AND r.score IS NOT NULL""", (u["id"],)).fetchone()
    matches = c.execute("""SELECT l.code, l.mode, l.created, m.result, m.delta, COALESCE(g.total,0) total FROM lobby_members m
        JOIN lobbies l ON l.id=m.lobby_id LEFT JOIN games g ON g.lobby_id=l.id AND g.user_id=m.user_id
        WHERE m.user_id=? AND m.result IS NOT NULL ORDER BY l.id DESC""", (u["id"],)).fetchall()
    ranked = [m for m in matches if m["mode"] in ("ranked", "legend")]
    return {"username": u["username"], "joined": u["created"], "banned": bool(u["banned"]),
            "rank": "Legend" if u["legend"] else rank_name(u["rank_points"]), "rank_points": u["rank_points"],
            "legend": bool(u["legend"]), "mmr": round(u["mmr"]),
            "games": g["n"], "best": g["best"] or 0, "avg": round(g["avg"] or 0),
            "avg_km": round(rnd["dist"] or 0), "perfect_rounds": rnd["perfect"] or 0,
            "ranked_record": record(ranked), "casual_record": record([m for m in matches if m not in ranked]),
            "recent": [dict(m) for m in matches[:15]]}


def api_report(h, b):
    u = h.user()
    require("reports_enabled", "Reporting")
    t = db().execute("SELECT id, username FROM users WHERE username=?", (str(b.get("username")),)).fetchone()
    if not t:
        raise Err(404, "No such player")
    if t["id"] == u["id"]:
        raise Err(400, "You can't report yourself")
    if b.get("reason") not in REPORT_REASONS:
        raise Err(400, "Pick a reason")
    details = str(b.get("details") or "").strip()[:500]
    c = db()
    if c.execute("SELECT 1 FROM reports WHERE reporter_id=? AND target_id=? AND status='open'", (u["id"], t["id"])).fetchone():
        raise Err(409, f"You already have an open report on {t['username']}")
    if c.execute("SELECT COUNT(*) FROM reports WHERE reporter_id=? AND created>?", (u["id"], time.time() - 86400)).fetchone()[0] >= cfg("report_daily_limit"):
        raise Err(429, "You've sent a lot of reports today. Try again tomorrow.")
    with c:
        c.execute("INSERT INTO reports(reporter_id,target_id,reason,details,lobby_code,created) VALUES(?,?,?,?,?,?)",
                  (u["id"], t["id"], b["reason"], details, str(b.get("lobby") or "")[:6] or None, time.time()))
    return {"ok": True}


def api_admin_reports(h, b):
    h.user(admin=True)
    rows = db().execute("""SELECT r.*, rep.username reporter, t.username target, t.banned target_banned,
        (SELECT COUNT(*) FROM reports x WHERE x.target_id=r.target_id) target_reports
        FROM reports r JOIN users t ON t.id=r.target_id LEFT JOIN users rep ON rep.id=r.reporter_id
        ORDER BY r.status='open' DESC, r.id DESC LIMIT 200""").fetchall()
    return {"reports": [dict(r) | {"reason_label": REPORT_REASONS.get(r["reason"], r["reason"])} for r in rows]}


def api_admin_report(h, b):
    me = h.user(admin=True)
    action = b.get("action")
    if action not in ("resolve", "dismiss", "ban", "delete"):
        raise Err(400, "Unknown action")
    r = db().execute("SELECT * FROM reports WHERE id=?", (int(b["id"]),)).fetchone()
    if not r:
        raise Err(404, "No such report")
    with db() as c:
        audit(c, me, "report_" + action, f"report #{r['id']}")
        if action == "delete":
            c.execute("DELETE FROM reports WHERE id=?", (r["id"],))
        elif action == "ban":
            if r["target_id"] == me["id"]:
                raise Err(400, "You can't ban yourself")
            c.execute("UPDATE users SET banned=1 WHERE id=?", (r["target_id"],))
            c.execute("DELETE FROM sessions WHERE user_id=?", (r["target_id"],))
            # banning settles every open report about that player
            c.execute("""UPDATE reports SET status='resolved', resolved_by=?, resolved_at=? WHERE target_id=? AND status='open'""",
                      (me["username"], time.time(), r["target_id"]))
        else:
            c.execute("UPDATE reports SET status=?, resolved_by=?, resolved_at=? WHERE id=?",
                      ("resolved" if action == "resolve" else "dismissed", me["username"], time.time(), r["id"]))
    return {"ok": True}


def api_admin_cheats(h, b):
    """An admin's own cheat menu. Nobody can switch cheats on for someone else."""
    u = h.user(admin=True)
    if h.command == "POST":
        sets = {k: conv(b[k]) for k, conv in CHEAT_FIELDS.items() if k in b}
        if sets:
            with db() as c:
                c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), u["id"]))
            u = h.user(admin=True)
    return {k: u[k] for k in CHEAT_FIELDS}


def get_lobby(code):
    lb = db().execute("SELECT * FROM lobbies WHERE code=?", (str(code).strip().upper(),)).fetchone()
    if not lb:
        raise Err(404, "No lobby with that code")
    return lb


def lobby_state(u, code):
    c = db()
    with c:
        expire_stale(c)
    lb = get_lobby(code)
    rows = c.execute("""SELECT u.id, u.username, m.team, m.delta, m.result, g.id game_id, COALESCE(g.total,0) total, COALESCE(g.done,0) done,
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
            "rounds": lb["rounds"], "teams": teams, "my_team": me["team"], "my_done": bool(me["done"]),
            "finished": bool(lb["started"]) and all(r["done"] for r in rows) and len(rows) == SEATS.get(lb["mode"], len(rows)),
            "seats": SEATS.get(lb["mode"]),
            "matchmade": bool(lb["matchmade"]), "settled": bool(lb["settled"]), "my_result": me["result"],
            "expired": bool(lb["settled"]) and (len(rows) < SEATS.get(lb["mode"], 0) or not all(r["done"] for r in rows)),
            "members": [{k: r[k] for k in ("username", "team", "total", "played", "done", "delta", "result")} | {"host": r["id"] == lb["host_id"]} for r in rows]}


def api_lobby_create(h, b):
    u = h.user()
    mode = b.get("mode")
    if mode not in ("1v1", "2v2"):
        raise Err(400, "Mode must be 1v1 or 2v2")
    require("mode_private", "Private lobbies")
    playable(u)
    with db() as c:
        code = new_lobby(c, u["id"], mode)
    return lobby_state(u, code)


def new_lobby(c, host_id, mode, matchmade=0):
    """Private lobbies wait for the host's Start; matchmade ones start immediately."""
    for _ in range(5):
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        try:
            lid = c.execute("INSERT INTO lobbies(code,mode,host_id,started,matchmade,created,rounds) VALUES(?,?,?,?,?,?,?)",
                            (code, mode, host_id, matchmade, matchmade, time.time(), cfg("rounds"))).lastrowid
            break
        except sqlite3.IntegrityError:
            continue
    c.execute("INSERT INTO lobby_members(lobby_id,user_id,team) VALUES(?,?,?)", (lid, host_id, "A" if mode == "2v2" else None))
    return code


def api_lobby_join(h, b):
    """Join a lobby, or (2v2) switch team. Only before the host starts it."""
    u, lb = h.user(), get_lobby(b.get("code", ""))
    playable(u)
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
    elif len(others) >= (2 if lb["mode"] == "1v1" else cfg("lobby_max")):  # others excludes the joiner
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
    if not db().execute("SELECT 1 FROM games WHERE lobby_id=? AND user_id=?", (lb["id"], u["id"])).fetchone():
        playable(u)  # maintenance blocks new games, not ones already underway
    need_key()
    with db() as c:
        c.execute("INSERT OR IGNORE INTO games(user_id, started, lobby_id, rounds) VALUES(?,?,?,?)", (u["id"], time.time(), lb["id"], lb["rounds"]))
        gid = c.execute("SELECT id FROM games WHERE lobby_id=? AND user_id=?", (lb["id"], u["id"])).fetchone()["id"]
    return game_state(u, gid)


def api_my_lobbies(h, b):
    u = h.user()
    with db() as c:
        expire_stale(c)
    rows = db().execute("""SELECT l.code, l.mode, l.started, l.settled, l.matchmade, l.created, m.delta, m.result, hu.username host,
        (SELECT COUNT(*) FROM lobby_members x WHERE x.lobby_id=l.id) players
        FROM lobby_members m JOIN lobbies l ON l.id=m.lobby_id JOIN users hu ON hu.id=l.host_id
        WHERE m.user_id=? ORDER BY l.id DESC LIMIT 20""", (u["id"],)).fetchall()
    return {"lobbies": [dict(r) for r in rows]}


def api_admin_users(h, b):
    h.user(admin=True)
    rows = db().execute("""SELECT u.id, u.username, u.is_admin, u.banned,
        u.rank_points, u.legend, ROUND(u.mmr) mmr,
        (SELECT COUNT(*) FROM quest_claims q WHERE q.user_id=u.id) quests_done,
        (SELECT COUNT(*) FROM reports r WHERE r.target_id=u.id AND r.status='open') open_reports,
        u.created, u.last_login, u.last_ip, COUNT(g.id) games, SUM(g.done) finished,
        COALESCE(MAX(CASE WHEN g.done THEN g.total END),0) best, COALESCE(SUM(g.total),0) points,
        (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id AND s.expires>?) sessions
        FROM users u LEFT JOIN games g ON g.user_id=u.id GROUP BY u.id ORDER BY u.id""", (time.time(),)).fetchall()
    return {"users": [dict(r) | {"rank": "Legend" if r["legend"] else rank_name(r["rank_points"])} for r in rows],
            "ranks": [{"name": n, "rp": rp} for rp, n in ranks()], "danger": DANGER}


def api_admin_user(h, b):
    me = h.user(admin=True)
    uid, action = int(b["id"]), b.get("action")
    target = db().execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        raise Err(404, "No such user")
    self_edit = uid == me["id"]
    with db() as c:
        audit(c, me, action, f"{target['username']} " + (json.dumps(b.get("fields")) if action == "update" else ""))
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
    "username": valid_username, "is_admin": flag, "banned": flag, "legend": flag,
    "rank_points": lambda v: bounded(int(v), 0, 100_000, "RP"),
    "mmr": lambda v: bounded(float(v), 0, 10_000, "MMR"),
}
CHEAT_FIELDS = {
    "cheat_reveal": flag, "cheat_aim": flag, "cheat_shield": flag,
    "cheat_multiplier": lambda v: bounded(float(v), 0, 100, "Multiplier"),
    "cheat_floor": lambda v: bounded(int(v), 0, 1000, "Score floor"),
}


def api_admin_settings(h, b):
    me = h.user(admin=True)
    if h.command == "POST":
        for key in ("google_key", "google_server_key", "announcement"):
            if key in b:
                set_setting(key, str(b[key]).strip()[:500])
        if "registration_open" in b:
            set_setting("registration_open", "1" if b["registration_open"] else "0")
        with db() as c:
            audit(c, me, "settings", ", ".join(b))  # key names only, never the keys
    return {"google_key": browser_key(), "google_server_key": setting("google_server_key"), "registration_open": setting("registration_open") == "1",
            "announcement": setting("announcement")}


def api_admin_config(h, b):
    """All game rules, generated from CONFIG so every rule shows up in the admin panel automatically."""
    me = h.user(admin=True)
    if h.command == "POST":
        vals = b.get("values") or {}
        unknown = set(vals) - set(CONFIG)
        if unknown:
            raise Err(400, f"Unknown setting: {', '.join(sorted(unknown))}")
        parsed = {k: parse_cfg(k, v) for k, v in vals.items()}
        # rank thresholds must still climb Silver < Gold < ... < Master
        floors = [int(parsed.get(k, cfg(k))) for k in ("rank_silver", "rank_gold", "rank_platinum", "rank_diamond", "rank_master")]
        if floors != sorted(set(floors)):
            raise Err(400, "Rank thresholds must go up: Silver < Gold < Platinum < Diamond < Master")
        with db() as c:
            for k, v in parsed.items():
                c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("cfg_" + k, v))
            if parsed:
                audit(c, me, "game_rules", ", ".join(f"{k}={v}" for k, v in parsed.items()))
    return {"rules": [{"key": k, "value": cfg(k), "default": d, "min": lo, "max": hi, "group": g, "label": label, "help": hlp,
                       "type": "bool" if isinstance(d, bool) else type(d).__name__}
                      for k, (d, lo, hi, g, label, hlp) in CONFIG.items()]}


def api_admin_locations(h, b):
    me = h.user(admin=True)
    if h.command == "POST":
        if b.get("reset"):
            set_setting("seeds", "")
        else:
            pts = []
            for i, line in enumerate(str(b.get("text", "")).splitlines(), 1):
                line = line.split("#")[0].strip()
                if not line:
                    continue
                try:
                    lat, lng = (float(x) for x in line.replace(";", ",").split(",")[:2])
                except ValueError:
                    raise Err(400, f"Line {i}: write it as latitude, longitude")
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    raise Err(400, f"Line {i}: coordinates out of range")
                pts.append([round(lat, 5), round(lng, 5)])
            if not pts:
                raise Err(400, "Add at least one area")
            set_setting("seeds", json.dumps(pts))
        with db() as c:
            audit(c, me, "locations", "reset to default" if b.get("reset") else f"{len(seeds())} areas")
    return {"text": "\n".join(f"{lat}, {lng}" for lat, lng in seeds()), "count": len(seeds()), "custom": bool(setting("seeds"))}


def api_admin_matches(h, b):
    h.user(admin=True)
    c = db()
    with c:
        expire_stale(c)
    lbs = c.execute("""SELECT l.*, hu.username host FROM lobbies l LEFT JOIN users hu ON hu.id=l.host_id
        ORDER BY l.settled, l.id DESC LIMIT 300""").fetchall()
    out = []
    for lb in lbs:
        ms = c.execute("""SELECT u.username, m.team, m.delta, m.result, COALESCE(g.total,0) total, COALESCE(g.done,0) done,
            (SELECT COUNT(*) FROM rounds r WHERE r.game_id=g.id AND r.score IS NOT NULL) played
            FROM lobby_members m JOIN users u ON u.id=m.user_id LEFT JOIN games g ON g.lobby_id=m.lobby_id AND g.user_id=m.user_id
            WHERE m.lobby_id=? ORDER BY m.team, total DESC""", (lb["id"],)).fetchall()
        out.append({k: lb[k] for k in ("code", "mode", "started", "settled", "matchmade", "created", "rounds", "host")}
                   | {"seats": SEATS.get(lb["mode"]), "members": [dict(m) for m in ms]})
    return {"matches": out}


def api_admin_match(h, b):
    """end: resolve it now (no-shows forfeit). void: undo its RP/MMR changes and delete it with its games."""
    me = h.user(admin=True)
    lb = get_lobby(b.get("code", ""))
    action = b.get("action")
    with db() as c:
        if action == "end":
            settle(c, lb["id"], force=True)
        elif action == "void":
            col = "mmr" if lb["mode"] == "legend" else "rank_points"
            for m in c.execute("SELECT user_id, delta FROM lobby_members WHERE lobby_id=? AND delta IS NOT NULL", (lb["id"],)).fetchall():
                c.execute(f"UPDATE users SET {col}=MAX(0, {col}-?) WHERE id=?", (m["delta"], m["user_id"]))
            c.execute("DELETE FROM games WHERE lobby_id=?", (lb["id"],))
            c.execute("DELETE FROM lobbies WHERE id=?", (lb["id"],))
        else:
            raise Err(400, "Unknown action")
        audit(c, me, "match_" + action, lb["code"])
    return {"ok": True}


def api_admin_create(h, b):
    me = h.user(admin=True)
    name, pw = valid_username(b.get("username")), valid_password(b.get("password"))
    try:
        with db() as c:
            c.execute("INSERT INTO users(username,pw,is_admin,created) VALUES(?,?,?,?)", (name, hash_pw(pw), flag(b.get("is_admin")), time.time()))
            audit(c, me, "create", name + (" (admin)" if b.get("is_admin") else ""))
    except sqlite3.IntegrityError:
        raise Err(409, "Username taken")
    return {"ok": True}


DANGER = {
    "season_reset": "Everyone's RP back to 0, out of Legend Division, MMR back to the start value",
    "wipe_matches": "Delete every game and match (all modes), including history",
    "wipe_reports": "Delete every report",
}


def api_admin_danger(h, b):
    me = h.user(admin=True)
    action = b.get("action")
    if action not in DANGER:
        raise Err(400, "Unknown action")
    if b.get("confirm") != action:
        raise Err(400, "Type the action name to confirm")
    with db() as c:
        if action == "season_reset":
            c.execute("UPDATE users SET rank_points=0, legend=0, mmr=?", (cfg("start_mmr"),))
        elif action == "wipe_matches":
            c.execute("DELETE FROM lobbies")
            c.execute("DELETE FROM games")
        else:
            c.execute("DELETE FROM reports")
        audit(c, me, action)
    return {"ok": True}


def api_admin_log(h, b):
    h.user(admin=True)
    return {"log": [dict(r) for r in db().execute("SELECT * FROM admin_log ORDER BY id DESC LIMIT 500")]}


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
    ("GET", r"/api/status", api_status),
    ("GET", r"/api/admin/config", api_admin_config),
    ("POST", r"/api/admin/config", api_admin_config),
    ("GET", r"/api/admin/locations", api_admin_locations),
    ("POST", r"/api/admin/locations", api_admin_locations),
    ("GET", r"/api/admin/matches", api_admin_matches),
    ("POST", r"/api/admin/match", api_admin_match),
    ("POST", r"/api/admin/create", api_admin_create),
    ("POST", r"/api/admin/danger", api_admin_danger),
    ("GET", r"/api/admin/log", api_admin_log),
    ("POST", r"/api/password", api_password),
    ("GET", r"/api/ranked-board", api_ranked_board),
    ("GET", r"/api/profile/([A-Za-z0-9_.-]{3,20})", api_profile),
    ("POST", r"/api/report", api_report),
    ("GET", r"/api/admin/reports", api_admin_reports),
    ("POST", r"/api/admin/report", api_admin_report),
    ("GET", r"/api/admin/cheats", api_admin_cheats),
    ("POST", r"/api/admin/cheats", api_admin_cheats),
    ("GET", r"/api/quests", api_quests),
    ("POST", r"/api/quests/claim", api_quest_claim),
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


def cli_init(admin_name, key=""):
    """Create/refresh the admin account (password read from stdin). Used by the YunoHost install script."""
    init_db()
    pw = valid_password(sys.stdin.readline().rstrip("\n"))
    with db() as c:
        c.execute("""INSERT INTO users(username,pw,is_admin,created) VALUES(?,?,1,?)
                     ON CONFLICT(username) DO UPDATE SET pw=excluded.pw, is_admin=1, banned=0""",
                  (valid_username(admin_name), hash_pw(pw), time.time()))
    if key:
        set_setting("google_key", key)


if __name__ == "__main__":
    if sys.argv[1:2] == ["init"]:
        cli_init(*sys.argv[2:4])
        sys.exit()
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving on 127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
