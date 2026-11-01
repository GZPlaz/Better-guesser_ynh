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

## Lobbies (free-for-all and 2v2)

From the home page, anyone can create a lobby and share its 6-letter code. Friends join with that code.
- **Free-for-all**: 2 to 20 players.
- **2v2**: exactly 2 players per team. New joiners go to the emptier team, and players can switch teams until the game starts.

Once the host presses **Start**, everyone plays the **same 5 locations** at their own pace. The lobby page updates the scores live, and in 2v2 a team's score is the sum of both players' scores. Lobby games also count toward the global leaderboard, and cheats work in them too.

## Admin panel

Log in as the admin and click **Admin**. For every account you get:
- created date, last login, last IP, active sessions, games, best score and total points
- rename, ban, kick (log out), set password, reset stats, delete, make admin

There are also two per-account cheats:
- **Peek cheat**: a 👁 button appears next to *Guess*. Clicking it drops a small pink dot on the map at the real location.
- **× Score**: a hidden score multiplier from 0 to 100. Set yours to 1.3 and nobody will notice. Set a friend's to 0.5 if you're feeling evil.

Settings: the Mapillary token and whether new sign-ups are allowed.

## Layout

- `src/app.py`: the server (Python stdlib, SQLite in the app's data dir)
- `src/index.html`: the whole frontend (Leaflet + MapillaryJS from CDN)
- `scripts/`, `conf/`, `manifest.toml`: YunoHost packaging (v2, helpers 2.1)
- `tests/test_app.py`: an end-to-end self-check. Run it with `python3 tests/test_app.py`.
