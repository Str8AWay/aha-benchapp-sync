# AHA → BenchApp Game Sync — Design

## Intent

The team manager of Les Etoiles du Nord C1 enters each game's results into
BenchApp by hand so teammates can see the season record and player stats there.
AHA Hockey already publishes the full box score for every game. A scheduled AWS
Lambda should copy each completed game's box score from AHA into BenchApp's
scorekeeper, so the record and stats stay current with no manual entry.

The record matters most; player stats come second.

## Goals, non-goals, anti-goals

**Goals**
- Every completed AHA game ends up in BenchApp with the correct final score, so
  BenchApp's W/L/T record matches AHA.
- Goals (scorer, assists, period, time), penalties, shots on goal per period,
  and the starting goalie are entered for both teams, matching what the manager
  enters by hand today.
- The team is configurable: AHA team URL and BenchApp team are settings, not code.
- Games already played this season that have no score in BenchApp get filled in
  on the first run (backfill falls out of the normal logic; no special mode).

**Non-goals**
- Live, in-game scorekeeping. Results are copied after AHA posts them.
- Syncing anything other than game results (schedule, RSVPs, rosters).
- Supporting more than one team per deployment. Another team deploys another
  copy with different settings.

**Anti-goals** (failures even if it "works")
- Entering a game twice, or altering a game someone already scored in BenchApp.
- Attributing a goal or point to the wrong teammate.
- Failing silently. If a game can't be synced, the manager hears about it.
- Holding the record hostage to stats: a problem with one player's stats must
  not prevent the final score from being entered.

## Constraints

- BenchApp has no public API. The Lambda uses the private GraphQL API the
  BenchApp website uses, logged in as the manager's account. It can break if
  BenchApp changes that API; the design should make breakage loud and easy to
  diagnose, not prevent it.
- AHA game pages are server-rendered HTML scraped with requests + BeautifulSoup,
  as the existing `same-level-players.py` already does.
- Python 3.12, matching the existing scripts in this directory.
- Runs on AWS Lambda on a schedule; cost should be negligible.

## Approaches

1. **Call BenchApp's private web API directly** — *chosen.* Small Python Lambda
   issuing the same GraphQL requests the website makes. Fast and cheap; requires
   recording the website's traffic once (done: `recon/benchapp.har`) and
   re-recording if BenchApp changes the API.
2. **Headless browser (Playwright) driving the website.** No need to learn the
   request format, but needs a large container-image Lambda with Chromium, is
   slow, and breaks on any UI change or bot check. Rejected as heavier and more
   fragile.
3. **Scrape AHA and send the manager a summary to enter by hand.** Rejected: it
   defeats the purpose.

The manager picked approach 1.

## What BenchApp's API looks like

Learned from `recon/benchapp.har`: the manager logged in and scored the
Nov 8, 2026 game vs Moose C1 (AHA game 28819, BenchApp event 3766427) on
the website. The HAR is sanitized, so it holds no auth header values and no
response bodies. Everything below comes from the requests alone.

- **One endpoint:** `POST https://graphql.benchapp.com/graphql`, JSON body
  `{operationName, query, variables}`. The website also sends
  `x-graphql-client-name: web`, `x-graphql-client-version: 1.0.0`,
  `x-graphql-client-route`, and `x-user-id` (900001 for the manager).
- **Login:** `mutation login(input: {email, password}) { token player { id } }`.
  How the token is sent back was stripped from the HAR. It is presumably an
  `Authorization` header, which the builder must confirm.
- **IDs seen:** our team `1247980`, season `531381`, opponent team (Moose C1)
  `1528241`. In BenchApp, opponents are teams with their own rosters.

Operations the sync needs (the full query text is in the HAR, which is the
source of truth; copy the queries verbatim):

