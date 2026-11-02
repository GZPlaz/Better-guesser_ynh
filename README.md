# Better Guesser for YunoHost

A self-hosted geography guessing game on real street-level imagery from **Mapillary**. Players are dropped somewhere in the world, walk around, and pin their guess on a map. **Everything is free**: there's no billing account and no paid API. It's a YunoHost package with no dependencies beyond `python3`: a single-file Python server, SQLite, and one HTML page.

## Install

```bash
sudo yunohost app install https://github.com/gzplaz/better-guesser
```

The install asks for the game admin's username and password, and a **Mapillary client token**. You can skip the token at install and paste it later in **Admin → Settings**.

Leave the access permission on `visitors` so friends can sign up in the game itself, without YunoHost accounts. Install, upgrade, backup, restore, change URL and remove are all supported. Upgrades migrate the database automatically and keep every account and score.

### Getting a Mapillary token (free, about 2 minutes)

1. Create a free account at https://www.mapillary.com (no card needed).
2. Go to https://www.mapillary.com/dashboard/developers and click **Register application**. Any name, description and URL will do, for example your game's address. Tick **Read** access.
3. Copy the **Client Token**, which starts with `MLY|`, and paste it into the install or into Admin → Settings.

Client tokens are meant to be used in browsers, so it's fine that players' browsers see it. The maps come from **OpenFreeMap**, which needs no key at all.

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
- **Daily quests**: 3 quests a day, the same for everyone, with new ones at midnight UTC. Examples are "Land 3 guesses within 50 km", "Score a near-perfect round" and "Win a ranked match".
  - Easy quests pay +10 RP, medium +20 and hard +35. Legends get a fifth of that as MMR.
  - Progress comes from the games you actually played that day. Claim each finished quest from the home page or the quest button in the top bar.
  - Quests for modes the admin has switched off never appear.
- **Reports**: players can report anyone from their profile or from a match, for cheating, an offensive username, harassment, or something else, with an optional note. Each player can have one open report per person, and at most 10 reports a day.
- Changing your password logs out your other devices. Logins are throttled after 10 failures in 15 minutes from one IP.
- In-game: full-screen street-level imagery. **Walk around freely** with the on-screen arrows or your keyboard, and look around 360° wherever there's a panorama. The guess map hides shops, stations and other places, so they don't give the answer away.
  - The guess map grows when you hover over it. Use − and + to pick its size, and the pin to keep it open. Both choices are remembered.
  - On phones, the map sits behind a Map button so it doesn't cover the imagery.
  - Press Space or Enter to guess. After each round, the result map shows a dashed great-circle line from your pin to the answer, and the end-of-game summary shows every round numbered.

## Admin panel

Log in as an admin and open **Admin** from the account menu. The admin panel controls the whole game:

- **Accounts**:
  - create accounts (optionally as admins)
  - rename players and change their rank (choosing a rank snaps RP into its range, and *Legend* moves the player into Legend Division)
  - edit RP and MMR
  - make admin, ban, set a password, kick, reset stats, delete
  - see open reports, join date, last login, IP, sessions and games
- **Matches**: every match in every mode, with players, scores, progress, results and RP/MMR changes.
  - *End now* resolves a match early: anyone who didn't finish forfeits.
  - *Void* reverses any RP/MMR the match gave or took, and deletes it and its games.
- **Reports**: ban (which also closes every other open report about that player), resolve, dismiss, or delete.
- **Game rules**: every number in the game, each with its default shown:
  - rounds per game, max points per round, how forgiving scoring is
  - the rank thresholds (Silver through Master)
  - the RP formula (swing, gap scale, cap, minimum, blowout margin) and the MMR formula (swing, gap scale, starting MMR)
  - quests per day and quest rewards (easy, medium, hard)
  - match expiry, login lockout, session length, and the daily report limit
- **Modes & features** (also under Game rules): switch ranked, Legend Division, duel and team-duel matchmaking, private lobbies, practice, daily quests and reports on or off. **Maintenance mode** lets only admins start games, while games already in progress can finish.
- **Locations**: the list of city centres rounds are drawn from (latitude, longitude per line), with about 260 cities on every continent built in. One click returns to that built-in list. Under Game rules:
  - **City rounds**: the chance a round lands in town (default 85%, within 4 km of a city centre).
  - **Countryside spread**: how far out the other rounds can land (default 40 km), so forests, fields and jungle stay rare.
- **Settings**: the announcement banner (shown to everyone, including on the login page), the Mapillary token, and whether sign-ups are open.
  - The **Danger zone** can reset the season (all RP, Legend status and MMR), wipe all matches, or wipe all reports. Each needs you to type the action's name to confirm.
- **My cheats**: admin-only, and they apply only to your own account, in every mode. Nobody can switch cheats on for someone else, and demoting an admin switches their cheats off.
  - **Peek**: an eye button shows a small dot on the real location.
  - **Aimbot**: a target button places your pin 2 to 40 km from the answer, so it looks legit.
  - **Rank shield**: you never lose RP or MMR.
  - **Score multiplier**: every round's score is multiplied by this.
  - **Score floor**: every round is worth at least this many points.
- **Log**: every admin action, newest first, showing who did what. Personal cheat settings are never logged.

## Layout

- `src/app.py`: the server (Python stdlib, SQLite in the app's data dir)
- `src/index.html`: the whole frontend (MapillaryJS for the imagery, MapLibre with OpenFreeMap for both maps; loaded on the first game)
- `scripts/`, `conf/`, `manifest.toml`: YunoHost packaging (v2, helpers 2.1)
- `tests/test_app.py`: an end-to-end self-check. Run it with `python3 tests/test_app.py`.
