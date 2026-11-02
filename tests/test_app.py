"""End-to-end self-check: python3 tests/test_app.py (no network; Google Street View is stubbed)."""
import http.cookiejar, io, itertools, json, sqlite3, os, sys, tempfile, threading, urllib.error, urllib.request

os.environ["BG_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app  # noqa: E402

# Seed a v0.1-style database (no lobby columns) to prove the upgrade migration works.
sqlite3.connect(os.environ["BG_DB"]).executescript(
    "CREATE TABLE games(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, started REAL, total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0);")
real_pick = app.pick_location
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
# cheats are an admin-only menu: the admin turns on their own, nobody else's
boss("admin/user", {"id": me_id, "action": "update", "fields": {"cheat_reveal": True}}, expect=400)  # not an account field
assert boss("admin/cheats", {"cheat_reveal": True, "cheat_multiplier": 2})["cheat_multiplier"] == 2
bob("admin/cheats", expect=403)
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
    c1 = p1("queue", {"mode": "ranked"})["code"]
    g1 = p1(f"lobby/{c1}/play", {})
    assert p1("queue", {"mode": "ranked"})["code"] == c1  # resumes the unfinished match instead of queueing again
    for _ in range(5):
        p1(f"game/{g1['id']}/guess", {"lat": 48.857, "lng": 2.352} if p1_wins else {"lat": 0, "lng": 0})
        g1 = p1(f"game/{g1['id']}")
    st = p1(f"lobby/{c1}")
    assert not st["finished"] and len(st["members"]) == 1  # waiting for an opponent
    c2 = p2("queue", {"mode": "ranked"})["code"]
    assert c2 == c1, "matchmaking should pair p2 with p1's open match"
    g2 = p2(f"lobby/{c2}/play", {})
    for _ in range(5):
        p2(f"game/{g2['id']}/guess", {"lat": 51.507, "lng": -0.128})
        g2 = p2(f"game/{g2['id']}")
    return p1(f"lobby/{c1}")


# dynamic rating: hand-checked values
U = lambda rp=0, mmr=1500: {"rank_points": rp, "mmr": mmr}  # noqa: E731
assert app.rating_delta("ranked", 1, U(100), U(100), 0, False) == 20  # even match: +20 / -20
assert app.rating_delta("ranked", 0, U(100), U(100), 0, False) == -20
assert app.rating_delta("ranked", 1, U(100), U(100), 1, False) == 40  # blowout doubles it
assert app.rating_delta("ranked", 1, U(100), U(300), 0, False) == 36  # underdog win pays a lot...
assert app.rating_delta("ranked", 1, U(300), U(100), 0, False) == 5  # ...a favourite's win pays the minimum
assert app.rating_delta("ranked", 0, U(300), U(100), 0, False) == -36  # and a favourite's loss hurts
assert app.rating_delta("ranked", 1, U(100), U(300), 1, False) == 60  # capped
assert app.rating_delta("ranked", 0, U(10), U(10), 0, False) == -10  # RP floor at 0
assert app.rating_delta("ranked", 0, U(100), U(100), 1, True) == 0  # rank shield
assert app.rating_delta("legend", 1, U(), U(), 0, False) == 16
assert app.rating_delta("legend", 0.5, U(mmr=1600), U(mmr=1400), 0, False) == -8.3  # draw vs weaker player costs a bit

st = ranked_match(ben, cat)
d = {m["username"]: m["delta"] for m in st["members"]}
assert st["finished"] and d["ben"] > 20 and d["cat"] == 0, st  # margin bonus; cat had 0 RP, can't drop
assert ben("me")["rank_points"] == d["ben"]
ben("lobby/join", {"code": st["code"]}, expect=403)  # matchmade lobbies can't be joined by code
st = ranked_match(cat, ben, p1_wins=False)
d2 = {m["username"]: m["delta"] for m in st["members"]}
assert 0 < d2["ben"] < 40, (d, d2)  # a blowout between equals pays 40; ben is now the favourite, so less
assert ben("me")["rank_points"] == d["ben"] + d2["ben"] and cat("me")["rank_points"] == 0

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
rp_before = ben("me")["rank_points"]
st = ranked_match(ben, dan)  # both legends now -> legend queue, Elo scaled by margin
assert st["mode"] == "legend" and {m["username"]: m["delta"] for m in st["members"]} == {"ben": 24.0, "dan": -24.0}, st
assert ben("me")["rank_points"] == rp_before  # RP frozen in legend
rows = client()("legends")["rows"]  # no login needed
assert [(r["username"], r["mmr"], r["matches"]) for r in rows] == [("ben", 1524, 1), ("dan", 1476, 1)], rows
cat("queue", {"mode": "ranked"})  # non-legend queue never pairs with legends
assert len(cat(f"lobby/{cat('queue', {'mode': 'ranked'})['code']}")["members"]) == 1


def play(cl, code, lat=48.857, lng=2.352, cheat=None):
    g = cl(f"lobby/{code}/play", {})
    for _ in range(5):
        if cheat:  # peek works in every mode
            assert cl(f"game/{g['id']}/peek") == {"lat": 48.857, "lng": 2.352}
        cl(f"game/{g['id']}/guess", {"lat": lat, "lng": lng})
        g = cl(f"game/{g['id']}")
    return cl(f"lobby/{code}")


# ---- casual matchmaking: 1v1 and 2v2 ----
q = {n: client() for n in ("pq1", "pq2", "pq3", "pq4", "pq5")}
for n, cl in q.items():
    cl("register", {"username": n, "password": "secret1"})
q1, q2, q3, q4, q5 = q.values()
ids.update({u["username"]: u["id"] for u in boss("admin/users")["users"]})
q1("queue", {"mode": "ffa"}, expect=400)
c1 = q1("queue", {"mode": "1v1"})["code"]
q3("lobby/join", {"code": c1}, expect=403)  # matchmade: queue only
assert q2("queue", {"mode": "1v1"})["code"] == c1
assert q3("queue", {"mode": "1v1"})["code"] != c1  # full, so q3 opens a new match
assert play(q1, c1)["finished"] is False  # q2 hasn't played yet
st = play(q2, c1, 51.507, -0.128)
assert st["finished"] and st["matchmade"] and all(m["delta"] is None for m in st["members"])  # casual: no RP

c2 = q1("queue", {"mode": "2v2"})["code"]
assert [cl("queue", {"mode": "2v2"})["code"] for cl in (q2, q4, q5)] == [c2] * 3
st = q1(f"lobby/{c2}")
assert sorted(m["team"] for m in st["members"]) == ["A", "A", "B", "B"] and st["seats"] == 4
assert q3("queue", {"mode": "2v2"})["code"] != c2  # 5th player gets a fresh match

# ---- cheats, in every playlist ----
boss("admin/user", {"id": ids["pq1"], "action": "update", "fields": {"is_admin": True}})
q1("admin/cheats", {"cheat_aim": True, "cheat_floor": 900, "cheat_shield": True})
assert q1("me")["cheat_aim"] and not q1("me")["cheat_reveal"]
q2(f"game/{q2(f'lobby/{c2}/play', {})['id']}/peek", expect=403)
for cl in (q2, q4, q5):
    play(cl, c2, 51.507, -0.128)
st = play(q1, c2, 0, 0, cheat=True)  # terrible guesses, but the floor makes each round worth 900
assert st["finished"] and next(m for m in st["members"] if m["username"] == "pq1")["total"] == 4500, st
team = next(m["team"] for m in st["members"] if m["username"] == "pq1")
assert st["teams"][team] > st["teams"]["AB".replace(team, "")], st  # q1's team wins
st = play(q1, q1("queue", {"mode": "ranked"})["code"], cheat=True)
q1("admin/cheats", {"cheat_floor": 0})
boss("admin/user", {"id": ids["pq1"], "action": "update", "fields": {"rank_points": 100}})
rc = q1("queue", {"mode": "ranked"})["code"]
play(q1, rc, 0, 0)  # loses...
assert q4("queue", {"mode": "ranked"})["code"] == rc  # closest RP to q4's 0 is q1's 100
play(q4, rc)
d = {m["username"]: m["delta"] for m in q1(f"lobby/{rc}")["members"]}
assert d["pq1"] == 0 and d["pq4"] == 60, d  # underdog blowout win hits the cap
assert q1("me")["rank_points"] == 100  # the shield blocked the RP loss
q1("admin/cheats", {"cheat_floor": 1001}, expect=400)
# demoted admins lose their cheats, even though the settings stay stored
boss("admin/user", {"id": ids["pq1"], "action": "update", "fields": {"is_admin": False}})
assert not q1("me")["cheat_aim"]
q1(f"game/{q1('game', {})['id']}/peek", expect=403)

# ---- daily quests (replaced daily rewards) ----
import datetime  # noqa: E402
real_today = app.utc_today
boss("admin/config", {"values": {"quests_per_day": 12}})  # show the whole pool
qz = client()
qz("register", {"username": "quester", "password": "secret1"})
ql = qz("quests")
qs = {q["key"]: q for q in ql["quests"]}
assert set(qs) == set(app.QUESTS) and not any(q["done"] for q in qs.values()) and 0 < ql["resets_in"] <= 86400
assert [app.TIER_ORDER[q["tier"]] for q in ql["quests"]] == sorted(app.TIER_ORDER[q["tier"]] for q in ql["quests"])  # easy first
assert qs["points_today"]["title"] == "Earn 10,000 points today" and qs["finish_games"]["title"] == "Finish 3 games"
qz("quests/claim", {"key": "finish_games"}, expect=409)  # not done yet
g = qz("game", {})
for _ in range(5):
    qz(f"game/{g['id']}/guess", {"lat": 48.857, "lng": 2.352})
    g = qz(f"game/{g['id']}")
qs = {q["key"]: q for q in qz("quests")["quests"]}
assert {k for k, q in qs.items() if q["done"]} == {"near_perfect", "big_game", "close_guesses"}, qs
assert (qs["rounds_played"]["progress"], qs["finish_games"]["progress"], qs["points_today"]["progress"]) == (5, 1, 5000)
assert qz("me")["quests_claimable"] == 3
rp0 = qz("me")["rank_points"]
assert qz("quests/claim", {"key": "big_game"}) == {"reward": 35, "unit": "RP"}
qz("quests/claim", {"key": "big_game"}, expect=409)  # once
qz("quests/claim", {"key": "nope"}, expect=404)
assert qz("me")["rank_points"] == rp0 + 35 and qz("me")["quests_claimable"] == 2
# rewards are admin-tunable; legends earn a fifth as MMR
boss("admin/config", {"values": {"quest_rp_easy": 50}})
assert {q["key"]: q for q in qz("quests")["quests"]}["close_guesses"]["reward"] == 50
assert all(q["unit"] == "MMR" for q in ben("quests")["quests"])
assert {q["key"]: q for q in ben("quests")["quests"]}["close_guesses"]["reward"] == 10
# quests for modes that are off never show up
boss("admin/config", {"values": {"mode_ranked": False}})
assert not {"play_ranked", "win_ranked"} & {q["key"] for q in qz("quests")["quests"]}
boss("admin/config", {"values": {"mode_ranked": True, "quest_rp_easy": 10, "quests_per_day": 3}})
# everyone gets the same 3 quests today, and a new day starts fresh
assert [q["key"] for q in qz("quests")["quests"]] == [q["key"] for q in q2("quests")["quests"]] and len(q2("quests")["quests"]) == 3
app.utc_today = lambda: real_today() + datetime.timedelta(days=1)
assert all(q["progress"] == 0 and not q["claimed"] for q in qz("quests")["quests"])
app.utc_today = real_today
assert [app.todays_quests(datetime.date(2026, 5, d)) for d in (1, 2)] != [app.todays_quests(datetime.date(2026, 5, 1))] * 2  # varies by day

# ---- spawns: mostly cities, some countryside ----
import random  # noqa: E402
near = lambda la, ln: min(app.haversine_km(la, ln, *c) for c in app.SEEDS)  # noqa: E731
random.seed(7)
pts = [app.pick_point() for _ in range(2000)]
city = [p for p in pts if p[2] == 800]
assert 0.80 < len(city) / len(pts) < 0.90, len(city)  # default 85%
assert all(near(la, ln) <= 4.01 for la, ln, _ in city) and all(near(la, ln) <= 40.1 for la, ln, _ in pts)
boss("admin/config", {"values": {"city_chance": 100}})
assert all(app.pick_point()[2] == 800 for _ in range(200))
boss("admin/config", {"values": {"city_chance": 85}})

# ---- profiles ----
pr = ben("profile/ben")
assert pr["ranked_record"] == {"w": 4, "l": 0, "d": 0}, pr  # 3 ranked wins + 1 legend win
assert pr["casual_record"] == {"w": 1, "l": 1, "d": 0}, pr  # lost the 1v1 to ann, won the 2v2 with her
assert pr["rank"] == "Legend" and pr["games"] >= 6 and pr["perfect_rounds"] >= 5
client()("profile/ben", expect=401)
ben("profile/nobody", expect=404)

# ---- reports ----
q2("report", {"username": "pq1", "reason": "cheating", "details": "x" * 900, "lobby": c2})
q2("report", {"username": "pq1", "reason": "cheating"}, expect=409)  # one open report per player
q2("report", {"username": "pq2", "reason": "cheating"}, expect=400)  # self
q2("report", {"username": "pq1", "reason": "vibes"}, expect=400)
q4("report", {"username": "pq1", "reason": "name"})
q4("report", {"username": "pq3", "reason": "abuse"})
q2("admin/reports", expect=403)
assert boss("me")["open_reports"] == 3
reps = boss("admin/reports")["reports"]
assert len(reps[-1]["details"]) == 500 and reps[-1]["lobby_code"] == c2 and reps[-1]["reason_label"] == "Cheating"
boss("admin/report", {"id": next(r["id"] for r in reps if r["target"] == "pq3"), "action": "dismiss"})
boss("admin/report", {"id": reps[-1]["id"], "action": "ban"})  # banning pq1 closes both reports about them
assert boss("me")["open_reports"] == 0
q1("me", expect=401)
assert {r["status"] for r in boss("admin/reports")["reports"]} == {"resolved", "dismissed"}

# ---- change password ----
q3b = client()
q3b("login", {"username": "pq3", "password": "secret1"})
q3("password", {"old": "nope", "new": "secret2"}, expect=403)
q3("password", {"old": "secret1", "new": "secret2"})
q3("me")
q3b("me", expect=401)  # other sessions are logged out

# ---- stale matches: unfilled ones close, no-shows forfeit ----
real_ttl = app.match_ttl
app.match_ttl = lambda: -1
q2("lobbies")  # expires everything still pending
app.match_ttl = real_ttl
sc = q2("queue", {"mode": "ranked"})["code"]
play(q2, sc)
assert q3("queue", {"mode": "ranked"})["code"] == sc  # joins, but never plays
app.match_ttl = lambda: -1
st = q2(f"lobby/{sc}")
app.match_ttl = real_ttl
assert st["expired"] and st["my_result"] == 1 and {m["username"]: m["result"] for m in st["members"]} == {"pq2": 1, "pq3": 0}, st
assert q3("queue", {"mode": "ranked"})["code"] != sc  # the expired match is gone from the queue

# ---- ranked leaderboard ----
board = client()("ranked-board")["rows"]  # public
assert all(r["rank"] for r in board) and "ben" not in [r["username"] for r in board]  # legends are listed separately

# ---- the admin panel controls everything ----
rules = {r["key"]: r for r in boss("admin/config")["rules"]}
assert rules["rounds"]["value"] == 5 and rules["maintenance"]["type"] == "bool" and rules["rp_k"]["group"] == "Ranked RP"
q2("admin/config", expect=403)
boss("admin/config", {"values": {"rounds": 0}}, expect=400)
boss("admin/config", {"values": {"nope": 1}}, expect=400)
boss("admin/config", {"values": {"rank_gold": 50}}, expect=400)  # thresholds must keep climbing

# rounds and scoring
boss("admin/config", {"values": {"rounds": 3, "max_round_score": 2000}})
g = q2("game", {})
assert g["rounds"] == 3
for _ in range(3):
    r = q2(f"game/{g['id']}/guess", {"lat": 48.857, "lng": 2.352})
    g = q2(f"game/{g['id']}")
assert r["done"] and r["score"] == 2000 and g["total"] == 6000, (r, g)
boss("admin/config", {"values": {"rounds": 5, "max_round_score": 1000}})

# rank thresholds
boss("admin/user", {"id": ids["pq2"], "action": "update", "fields": {"rank_points": 15}})
assert q2("me")["rank"] == "Bronze"
boss("admin/config", {"values": {"rank_silver": 10}})
me2 = q2("me")
assert me2["rank"] == "Silver" and me2["rank_floor"] == 10 and me2["next_rank_at"] == 200
boss("admin/config", {"values": {"rank_silver": 100}})

# modes & features on/off, public status
off = {"mode_2v2": False, "mode_practice": False, "quests_enabled": False, "reports_enabled": False, "mode_private": False, "mode_legend": False}
boss("admin/config", {"values": off})
q2("queue", {"mode": "2v2"}, expect=403)
q2("game", {}, expect=403)
q2("quests", expect=403)
q2("quests/claim", {"key": "duel"}, expect=403)
q2("report", {"username": "pq4", "reason": "other"}, expect=403)
q2("lobby", {"mode": "1v1"}, expect=403)
q2("legend/enter", {}, expect=403)
st = client()("status")
assert st["modes"]["2v2"] is False and st["modes"]["ranked"] is True and not st["quests_enabled"]
boss("admin/config", {"values": {k: True for k in off}})

# maintenance: only admins can start games
boss("admin/config", {"values": {"maintenance": True}})
q2("game", {}, expect=503)
q2("queue", {"mode": "1v1"}, expect=503)
boss("game", {})
boss("admin/config", {"values": {"maintenance": False}})
boss("admin/settings", {"announcement": "Season 2 starts Monday"})
assert client()("status")["announcement"] == "Season 2 starts Monday" and q2("me")["server"]["announcement"]


# matches: void undoes rating changes; end resolves now
app.match_ttl = lambda: -1
boss("admin/matches")  # clears every pending match so the next pairing is predictable
app.match_ttl = real_ttl
rp2, rp4 = q2("me")["rank_points"], q4("me")["rank_points"]
mc = q2("queue", {"mode": "ranked"})["code"]
play(q2, mc)
assert q4("queue", {"mode": "ranked"})["code"] == mc
play(q4, mc, 0, 0)
d = {m["username"]: m["delta"] for m in q2(f"lobby/{mc}")["members"]}
assert q2("me")["rank_points"] == rp2 + d["pq2"] and d["pq2"] > 0
listed = next(m for m in boss("admin/matches")["matches"] if m["code"] == mc)
assert listed["settled"] and {m["username"] for m in listed["members"]} == {"pq2", "pq4"}
boss("admin/match", {"code": mc, "action": "void"})
assert q2("me")["rank_points"] == rp2 and q4("me")["rank_points"] == rp4
q2(f"lobby/{mc}", expect=404)
mc = q2("queue", {"mode": "ranked"})["code"]
play(q2, mc)
boss("admin/match", {"code": mc, "action": "end"})
assert q2(f"lobby/{mc}")["expired"]  # nobody joined: closed without a result
boss("admin/match", {"code": mc, "action": "explode"}, expect=400)

# accounts: create; reports: delete
boss("admin/create", {"username": "newbie", "password": "secret1", "is_admin": False})
boss("admin/create", {"username": "newbie", "password": "secret1"}, expect=409)
client()("login", {"username": "newbie", "password": "secret1"})
assert next(u for u in boss("admin/users")["users"] if u["username"] == "quester")["quests_done"] == 1
q4("report", {"username": "pq2", "reason": "other"})
rid = boss("admin/reports")["reports"][0]["id"]
boss("admin/report", {"id": rid, "action": "delete"})
assert rid not in [r["id"] for r in boss("admin/reports")["reports"]]

# locations
boss("admin/locations", {"text": "48.85, 2.35  # Paris\n\n40.7;-74"})
assert app.seeds() == [(48.85, 2.35), (40.7, -74.0)] and boss("admin/locations")["custom"]
boss("admin/locations", {"text": "91, 0"}, expect=400)
boss("admin/locations", {"text": "paris"}, expect=400)
boss("admin/locations", {"reset": True})
assert app.seeds() == app.SEEDS

# admin log records every admin action, but never anyone's cheats
log = boss("admin/log")["log"]
actions = {e["action"] for e in log}
assert {"game_rules", "match_void", "match_end", "create", "settings", "locations", "report_delete", "update"} <= actions, actions
assert not any("cheat" in e["action"] or "cheat" in (e["detail"] or "") for e in log)
q2("admin/log", expect=403)

# deleting a host removes their lobbies but keeps other players' games
ben_games = lambda: next(u for u in boss("admin/users")["users"] if u["username"] == "ben")["games"]  # noqa: E731
before = ben_games()
boss("admin/user", {"id": ids["ann"], "action": "delete"})
ben(f"lobby/{code}", expect=404)
assert ben_games() == before

# ---- danger zone ----
boss("admin/danger", {"action": "season_reset"}, expect=400)  # needs the typed confirmation
boss("admin/danger", {"action": "season_reset", "confirm": "season_reset"})
assert not ben("me")["legend"] and ben("me")["rank_points"] == 0 and client()("legends")["rows"] == []
boss("admin/danger", {"action": "wipe_reports", "confirm": "wipe_reports"})
assert boss("admin/reports")["reports"] == []
boss("admin/danger", {"action": "wipe_matches", "confirm": "wipe_matches"})
assert all(u["games"] == 0 for u in boss("admin/users")["users"])

# settings + closed sign-ups
boss("admin/settings", {"google_key": "browser-key", "registration_open": False})
assert boss("admin/settings") == {"google_key": "browser-key", "google_server_key": "", "registration_open": False, "announcement": "Season 2 starts Monday"}
assert app.server_key() == "browser-key"  # falls back to the browser key
boss("admin/settings", {"google_server_key": "server-key"})
assert app.server_key() == "server-key" and app.browser_key() == "browser-key"
assert not any(k in (e["detail"] or "") for e in boss("admin/log")["log"] for k in ("browser-key", "server-key"))  # keys never logged

# real pick_location against fake Google responses: skips empty spots and user photo spheres
answers = iter([{"status": "ZERO_RESULTS"}, {"status": "OK", "copyright": "© Some Hiker", "pano_id": "user", "location": {"lat": 1, "lng": 2}},
                {"status": "OK", "copyright": "© 2024 Google", "pano_id": "PANO123", "location": {"lat": 48.1, "lng": 11.5}}])
seen = []


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


def fake_urlopen(url, timeout=None):
    seen.append(url)
    return FakeResp(json.dumps(next(answers)).encode())


app.urllib.request.urlopen = fake_urlopen
assert real_pick("K") == ("PANO123", 48.1, 11.5)
assert len(seen) == 3 and "key=K" in seen[0] and "source=outdoor" in seen[0] and "maps.googleapis.com/maps/api/streetview/metadata" in seen[0]
answers = iter([{"status": "REQUEST_DENIED", "error_message": "API key invalid"}])
try:
    real_pick("bad")
    raise AssertionError("expected an error")
except app.Err as e:
    assert e.code == 502 and "REQUEST_DENIED" in e.msg and "API key invalid" in e.msg
client()("register", {"username": "eve", "password": "secret1"}, expect=403)

# ---- login throttling (last: it locks out this IP) ----
x = client()
for _ in range(10):
    x("login", {"username": "boss", "password": "wrong"}, expect=401)
x("login", {"username": "boss", "password": "adminpass"}, expect=429)  # even the right password, until the window passes
print("all checks passed")