| Purpose | Operation | Key variables |
|---|---|---|
| List games | `eventListScreenContent` (`events`) | `eventsInput: {teamId, seasonId, includePrevious: true, order}`, `take`, `skip`. Returns `dateAndTime`, `homeTeam`/`awayTeam`, `opponent`, `homeOrAway`, `stats {homeTeamScore, awayTeamScore, homeTeamShots, awayTeamShots}`, `canScorekeep` |
| Rosters for a game | `scorekeeperRostersPage` (`gameSheet`) | `eventId` → each team's players with `playerId`, `playerNumberDisplay`, `playerName`, `isGoalie`, `temp` |
| Existing points/penalties | `scorekeeperPointsAndPenalties`, `eventDetailsPointsAndPenalties` | `eventId` |
| Add opponent player | `scorekeeperCreateRosterPlayer` | `{class: "Spare", name, playerNumberDisplay, seasonId, teamId, temp: true}` |
| Mark player in | `scorekeeperSetAttendance` | `{eventId, playerId, seasonId, teamId, status: "In"}` |
| Goal | `scorekeeperCreateEventPoint` | `{eventId, seasonId, teamId, homeAway: "Home"\|"Away", period: "One"\|"Two"\|"Three", time: "MM:SS", type: "EvenStrength", goalPlayerId, assistOnePlayerId, assistTwoPlayerId}` (assists nullable) |
| Penalty | `scorekeeperCreateEventPenalty` | `{eventId, seasonId, teamId, homeAway, period, time, playerId, kind: "Hooking", duration: "Two", type: "Minor"}` |
| Shot | `scorekeeperCreateEventShot` | `{eventId, period, teamId}`. **One call per shot.** |
| Undo shot | `scorekeeperDeleteEventShot` | `{id}` |
| Current period | `scorekeeperSetPeriod` | `(id: eventId, period)` |
| Goalie | `manageGoaliesCreateEventGoalie` | `{eventId, teamId, playerId, period: "One", time: "00:00"}` |
| Season record | `getTeamRecord` | `{teamId, seasonId}` → `wins losses ties OTL` |

What the recording tells us about how BenchApp works:

- **No separate "final score" field.** The score (and so the record) comes
  from the goal points entered. The record is only right if *every* goal, ours
  and theirs, is entered.
- **Opponent goals need opponent players.** The manager created temporary
  "Spare" players on the opponent's BenchApp team for each opposing scorer and
  assister, using the AHA name and number, marked them "In", then used their
  IDs on the points.
- **Our goals used existing roster player IDs** for our team.
- **Only our goalie was entered** (period One, 00:00).
- **The shot counts in the HAR don't reconcile with AHA.** For us: 13/19/10
  created vs AHA 12/17/10, a difference that equals our goals per period. For
  Moose: 15/30/10 created vs 15/18/10, with 3 deletes and the period toggled
  back and forth. So either a goal counts as a shot automatically or it doesn't,
  and the manager's entry included corrections. **This must be settled before
  shots are written** (see Left to the builder).
- `EventPeriod` values seen: `One`, `Two`, `Three`. Overtime/shootout and
  "Final" values, if any, are unknown.

## Design

### Flow

Each scheduled run:

1. Fetch the AHA team page. Collect game links whose link text is "Box score"
   (completed). Links labeled "Game Info" are upcoming and get skipped.
2. Drop games already recorded as synced in state (see State and idempotency).
3. For each remaining game, parse the AHA game page into a `BoxScore`: date,
   both team names, per-period goals, goal list, penalty list, per-period
   shots, goalies, both rosters.
4. Log in to BenchApp once per run and find the matching BenchApp event.
5. If that event already has any points entered, record it as `skipped-existing`
   and touch nothing.
6. Otherwise write the game (see Writing to BenchApp), then read it back and
   verify the score.
7. Send one summary notification if anything was synced with warnings, skipped
   unexpectedly, or failed. Stay quiet on runs where nothing happened.

Code is split into three pure-ish layers so each can be tested from saved
fixtures: `aha` (HTML → `BoxScore`), `benchapp` (thin GraphQL client, one
function per operation), and `sync` (matching plus orchestration).

### Matching

**Game:** a BenchApp event in our team's current season whose date (in the
team's timezone) equals the AHA game date and whose opponent name matches the
AHA opponent. Match names leniently: case-insensitive, ignore punctuation, and
treat a trailing division label ("C1") as optional, because AHA shows both
"Moose C1" and "Spartans C1 C1". If there isn't exactly one match, the game
fails with a "no/ambiguous BenchApp event" warning and nothing is written.

**Home/away:** take it from the BenchApp event (`homeOrAway`, `homeTeam`/
`awayTeam`). Don't infer it from AHA's ordering.

