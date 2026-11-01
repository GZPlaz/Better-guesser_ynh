# Better Guesser for YunoHost

A self-hosted geography guessing game on real street-level imagery. Players are dropped somewhere in the world, look around, and pin their guess on a map. It's a YunoHost package with no dependencies beyond `python3`: a single-file Python server, SQLite, and one HTML page.

## Install

```bash
sudo yunohost app install https://github.com/gzplaz/better-guesser
```

The install asks for:
- the game admin's username and password
- a **Mapillary client token**, which is free: create an app at https://www.mapillary.com/dashboard/developers and copy its *Client Token*. You can skip this at install and paste it later in **Admin → Settings**.

Leave the access permission on `visitors` so friends can sign up in the game itself, without YunoHost accounts. Install, upgrade, backup, restore, change URL and remove are all supported. Upgrades migrate the database automatically and keep every account and score.

## Game modes

- **Ranked duel (1v1)**: you're matched with the player whose rank is closest to yours. You both play the same 5 places, and the higher total wins.
  - Ranks: Bronze (0 RP), Silver (100), Gold (200), Platinum (300), Diamond (400), **Master (500)**.
  - **RP changes are dynamic**: beating a higher-ranked player pays more, losing to a lower-ranked one costs more, and a big points margin swings it further (up to double). A typical even match is ±20 RP. Gains and losses are capped at 60, every win pays at least 5, and RP never drops below 0.
- **Legend Division**: once you reach Master you can enter it. Legends only face other legends and play for **MMR**: it starts at 1500 and changes by Elo (K=32), also scaled by the points margin. There is a **public Legend leaderboard** on the login page and under Leaderboards, with every legend's MMR.
- **Duel (1v1)** and **Team duel (2v2)**: *Find match* queues you with random players. *Private lobby* gives you a 6-letter code for friends. In 2v2, a team's score is the sum of both players' scores. These don't affect rank.
- **Practice**: 5 rounds by yourself.

Matchmade games don't wait for everyone to be online: you play straight away, and the next players in the queue get the same 5 places. If a match isn't complete after 24 hours, anyone who didn't finish forfeits, and matches nobody else joined are closed.

## Player features

- Profiles with ranked and casual records (wins, losses, draws), best and average game, average distance, near-perfect rounds, and recent matches.
- Leaderboards: Legend Division (MMR), Ranked (RP), and Best games.
- **Daily reward**: claim once a day (days are in UTC) to build a 7-day streak. Day 1 gives +5 RP and day 7 gives +35 RP, then the cycle starts over. Legends get +1 to +7 MMR. Missing a day resets the streak.
- **Reports**: players can report anyone from their profile or from a match, for cheating, an offensive username, harassment, or something else, with an optional note. Each player can have one open report per person, and at most 10 reports a day.
- Changing your password logs out your other devices. Logins are throttled after 10 failures in 15 minutes from one IP.
- In-game: full-screen street view, a pop-out guess map, Space/Enter to guess, a result after each round, and an end-of-game summary map.

## Admin panel

Log in as an admin and open **Admin** from the account menu.
- **Accounts**: rename, change rank (choosing a rank snaps RP into its range, and *Legend* moves the player into Legend Division), edit RP and MMR, make admin, ban, set a password, kick, reset stats, delete. You also see open reports, join date, last login, IP, sessions, daily streak, and games.
- **Reports**: every report with the reporter, reason, note and match link. Ban (which also closes every other open report about that player), resolve, or dismiss.
- **My cheats**: admin-only, and they apply only to your own account, in every mode. Nobody can switch cheats on for someone else, and demoting an admin switches their cheats off.
  - **Peek**: an eye button shows a small dot on the real location.
  - **Aimbot**: a target button places your pin 2 to 40 km from the answer, so it looks legit.
  - **Rank shield**: you never lose RP or MMR.
  - **Score multiplier**: every round's score is multiplied by this.
  - **Score floor**: every round is worth at least this many points.
- **Settings**: the Mapillary token, and whether sign-ups are open.

## Layout

- `src/app.py`: the server (Python stdlib, SQLite in the app's data dir)
- `src/index.html`: the whole frontend (Leaflet + MapillaryJS from CDN)
- `scripts/`, `conf/`, `manifest.toml`: YunoHost packaging (v2, helpers 2.1)
- `tests/test_app.py`: an end-to-end self-check. Run it with `python3 tests/test_app.py`.
