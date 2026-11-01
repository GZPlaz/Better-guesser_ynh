# Better Guesser for YunoHost

A self-hosted geography guessing game on real street-level imagery. It's a YunoHost package with no dependencies beyond `python3`.

## Install

```bash
sudo yunohost app install https://github.com/gzplaz/better-guesser
```

The install asks for:
- the game admin's username and password
- a **Mapillary client token**, which is free: create an app at https://www.mapillary.com/dashboard/developers and copy its *Client Token*. You can skip this at install and paste it later in **Admin → Settings**.

Leave the access permission on `visitors` so friends can sign up in the game itself, without YunoHost accounts.

## Game modes

- **🏆 Ranked 1v1**: press *Find ranked match* to be paired with the player whose rank is closest to yours. You both play the same 5 places, and the higher total wins.
  - Win: +25 RP. Loss: -20 RP (you can't go below 0). Draw: no change.
  - Ranks: Bronze (0), Silver (100), Gold (200), Platinum (300), Diamond (400), **Master (500)**.
  - Matches are asynchronous, so you never wait in a queue: you play straight away, and the next player who queues plays your 5 places against you.
- **👑 Legend Division**: once you reach Master, an *Enter Legend Division* button appears. Legends are only matched with other legends and get an **MMR** instead of RP: it starts at 1500 and changes by Elo with K=32. There is a **public Legend leaderboard** with every legend and their MMR, shown on the home page and on the login page, so it's visible without an account.
- **⚔️ 1v1**: *Find match* pairs you with the next player who queues. *Private lobby* gives you a code to send to a friend. Doesn't affect rank.
- **👥 2v2**: *Find match* fills two teams of two from the queue, balancing them as players join. *Private lobby* works by code, and players can switch teams until the host starts. A team's score is the sum of both players' scores. Doesn't affect rank.
- **Practice solo**: plays 5 rounds by yourself.

All modes count toward the *Best games* board.

## 🎁 Daily rewards

Claim a reward once per day (days are in UTC). Keep a streak to grow it:
- Day 1 gives +5 RP and day 7 gives +35 RP, then the cycle starts over.
- Legends get +1 to +7 MMR instead.
- Missing a day resets the streak.

## Admin panel

Log in as the admin and click **Admin**. For every account you get:
- created date, last login, last IP, active sessions, games, best score and total points
- rename, ban, kick (log out), set password, reset stats, delete, make admin
- edit RP, put someone in or take them out of Legend Division, and edit MMR

There are also per-account cheats. Each works in **every mode**: practice, 1v1, 2v2, private lobbies, ranked and Legend.
- **Peek**: a 👁 button appears next to *Guess*. Clicking it drops a small pink dot on the map at the real location.
- **Aimbot**: a 🎯 button places your pin a believable 2 to 40 km from the answer (about 970 to 1000 points), so it doesn't look like a perfect score every time. Then just press *Guess*.
- **× Score**: a hidden score multiplier from 0 to 100. Set yours to 1.3 and nobody will notice. Set a friend's to 0.5 if you're feeling evil.
- **Score floor**: every round is worth at least this many points (0 to 1000), however bad the guess.
- **Rank shield**: you never lose RP, or MMR in Legend Division. Wins still count.

The admin can also hand out RP, MMR or Legend status directly.

Settings: the Mapillary token and whether new sign-ups are allowed.

## Layout

- `src/app.py`: the server (Python stdlib, SQLite in the app's data dir)
- `src/index.html`: the whole frontend (Leaflet + MapillaryJS from CDN)
- `scripts/`, `conf/`, `manifest.toml`: YunoHost packaging (v2, helpers 2.1)
- `tests/test_app.py`: an end-to-end self-check. Run it with `python3 tests/test_app.py`.