**Our players:** match AHA `#N. F. Lastname` to the BenchApp game-sheet roster
for our team **by jersey number only** (`playerNumberDisplay`). Don't compare
names: players may use nicknames in BenchApp, and the league doesn't allow
non-rostered skater subs, so a number identifies the player *(manager's
decision)*. If a number matches nobody, or matches more than one player, the
player is unmatched (see below).

**Our goalie:** sub goalies are allowed, so a number match counts only if the
matched roster player is a goalie (`isGoalie`). Otherwise the goalie is treated
as unmatched *(my call: a sub goalie might wear a skater's number)*.

**Opponent players:** look them up on the opponent's game-sheet roster by
number, then by name. Earlier games against the same opponent may already have
created them. If there's no match, create a temp Spare player with the AHA
name and number, then mark them In, the same way the manager does now.

**Unmatched teammate** (a sub goalie, or a number missing from the BenchApp
roster): don't
attribute the stat to a guess. Create a temp Spare on *our* team the same way as
for opponents, so the goal still counts toward the score. Then list it in the
notification so the manager can fix the attribution by hand. This keeps the
record right without ever crediting the wrong teammate.

### Writing to BenchApp

Write in priority order so the record is protected first:

1. **Players:** resolve or create every player the game needs, as described
   under Matching.
2. **Goals:** for every AHA goal, both teams, call `createEventPoint` with
   `type: "EvenStrength"`. AHA doesn't report power-play or shorthanded goals.
   Use AHA's period and clock time as given, since the manager entered them that
   way (AHA "11:20" became BenchApp "11:20").
3. **Verify the score:** re-read the event's `stats` and compare it to the AHA
   final. On a mismatch, mark the game `failed-score-mismatch`, notify, and stop
   writing to that game.
4. **Goalies:** for each team, the AHA goalie goes in via `createEventGoalie`
   (period One, 00:00). This is our goalie at minimum; add the opponent's if the
   API allows it.
5. **Penalties:** map AHA "Penalty" to `kind` and AHA "Duration" in minutes to
   `duration` (2 → "Two", 4 → "Four", 5 → "Five", 10 → "Ten"; confirm the enum
   names). `type` is "Minor" for 2, "Major" for 5, and "Misconduct" for 10, also
   to confirm. An AHA penalty name with no matching `kind` is skipped with a
   warning. It never blocks the game.
6. **Shots:** for each team and period, BenchApp's total shots must **equal
   AHA's shots on goal** *(manager's decision)*. If BenchApp adds a shot
   automatically for each goal, create only `AHA shots − goals` shots in that
   period; if it doesn't, create the full AHA count. Then verify the totals
   against `stats.homeTeamShots` / `awayTeamShots`.
7. **Period:** leave the event's period at the last period played, as the
   manager did.

**Games not decided in regulation** (decided with the manager):
- **Overtime:** BenchApp has an overtime period. AHA overtime goals go in under
  that period like any other goal, and so do overtime shots if AHA reports them.
  The builder confirms the `EventPeriod` value.
- **Shootouts:** AHA only uses them in the playoffs. If a game page shows a
  shootout, or a final score that doesn't equal the sum of the listed goals,
  the game isn't written. It's marked `failed-manual-entry` and the manager gets
  an email to enter it by hand.
- **Forfeits:** handled the same way as shootouts *(my call; the manager didn't
  object to the proposal)*.

Steps 4 through 6 are best-effort. A failure records a warning and moves on.
Only failures in steps 1 through 3 fail the game.

