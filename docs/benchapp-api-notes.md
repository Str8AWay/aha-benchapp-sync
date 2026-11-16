# BenchApp API notes

What the sync relies on in BenchApp's private GraphQL API, and how each fact was established. Re-check these whenever the sync starts failing with `BenchAppError`.

Sources:
- `recon/benchapp.har`: the manager scoring the Nov 8, 2026 Moose game (event 3766427) on the website.
- `tools/probe_benchapp.py`: a read-only probe run on 2026-11-16.
- The website bundle `https://www.benchapp.com/_expo/static/js/web/index-d70be8131c286541e9617696d39c58e6.js`. Introspection is disabled (Apollo `INTROSPECTION_DISABLED`), so enum values come from the bundle.

## Requests

- `POST https://graphql.benchapp.com/graphql` with a JSON body of `{operationName, query, variables}`. Query text is in `src/aha_benchapp/graphql/`, copied verbatim from the HAR by `tools/extract_har_queries.py`.
- **Headers on every call:** `x-graphql-client-name: web`, `x-graphql-client-version: 1.0.0`, `x-graphql-client-route: /schedule/[eventId]`, and `content-type: application/json`.
- **Auth:** `login(input: {email, password})` returns `{token, player {id}}`. Send `Authorization: Bearer <token>`; a bare token gets `UNAUTHENTICATED`. Also send `x-user-id: <login player.id>`, which is 900001 for the manager and matches the HAR.
- **Errors** come back as HTTP 200 with an `errors` array, for example `extensions.code: UNAUTHENTICATED`.
- **Local runs behind Cloudflare Gateway** (Zero Trust TLS inspection) need `REQUESTS_CA_BUNDLE` pointing at certifi's roots plus the keychain's "Gateway CA - Cloudflare Managed" certificate. Lambda doesn't need this.

## Reads

| Need | Operation | Where |
|---|---|---|
| Team's games | `eventListScreenContent` with `eventsInput: {teamId, includePrevious: true, order: "desc", seasonId: null, leagueId: null}`, `take`, `skip` | `data.events[]`. Each has `id`, `homeOrAway` (ours), `homeTeam`/`awayTeam {id, name}`, `opponent.id`, `season.id`, `dateAndTime {date: "2026-11-08", time: "16:20:00", full: "2026-11-08 16:20:00", status: "Past"}`, and `stats` |
| Score and shots | `scorekeeperEvent(eventId)` | `data.event.stats {homeTeamScore, awayTeamScore}` and `data.event.shots[] {id, period, goalId, team {id}}` |
| Rosters | `scorekeeperRostersPage(eventId)` | `data.gameSheet.{homeTeam,awayTeam}.players[] {playerId, playerNumberDisplay, playerName, isGoalie, temp, status}` |
| Existing points | `scorekeeperPointsAndPenalties(eventId)` | `data.eventPointsAndPenalties.points[]` / `.penalties[]` |

- **Dates:** `dateAndTime.date` is already the team's local date, so match on it directly and don't convert time zones.
- **Team names** carry the division in either position: "C1 Les Etoiles du Nord" and "Moose C1".
- **`stats.homeTeamShots` / `awayTeamShots` are always 0,** even with about 95 shot records on event 3766427. Count `scorekeeperEvent.shots` instead.
- **Rosters:** our roster includes inactive players (attendance `status` of `"Out"`). The opponent's sheet lists the temp Spare players created in earlier games against that opponent.

## Writes

The new object's ID is at `data.<mutation field>.id`. For `createRosterPlayer`, the player ID to use in points and penalties is `data.createRosterPlayer.player.id`.

| Write | Operation | Notes |
|---|---|---|
| Opponent or temp player | `scorekeeperCreateRosterPlayer` | `{class: "Spare", name, playerNumberDisplay, seasonId, teamId, temp: true}` |
| Attendance | `scorekeeperSetAttendance` | `{eventId, playerId, seasonId, teamId, status: "In"}` |
| Goal | `scorekeeperCreateEventPoint` | `period` is an EventPointPeriod; `type` is an EventPointType |
| Penalty | `scorekeeperCreateEventPenalty` | `kind` is the catalog display name; `duration` and `type` are enums |
| Shot | `scorekeeperCreateEventShot` | `{eventId, period, teamId}`, one call per shot |
| Goalie | `manageGoaliesCreateEventGoalie` | `{eventId, teamId, playerId, period: "One", time: "00:00"}` |
| Current period | `scorekeeperSetPeriod` | `(id, period)` |

**Goals don't create shots.** Event 3766427 has 7 goals and none of its 94 shots has a `goalId`. So `GOAL_COUNTS_AS_SHOT = False`, and AHA's full shot count gets entered.

**Existing data check (event 3766427):** our shots per period are 12/17/10, which matches AHA. Moose's are 15/30/10, against AHA's 15/18/10, which means 12 extra in period 2 from manual entry.

## Enums (from the bundle)

- **EventPointPeriod:** `One` … `Twenty`, `OT`, `OT2`, `SO`.
- **EventPeriod**, used by `setEventPeriod`: the same values plus `End`, `Final`, `Forfeit`.
- **EventPointType:** `EvenStrength`, `PowerPlay`, `ShortHanded`, `EmptyNet`, `PenaltyShot`, `OwnGoal`, `ShootOut`.
- **Penalty `type`:** `Minor`, `DoubleMinor`, `Major`, `GameMisconduct`, `BenchMinor`.
- **Penalty `duration`:** `One`, `OnePointFive`, `Two`, `TwoPointFive`, `Three`, `ThreePointFive`, `Four`, `Five`, `Six`, `Seven`, `Ten`, `Twenty`, `TwentyFive`, `PenaltyShot`, `Game`, `Match`.
- **Penalty `kind`:** free text, sent as the name from BenchApp's 42-entry ice hockey catalog ("Hooking", "High-Sticking", …). It's mirrored in `src/aha_benchapp/enums.py`.
