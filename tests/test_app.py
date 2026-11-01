"""End-to-end self-check: python3 tests/test_app.py (no network; Mapillary is stubbed)."""
import http.cookiejar, io, itertools, sqlite3, json, os, sys, tempfile, threading, urllib.error, urllib.request

os.environ["BG_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app  # noqa: E402

# Seed a v0.1-style database (no lobby columns) to prove the upgrade migration works.
sqlite3.connect(os.environ["BG_DB"]).executescript(
    "CREATE TABLE games(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, started REAL, total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0);")
_imgs = itertools.count(1)
app.pick_location = lambda token: (f"img{next(_imgs)}", 48.857, 2.352)  # always Paris, new image each call
sys.stdin = io.StringIO("adminpass\n")
app.cli_init("boss", "tok")
srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}/"


def client():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None, expect=200):
        req = urllib.request.Request(BASE + "api/" + path, None if body is None else json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with op.open(req) as r:
                code, data = r.status, json.load(r)
        except urllib.error.HTTPError as e:
            code, data = e.code, json.load(e)
        assert code == expect, (path, code, data)
        return data
    return call


boss, bob = client(), client()
boss("login", {"username": "boss", "password": "adminpass"})
assert boss("me")["is_admin"]
bob("register", {"username": "bob", "password": "secret1"})
bob("register", {"username": "bob", "password": "secret1"}, expect=409)
bob("admin/users", expect=403)
assert urllib.request.urlopen(BASE).read().startswith(b"<!doctype html>")

# bob plays a full game: guesses Paris exactly every round -> 5 * 1000
g = bob("game", {})
assert g["round"] == 1 and g["image_id"].startswith("img")
bob(f"game/{g['id']}/peek", expect=403)
for n in range(1, 6):
    r = bob(f"game/{g['id']}/guess", {"lat": 48.857, "lng": 2.352 + 360})  # wrapped longitude
    assert r["score"] == 1000 and r["done"] == (n == 5), r
    if n < 5:
        assert bob(f"game/{g['id']}")["round"] == n + 1
bob(f"game/{g['id']}/guess", {"lat": 48.857, "lng": 2.352}, expect=409)  # game over
assert bob(f"game/{g['id']}")["total"] == 5000
boss(f"game/{g['id']}", expect=404)  # can't see someone else's game

# far guess scores low; London ~344 km away
far = app.score_for(app.haversine_km(48.857, 2.352, 51.507, -0.128))
assert 700 < far < 800, far

# admin grants themselves cheats: peek + 2x multiplier
users = {u["username"]: u for u in boss("admin/users")["users"]}
assert users["bob"]["best"] == 5000 and users["bob"]["last_ip"] == "127.0.0.1"
me_id = users["boss"]["id"]
boss("admin/user", {"id": me_id, "action": "update", "fields": {"username": "boss", "is_admin": True, "cheat_reveal": True, "cheat_multiplier": 2}})
boss("admin/user", {"id": me_id, "action": "update", "fields": {"username": "boss", "is_admin": False}}, expect=400)
g2 = boss("game", {})
assert boss(f"game/{g2['id']}/peek") == {"lat": 48.857, "lng": 2.352}
assert boss(f"game/{g2['id']}/guess", {"lat": 48.857, "lng": 2.352})["score"] == 2000

# admin controls bob's account
bob_id = users["bob"]["id"]
boss("admin/user", {"id": bob_id, "action": "update", "fields": {"username": "bob", "banned": True}})
bob("me", expect=401)
bob("login", {"username": "bob", "password": "secret1"}, expect=403)
boss("admin/user", {"id": bob_id, "action": "update", "fields": {"username": "bobby", "banned": False}})
boss("admin/user", {"id": bob_id, "action": "password", "password": "newpass1"})
bob("login", {"username": "bobby", "password": "newpass1"})
boss("admin/user", {"id": bob_id, "action": "reset_stats"})
assert next(u for u in boss("admin/users")["users"] if u["id"] == bob_id)["games"] == 0
boss("admin/user", {"id": me_id, "action": "delete"}, expect=400)
boss("admin/user", {"id": bob_id, "action": "delete"})
bob("me", expect=401)

# ---- lobbies ----
players = {n: client() for n in ("ann", "ben", "cat", "dan")}
for n, cl in players.items():
    cl("register", {"username": n, "password": "secret1"})
ann, ben, cat, dan = players.values()

# 1v1: same locations for both, separate scores, max 2 players
ann("lobby", {"mode": "ffa"}, expect=400)
ffa = ann("lobby", {"mode": "1v1"})
code = ffa["code"]
ben(f"lobby/{code}", expect=403)  # not a member yet
ann(f"lobby/{code}/start", {}, expect=409)  # alone
ben("lobby/join", {"code": code.lower()})
ben(f"lobby/{code}/start", {}, expect=403)  # not host
ben(f"lobby/{code}/play", {}, expect=409)  # not started
cat("lobby/join", {"code": code}, expect=409)  # full
ann(f"lobby/{code}/start", {})
ga, gb = ann(f"lobby/{code}/play", {}), ben(f"lobby/{code}/play", {})
assert ann(f"lobby/{code}/play", {})["id"] == ga["id"]  # one game per player per lobby
for n in range(1, 6):
    assert ga["image_id"] == gb["image_id"], (n, ga, gb)
    ann(f"game/{ga['id']}/guess", {"lat": 48.857, "lng": 2.352})
    ben(f"game/{gb['id']}/guess", {"lat": 51.507, "lng": -0.128})  # London: worse
    if n < 5:
        ga, gb = ann(f"game/{ga['id']}"), ben(f"game/{gb['id']}")
st = ann(f"lobby/{code}")
assert st["finished"] and [m["username"] for m in st["members"]] == ["ann", "ben"] and st["members"][0]["total"] == 5000, st
assert [lb["code"] for lb in ben("lobbies")["lobbies"]] == [code]

# 2v2: auto-balanced teams, max 2 per team, team score = sum
v = ann("lobby", {"mode": "2v2"})
code = v["code"]
assert v["my_team"] == "A"
ben("lobby/join", {"code": code, "team": "A"})
cat("lobby/join", {"code": code, "team": "A"}, expect=409)  # A full
assert cat("lobby/join", {"code": code})["my_team"] == "B"  # auto-picks the open team
ann(f"lobby/{code}/start", {}, expect=409)  # 3 players
dan("lobby/join", {"code": code, "team": "C"}, expect=400)
dan("lobby/join", {"code": code, "team": "B"})
ann(f"lobby/{code}/start", {})
for cl, lat, lng in ((ann, 48.857, 2.352), (ben, 48.857, 2.352), (cat, 51.507, -0.128), (dan, 51.507, -0.128)):
    g = cl(f"lobby/{code}/play", {})
    for n in range(5):
        cl(f"game/{g['id']}/guess", {"lat": lat, "lng": lng})
        g = cl(f"game/{g['id']}")
st = dan(f"lobby/{code}")
assert st["finished"] and st["teams"]["A"] == 10000 and st["teams"]["B"] == 2 * 5 * far, st

# ---- ranked ----
ids = {u["username"]: u["id"] for u in boss("admin/users")["users"]}
assert ben("me")["rank"] == "Bronze" and ben("me")["next_rank_at"] == 100


def ranked_match(p1, p2, p1_wins=True):
    c1 = p1("ranked", {})["code"]
    g1 = p1(f"lobby/{c1}/play", {})
    assert p1("ranked", {})["code"] == c1  # resumes the unfinished match instead of queueing again
    for _ in range(5):
        p1(f"game/{g1['id']}/guess", {"lat": 48.857, "lng": 2.352} if p1_wins else {"lat": 0, "lng": 0})
        g1 = p1(f"game/{g1['id']}")
    st = p1(f"lobby/{c1}")
    assert not st["finished"] and len(st["members"]) == 1  # waiting for an opponent
    c2 = p2("ranked", {})["code"]
    assert c2 == c1, "matchmaking should pair p2 with p1's open match"
    g2 = p2(f"lobby/{c2}/play", {})
    for _ in range(5):
        p2(f"game/{g2['id']}/guess", {"lat": 51.507, "lng": -0.128})
        g2 = p2(f"game/{g2['id']}")
    return p1(f"lobby/{c1}")


st = ranked_match(ben, cat)
assert st["finished"] and {m["username"]: m["delta"] for m in st["members"]} == {"ben": 25, "cat": 0}, st  # cat had 0 RP, can't drop
assert ben("me")["rank_points"] == 25
ben("lobby/join", {"code": st["code"]}, expect=403)  # ranked lobbies are matchmaking-only
st = ranked_match(cat, ben, p1_wins=False)
assert ben("me")["rank_points"] == 50 and cat("me")["rank_points"] == 0

# Legend Division: gated on the top rank, then MMR via Elo, public leaderboard
ben("legend/enter", {}, expect=403)
boss("admin/user", {"id": ids["ben"], "action": "update", "fields": {"rank_points": 499}})
assert ben("me")["rank"] == "Diamond" and not ben("me")["legend_eligible"]
boss("admin/user", {"id": ids["cat"], "action": "update", "fields": {"rank_points": 480}})
ranked_match(ben, cat)
me = ben("me")
assert me["rank"] == "Master" and me["legend_eligible"] and not me["legend"]
ben("legend/enter", {})
assert ben("me")["legend"] and ben("me")["mmr"] == 1500
boss("admin/user", {"id": ids["dan"], "action": "update", "fields": {"legend": True}})
st = ranked_match(ben, dan)  # both legends now -> legend queue, Elo
assert st["mode"] == "legend" and {m["username"]: m["delta"] for m in st["members"]} == {"ben": 16, "dan": -16}, st
assert ben("me")["rank_points"] == 524  # RP frozen in legend
rows = client()("legends")["rows"]  # no login needed
assert [(r["username"], r["mmr"], r["matches"]) for r in rows] == [("ben", 1516, 1), ("dan", 1484, 1)], rows
cat("ranked", {})  # non-legend queue never pairs with legends
assert len(cat(f"lobby/{cat('ranked', {})['code']}")["members"]) == 1

# deleting a host removes their lobbies but keeps other players' games
ben_games = lambda: next(u for u in boss("admin/users")["users"] if u["username"] == "ben")["games"]  # noqa: E731
before = ben_games()
boss("admin/user", {"id": ids["ann"], "action": "delete"})
ben(f"lobby/{code}", expect=404)
assert ben_games() == before

# settings + closed sign-ups
boss("admin/settings", {"mapillary_token": "t2", "registration_open": False})
assert boss("admin/settings") == {"mapillary_token": "t2", "registration_open": False}
client()("register", {"username": "eve", "password": "secret1"}, expect=403)
print("all checks passed")