Partial writes and mismatches: the sync never deletes anything in BenchApp. If
a game fails after its first point has been written (an API error midway, a
score mismatch, or a run that died while the state said `in-progress`), it
moves to a terminal `needs-cleanup` status. The manager is notified once, with
the IDs of the points the sync created (stored in state as they're created),
and the game is never retried automatically. Once the manager fixes it by hand,
they can clear the state entry to allow a re-sync. *(Builder-check call: no
delete mutation is known, and auto-deleting risks removing the manager's own
entries.)*

### State and idempotency

A DynamoDB table (on-demand), keyed by AHA game ID, holds `status`, the
BenchApp event ID, a timestamp, and any warnings. Statuses:

- `in-progress`: written before the first mutation. If it's still there on the
  next run, that run died partway through, so report the game as needing manual
  cleanup.
- `synced` / `synced-with-warnings`: done. Never touched again.
- `skipped-existing`: BenchApp already had points when we first looked. Never
  touched again.
- `needs-cleanup`: failed after writing something (see Writing to BenchApp).
  Terminal; never retried automatically.
- `failed-manual-entry`: shootout or forfeit. Terminal; the manager is notified
  once.
- `failed-<reason>` (`no-event`, `ambiguous-event`, `parse-error`,
  `api-error` before any write): nothing was written, so it's retried on every
  run. A `no-event` failure in
  particular may clear once the BenchApp event exists. Notify only when a game's
  status changes, so a stuck game doesn't email the manager every day.

The "already has points → skip" check on the BenchApp event is the real
safeguard. State alone is never trusted to prevent a duplicate.

### Configuration and secrets

Lambda environment variables:

| Name | Example | Notes |
|---|---|---|
| `AHA_TEAM_URL` | `https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308` | the configurable team |
| `BENCHAPP_TEAM_ID` | `1247980` | our BenchApp team |
| `BENCHAPP_SECRET_ID` | `aha-benchapp/credentials` | Secrets Manager secret holding `{"email": ..., "password": ...}` |
| `STATE_TABLE` | set by the template | |
| `NOTIFY_TOPIC_ARN` | set by the template | SNS topic with the manager's email subscribed |
| `TEAM_TIMEZONE` | `America/Chicago` | for date matching |
| `DRY_RUN` | `true` / `false` | when `true`, do every read and log every mutation that would be sent, without sending it or writing state |

Season isn't configured. It comes from the matched BenchApp event
(`season.id`), so a new season needs no change. A new AHA season gives the team
a new URL, so `AHA_TEAM_URL` gets updated then.

### Deployment and operations

- **Infrastructure:** an AWS SAM template defining the Python 3.12 Lambda
  (5-minute timeout, since one game is roughly 100 GraphQL calls, mostly
  shots), an EventBridge Scheduler rule firing daily at 8:00 America/Chicago,
  the DynamoDB table, the SNS topic plus email subscription, and read access to
  the secret. The secret itself is created by hand, so the password never
  appears in the template. This is my call; nothing in the directory is
  deployed yet.
- **Dependencies:** `requests` and `beautifulsoup4`, the same libraries the
  existing scripts use.
- **Local runs:** a CLI entry point (`python -m aha_benchapp --game 28819
  --dry-run`) runs the same code against one game using local AWS or env
  credentials. This is how the first real syncs get checked.
- **Rollout:** deploy with `DRY_RUN=true`. Review the logged mutations for one
  already-scored game and one unscored game, then flip to live.
- **Logging:** structured logs to CloudWatch for each run and game. The
  GraphQL errors array gets logged in full, since that is where API breakage
  will show up.
- **Politeness:** fetch AHA pages sequentially and stop after the first 20 new
  games in one run.

## Left to the builder

- **Auth header:** confirm how the login token is sent, either from a fresh
  unsanitized request (the manager can copy one header value from DevTools) or
  by trying `Authorization: Bearer <token>`.
- **Shots-vs-goals rule:** settle whether creating a point also creates a shot.
  Read back event 3766427's current `gameSheet` / `stats` shot totals and
  compare them with the 39/43 AHA totals, or test on a throwaway BenchApp event.
  This decides which of the two counts in step 6 applies.
- **Enum values:** introspect or probe the `EventPeriod`, penalty
  `kind`/`duration`/`type`, and point `type` enums. Build the AHA → BenchApp
  penalty-name map from the real enum.
- **Opponent goalie:** whether `createEventGoalie` accepts the opponent team.
  If it doesn't, skip it.
- **AHA edge cases:** find real AHA pages (other teams or past seasons) showing
  overtime, a shootout, a forfeit, and empty goal or penalty tables, and use them
  to detect the cases above. Fail cleanly as `parse-error` on anything
  unrecognized.
- **Fixtures:** save AHA team and game HTML and trimmed HAR request bodies under
  `tests/fixtures/` and unit-test the `aha` parser and the `sync` matching and
  ordering against them. Keep the raw HAR out of version control; it contains
  the manager's email.
