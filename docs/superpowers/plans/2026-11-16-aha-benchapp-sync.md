# AHA → BenchApp Game Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily AWS Lambda that copies each completed AHA game's box score into BenchApp's scorekeeper, so BenchApp's record and player stats match AHA.

**Architecture:** There are three layers, each tested from saved fixtures:
- `aha` turns AHA HTML into a `BoxScore`.
- `benchapp` is a thin GraphQL client with one method per operation, replayed verbatim from the recorded HAR.
- `sync` matches the game and players, writes in priority order, verifies the result, and records status in DynamoDB.

A Lambda handler and a CLI wrap `sync.run`.

**Tech Stack:**
- Python 3.12, using `requests` and `beautifulsoup4`
- `boto3` for DynamoDB, SNS, and Secrets Manager
- `pytest` and `responses` for tests
- AWS SAM with EventBridge Scheduler

**Spec:** `docs/superpowers/specs/2026-11-16-aha-benchapp-sync-design.md`

## Global Constraints

- Python 3.12. This directory is itself a Python 3.12.2 venv (`./bin/python`), so run every command through `./bin/python` / `./bin/pip`. The system `python3` is 3.15.
- Runtime dependencies are only `requests`, `beautifulsoup4`, and `boto3`. `boto3` is provided by the Lambda runtime; still list it so local runs work.
- Every BenchApp request is `POST https://graphql.benchapp.com/graphql` and carries the headers `x-graphql-client-name: web`, `x-graphql-client-version: 1.0.0`, `x-graphql-client-route`, and `x-user-id`, as the website sends them.
- GraphQL query text is copied verbatim from `recon/benchapp.har`; don't hand-write queries. The HAR has stray terminal escapes, so strip `\x1b\[[0-9;]*[A-Za-z]` before parsing it.
- Never delete anything in BenchApp. Never write to an event that already has points.
- Teammates match by jersey number only. A goalie match additionally requires `isGoalie`.
- BenchApp shot totals per team and period must equal AHA's shots on goal.
- In `DRY_RUN`, every read happens, every mutation is logged but not sent, and no state is written.
- `recon/` and the venv folders (`bin/`, `lib/`, `include/`, `pyvenv.cfg`) never go into git. The HAR contains the manager's email.
- Environment variable names are exactly `AHA_TEAM_URL`, `BENCHAPP_TEAM_ID`, `BENCHAPP_SECRET_ID`, `STATE_TABLE`, `NOTIFY_TOPIC_ARN`, `TEAM_TIMEZONE`, and `DRY_RUN`.
- Schedule: daily at 08:00 `America/Chicago`. Lambda timeout: 300 s. Process at most 20 new games per run.

## Review Focus

1. **A late game near midnight.** A BenchApp event at 23:30 Central on Nov 8 is stored in UTC as Nov 9, but it must still match the AHA game dated Nov 8. Covered in Task 5, `test_find_event_late_game_uses_team_timezone`.
2. **Opponent name variants.** AHA's "Spartans C1 C1" must match BenchApp "Spartans C1" or "Spartans", but must not match "Spartans B". Covered in Task 5, `test_team_names_match_variants`.
3. **AHA data where a period has more goals than shots.** The shot count to create goes negative. Clamp it to 0, add a warning, and don't crash. Covered in Task 7, `test_shots_clamped_when_goals_exceed_shots`.
4. **A posted "Box score" with an empty shots table.** AHA posted goals but not shots yet. Sync the goals and warn about missing shots; don't fail the game. Covered in Task 2, `test_parse_game_without_shots_table`, and Task 7, `test_missing_shots_is_warning`.
5. **An opponent temp player that already exists** from an earlier game against the same team. Reuse that player instead of creating a duplicate. Covered in Task 5, `test_opponent_player_reused_by_number`.

## File Structure

```
src/                 # SAM CodeUri: everything Lambda ships (keeps the venv dirs out)
 requirements.txt    # runtime deps, built into the Lambda package by `sam build`
 aha_benchapp/
  __init__.py
  models.py          # dataclasses + Period/Status enums shared by all layers
  aha.py             # fetch + parse AHA team page and game pages
  graphql/*.graphql  # query text extracted verbatim from the HAR (Task 3)
  benchapp.py        # BenchAppClient: login, one method per operation, dry-run
  enums.py           # AHA → BenchApp value maps (period, penalty kind/duration/type)
  matching.py        # event matching, team-name normalization, player resolution
  state.py           # StateStore protocol, DynamoStateStore, MemoryStateStore
  sync.py            # sync_game() write order + verification; run() loop
  notify.py          # summary formatting + SNS publish
  config.py          # Config.from_env()
  handler.py         # lambda_handler
  __main__.py        # CLI: python -m aha_benchapp [--game ID] [--dry-run]
tools/extract_har_queries.py   # one-off: HAR → src/aha_benchapp/graphql/*.graphql
docs/benchapp-api-notes.md     # findings from live API discovery (Task 3)
tests/fixtures/                # AHA HTML pages, trimmed GraphQL responses
tests/test_*.py                # one per module
template.yaml, samconfig.toml, requirements-dev.txt, pytest.ini, .gitignore
```

---

## Tasks

### Task 1: Scaffold, shared models, completed-game list

**Files:**
- Create: `.gitignore`, `src/requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `src/aha_benchapp/__init__.py`, `src/aha_benchapp/models.py`, `src/aha_benchapp/aha.py`
- Create: `tests/__init__.py`, `tests/fixtures/aha_team_308.html`, `tests/test_aha.py`

**Interfaces:**
- Produces (`models.py`):
  - `class Period(Enum)`: `ONE = 1`, `TWO = 2`, `THREE = 3`, `OT = 4`
  - `@dataclass(frozen=True) AhaPlayer(number: str, name: str)`. `number` is kept as a string so `"0"` and `"00"` stay distinct.
  - `@dataclass(frozen=True) Goal(team: str, period: Period, time: str, scorer: AhaPlayer, assists: tuple[AhaPlayer, ...])`
  - `@dataclass(frozen=True) Penalty(team: str, player: AhaPlayer, kind: str, period: Period, minutes: int, time: str)`
  - `@dataclass(frozen=True) BoxScore(game_id: int, date: datetime.date, teams: tuple[str, str], team_ids: tuple[int, int], final: dict[str, int], goals: tuple[Goal, ...], penalties: tuple[Penalty, ...], shots: dict[str, dict[Period, int]], goalies: dict[str, AhaPlayer])`. `teams` is in AHA's listed order. `shots == {}` when AHA has no shots table.
  - `class Status(str, Enum)` with values `in-progress`, `synced`, `synced-with-warnings`, `skipped-existing`, `needs-cleanup`, `failed-manual-entry`, `failed-no-event`, `failed-ambiguous-event`, `failed-parse-error`, `failed-api-error`, plus the property `retryable -> bool`. It's True only for the four `failed-*` values other than `failed-manual-entry`.
  - `@dataclass GameResult(game_id: int, status: Status, event_id: int | None = None, warnings: list[str] = field(default_factory=list), created_point_ids: list[int] = field(default_factory=list))`
  - Exceptions: `ParseError(Exception)` and `ManualEntryRequired(Exception)`. The message of `ManualEntryRequired` is the reason the manager sees.
- Produces (`aha.py`): `fetch(url: str, session: requests.Session) -> str`, and `completed_game_ids(team_html: str) -> list[int]`, in page order.

- [ ] **Step 1: Set up the repo.** Run `git init`.
  - `.gitignore` lists `bin/`, `lib/`, `include/`, `pyvenv.cfg`, `recon/`, `__pycache__/`, and `.aws-sam/`.
  - `src/requirements.txt` lists `requests`, `beautifulsoup4`, and `boto3`.
  - `requirements-dev.txt` lists `-r src/requirements.txt`, `pytest`, and `responses`.
  - `pytest.ini` sets `pythonpath = src` and `testpaths = tests`. Run the CLI as `./bin/python -m aha_benchapp` with `PYTHONPATH=src`.
  - Install into this directory's venv with `./bin/pip install -r requirements-dev.txt`.
  - Download `https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308` to `tests/fixtures/aha_team_308.html`.

- [ ] **Step 2: Write the failing tests.**

```python
def test_completed_game_ids_only_box_scores():
    ids = completed_game_ids(fixture("aha_team_308.html"))
    assert ids[:2] == [28819, 28852]           # links labeled "Box score"
    assert 28970 not in ids                    # labeled "Game Info" (upcoming)

def test_status_retryable():
    assert Status.FAILED_NO_EVENT.retryable
    assert not Status.FAILED_MANUAL_ENTRY.retryable
    assert not Status.NEEDS_CLEANUP.retryable
```

If the freshly downloaded fixture has more completed games, extend the first assertion. Don't loosen it. `fixture(name)` is a helper in `tests/__init__.py` that reads `tests/fixtures/<name>`.

- [ ] **Step 3: Run `./bin/python -m pytest tests/test_aha.py -v`.** Expected: FAIL (ImportError).

- [ ] **Step 4: Implement `models.py` and `completed_game_ids`.** Select `a[href^="/game/"]` whose stripped text is `Box score` and de-duplicate while keeping order. `fetch` sends a `User-Agent: aha-benchapp-sync` header, raises on non-2xx, and returns `.text`.

- [ ] **Step 5: Run the tests.** Expected: PASS.

- [ ] **Step 6: Commit.** Run `git add .gitignore requirements-dev.txt pytest.ini src tests`, then `git commit -m "feat: scaffold, models, AHA completed-game list"`.

### Task 2: AHA game page parser

**Files:**
- Modify: `src/aha_benchapp/aha.py`
- Create the fixtures `tests/fixtures/aha_game_28819.html` (downloaded), `aha_game_ot.html`, `aha_game_shootout.html`, `aha_game_forfeit.html`, and `aha_game_no_shots.html`.
- Test: `tests/test_aha.py`

**Interfaces:**
- Consumes: the models from Task 1.
- Produces: `parse_game(game_id: int, html: str) -> BoxScore`. It raises `ManualEntryRequired` for a shootout, or when the listed goals don't add up to the final score (a forfeit). It raises `ParseError` for anything it doesn't recognize.

The page structure (from 28819) is `<h3 class="headline">` sections:
- **Scoring** is `table.scorebox`. Its cells are `#goals-home1..3` and `#goals-home-final`, and the same with `away`. "home" means the first-listed team. Each row's team link (`/team/<slug>/<id>`) gives the AHA team ID.
- **Goals** columns: Player, Team, Assist 1, Assist 2, (blank), Period, Time.
- **Penalties** columns: Player, Team, Penalty, Period, Duration, Time.
- **Shots on Goal** columns: Team, Period 1–3, Final.
- **Goalies** columns: Player, Team, Shots Against, Goals Against, Save %.
- Players are written `#5. R. Steckman`.
- The date is in the paragraph "...was played on <strong>November 8th, 2026</strong>".

- [ ] **Step 1: Create the fixtures.**
  - Download `https://www.ahahockey.com/game/28819`.
  - Before hand-editing variants, look for real AHA examples by browsing other C1 teams' completed games: overtime (a 4th scoring column), a shootout, and a forfeit. Save any you find in place of the hand-made one, and match the labels they actually use.
  - If there's no real example, make the variants by hand:
    - `ot`: add a 4th scorebox column `OT`, plus one goal row with period `OT`, and bump that team's final.
    - `shootout`: add an `SO` column.
    - `forfeit`: make the Goals table empty, with a final of 1–0.
    - `no_shots`: remove the Shots on Goal section.

- [ ] **Step 2: Write the failing tests.**

```python
def test_parse_game_28819():
    box = parse_game(28819, fixture("aha_game_28819.html"))
    les, moose = "Les Etoiles du Nord C1", "Moose C1"
    assert box.date == date(2026, 11, 8)
    assert box.teams == (les, moose)
    assert box.team_ids == (308, 547)          # from the scorebox team links /team/<slug>/<id>
    assert box.final == {les: 3, moose: 4}
    assert len(box.goals) == 7
    assert box.goals[0] == Goal(les, Period.ONE, "11:20", AhaPlayer("5", "R. Steckman"), (AhaPlayer("24", "K. Sherlund"),))
    assert box.goals[2].assists == (AhaPlayer("92", "R. Riepe"), AhaPlayer("78", "S. Angell"))
    assert box.penalties == (Penalty(moose, AhaPlayer("23", "A. Cleary"), "Hooking", Period.TWO, 2, "11:07"),)
    assert box.shots == {les: {Period.ONE: 12, Period.TWO: 17, Period.THREE: 10},
                         moose: {Period.ONE: 15, Period.TWO: 18, Period.THREE: 10}}
    assert box.goalies == {les: AhaPlayer("30", "T. Agerter"), moose: AhaPlayer("37", "J. Bader")}

def test_parse_game_overtime_goal():
    assert parse_game(1, fixture("aha_game_ot.html")).goals[-1].period is Period.OT

def test_parse_game_shootout_requires_manual_entry():
    with pytest.raises(ManualEntryRequired, match="shootout"):
        parse_game(1, fixture("aha_game_shootout.html"))

def test_parse_game_forfeit_requires_manual_entry():
    with pytest.raises(ManualEntryRequired, match="final score"):
        parse_game(1, fixture("aha_game_forfeit.html"))

def test_parse_game_without_shots_table():          # Review Focus 4
    box = parse_game(28819, fixture("aha_game_no_shots.html"))
    assert box.shots == {} and len(box.goals) == 7

def test_parse_game_unrecognized_page():
    with pytest.raises(ParseError):
        parse_game(1, "<html><body>nothing here</body></html>")
```

- [ ] **Step 3: Run `./bin/python -m pytest tests/test_aha.py -v`.** Expected: the new tests FAIL.

- [ ] **Step 4: Implement `parse_game`.**
  - Find each table by the `h3.headline` text immediately before it.
  - Parse players with `^#(\S+)\.\s+(.+)$`. An empty assist cell means no assist.
  - Period labels: `1`/`2`/`3` map to `ONE`/`TWO`/`THREE`. `OT` (or whatever label the real page uses) maps to `Period.OT`. Any other label raises `ParseError`.
  - Parse the date with `datetime.strptime` after stripping the ordinal suffix (`st`, `nd`, `rd`, `th`).
  - The goal-sum check compares, per team, the count of `goals` to `final`.

- [ ] **Step 5: Run the tests.** Expected: PASS.

- [ ] **Step 6: Commit.** Run `git add src/aha_benchapp tests`, then `git commit -m "feat: parse AHA game box scores"`.

### Task 3: BenchApp API discovery (live, read-only, needs the manager)

This task settles the spec's "Left to the builder" API unknowns before any client code depends on them. It makes **no mutations**.
- **Who runs it:** the manager supplies credentials as `BENCHAPP_EMAIL` and `BENCHAPP_PASSWORD` in their own shell. Never write them to disk.
- **What it produces:** query files, `src/aha_benchapp/enums.py`, and `docs/benchapp-api-notes.md`.

**Files:**
- Create: `tools/extract_har_queries.py`, `src/aha_benchapp/graphql/*.graphql`, `src/aha_benchapp/enums.py`, `docs/benchapp-api-notes.md`
- Test: `tests/test_enums.py`

**Interfaces:**
- Produces (`enums.py`):
  - `PERIOD: dict[Period, str]`. `ONE`/`TWO`/`THREE` map to `"One"`/`"Two"`/`"Three"`, and `OT` maps to the discovered value.
  - `GOAL_COUNTS_AS_SHOT: bool`
  - `PENALTY_KINDS: dict[str, str]`, mapping a normalized name (lowercase, letters only) to the BenchApp `kind` enum value
  - `penalty_fields(kind: str, minutes: int) -> dict[str, str] | None`. It returns the `{"kind", "duration", "type"}` input fields, or `None` when the AHA kind or minutes have no BenchApp equivalent.
- Produces (`docs/benchapp-api-notes.md`), which Task 4 needs:
  - how the token is sent (header name and format)
  - where `x-user-id` comes from
  - which recorded query returns an event's `homeTeamScore`/`awayTeamScore`/`homeTeamShots`/`awayTeamShots` by ID
  - what the response shapes of the create mutations look like, i.e. where the new ID is

- [ ] **Step 1: Write `tools/extract_har_queries.py HAR OUT_DIR`.**
  - Strip the escapes (see Global Constraints) and parse the JSON.
  - For each GraphQL entry whose `operationName` is on the allowlist, write the first-seen `query` to `OUT_DIR/<operationName>.graphql`.
  - The allowlist: `login`, `eventListScreenContent`, `eventDetailsScreenContent`, `scorekeeperRostersPage`, `scorekeeperPointsAndPenalties`, `scorekeeperCreateRosterPlayer`, `scorekeeperSetAttendance`, `scorekeeperCreateEventPoint`, `scorekeeperCreateEventPenalty`, `scorekeeperCreateEventShot`, `scorekeeperSetPeriod`, `manageGoaliesCreateEventGoalie`, and `getTeamRecord`.
  - Also dump `variables` for entries 133 (point) and 565 (penalty) to `tests/fixtures/har_point_vars.json` and `har_penalty_vars.json`.
  - Run it: `./bin/python -I tools/extract_har_queries.py recon/benchapp.har src/aha_benchapp/graphql`. Expected: 13 files written.

- [ ] **Step 2: Auth.** With a throwaway script (don't commit it), send `login` and then `eventDetailsScreenContent(id: 3766427)`:
  - First try `Authorization: Bearer <token>`, then a bare `Authorization: <token>`.
  - Set `x-user-id` to the login `player.id`. If that's rejected, try the user ID from a `me` query.
  - Record what worked in the notes.

- [ ] **Step 3: Enums.**
  - Try an introspection query: `__type(name: "EventPeriod")`, `CreateEventPenaltyInput`'s field types, and the point `type` enum.
  - If introspection is disabled, search the website bundle for the enum literals near `"EvenStrength"`, `"Hooking"`, and `"Minor"`. The bundle is `https://www.benchapp.com/_expo/static/js/web/index-*.js`, and its exact URL is in the HAR.
  - Record every penalty `kind`, `duration`, and `type` value, and the overtime `EventPeriod` value.

- [ ] **Step 4: Shots-vs-goals rule.**
  - Read event 3766427's current shot totals.
  - From the HAR, compute net manual shots per team: shots created minus `scorekeeperDeleteEventShot` deletes. Look up which team each deleted shot ID belonged to; the gameSheet or points query may list shots. If a deleted shot can't be attributed, ask the manager to read the per-team shot totals off the BenchApp game page.
  - If the totals equal net manual shots plus goals, `GOAL_COUNTS_AS_SHOT = True`. If they equal net manual shots, it's `False`.
  - The website does **not** send a shot after a goal (verified in the HAR), so any automatic shot comes from the server.
  - If neither equation holds, stop and ask the manager whether a throwaway test event is OK to write to.

- [ ] **Step 5: Write `enums.py` and the failing tests.**

```python
def test_penalty_fields_hooking_minor():
    assert penalty_fields("Hooking", 2) == {"kind": "Hooking", "duration": "Two", "type": "Minor"}

def test_penalty_fields_matches_ignoring_case_and_spaces():
    assert penalty_fields("high sticking", 2)["kind"] == PENALTY_KINDS["highsticking"]   # use the real enum value

def test_penalty_fields_unknown_kind():
    assert penalty_fields("Being Too Handsome", 2) is None

def test_periods_cover_overtime():
    assert set(PERIOD) == set(Period)
```

  - AHA kind names are matched to the discovered `kind` enum by lowercasing and removing non-letters. `PENALTY_KINDS` is that normalized-name → enum-value map.
  - Minutes map to `duration` and `type` using the discovered enums: 2 → Minor, 4 → Double Minor if it exists (otherwise Minor), 5 → Major, 10 → Misconduct. Any other value returns `None`.

- [ ] **Step 6: Run `./bin/python -m pytest tests/test_enums.py -v`.** Expected: PASS once `enums.py` is filled in from steps 3 and 4.

- [ ] **Step 7: Commit.** Run `git add tools src/aha_benchapp/graphql src/aha_benchapp/enums.py docs/benchapp-api-notes.md tests`, then `git commit -m "feat: BenchApp queries, enums, API notes"`.

### Task 4: BenchApp GraphQL client

**Files:**
- Create: `src/aha_benchapp/benchapp.py`
- Test: `tests/test_benchapp.py`, plus trimmed response fixtures in `tests/fixtures/ba_*.json`. Make these from real responses captured in Task 3, with personal emails and phones removed.

**Interfaces:**
- Consumes: `src/aha_benchapp/graphql/*.graphql` and `docs/benchapp-api-notes.md` from Task 3.
- Produces:
  - `class BenchAppError(Exception)`. Its message includes the operation name and the full GraphQL `errors` array, or the HTTP status.
  - `class BenchAppClient(session: requests.Session, *, dry_run: bool = False)` with these methods:
    - `login(email: str, password: str) -> None`
    - `events(team_id: int) -> list[dict]`. Pages `eventListScreenContent` with `take=50` and `includePrevious=True` until a short page comes back, and returns the raw `Event` dicts.
    - `game_sheet(event_id: int) -> dict`, the raw `gameSheet`
    - `event_stats(event_id: int) -> dict`, with keys `homeTeamScore`, `awayTeamScore`, `homeTeamShots`, `awayTeamShots`, from the query named in the notes
    - `has_points(event_id: int) -> bool`
    - `create_roster_player(team_id: int, season_id: int, name: str, number: str) -> int`. Sends `class "Spare"` and `temp true`, and returns the `player.id`.
    - `set_attendance(event_id: int, season_id: int, team_id: int, player_id: int) -> None`. Sends status `"In"`.
    - `create_point(event_id: int, season_id: int, team_id: int, home_away: str, period: str, time: str, goal_player_id: int, assist_ids: tuple[int, ...]) -> int`. Always sends `type: "EvenStrength"`.
    - `create_penalty(event_id: int, season_id: int, team_id: int, home_away: str, period: str, time: str, player_id: int, fields: dict[str, str]) -> int`
    - `create_shot(event_id: int, team_id: int, period: str) -> int`
    - `create_goalie(event_id: int, team_id: int, player_id: int) -> int`. Sends period `"One"` and time `"00:00"`.
    - `set_period(event_id: int, period: str) -> None`
  - The `period` arguments are already BenchApp strings, taken from `enums.PERIOD`.

- [ ] **Step 1: Write the failing tests** (using `responses`).

```python
def test_create_point_sends_recorded_variables(client):
    expected = json.loads(fixture("har_point_vars.json"))["input"]
    client.create_point(3766427, 531381, 1247980, "Home", "One", "11:20", 900016, (900004,))
    sent = json.loads(responses.calls[-1].request.body)
    assert sent["operationName"] == "scorekeeperCreateEventPoint"
    assert sent["variables"]["input"] == expected

def test_requests_carry_auth_and_client_headers(client):
    client.event_stats(3766427)
    h = responses.calls[-1].request.headers
    assert h["x-graphql-client-name"] == "web" and h["x-graphql-client-version"] == "1.0.0"
    assert "x-user-id" in h and AUTH_HEADER in h      # AUTH_HEADER per the API notes

def test_graphql_errors_raise_with_details(client):
    # respond 200 with {"errors": [{"message": "Not authorized"}]}
    with pytest.raises(BenchAppError, match="Not authorized"):
        client.has_points(1)

def test_dry_run_mutations_send_nothing(dry_client):
    before = len(responses.calls)
    ids = [dry_client.create_shot(1, 2, "One") for _ in range(3)]
    assert len(responses.calls) == before
    assert ids == [-1, -2, -3]

def test_events_pages_until_short_page(client):
    # first page 50 events, second page 3 → 53 returned, 2 requests
```

  - The `client` fixture registers the login response and calls `login()`.
  - In `dry_run`, mutations log `DRY RUN <operationName> <variables json>` at INFO level and return negative IDs from a counter. `login` and all queries still really run.

- [ ] **Step 2: Run `./bin/python -m pytest tests/test_benchapp.py -v`.** Expected: FAIL.

- [ ] **Step 3: Implement `benchapp.py`.**
  - Load query text from the package's `graphql/` directory.
  - Use one private `_call(operation_name: str, variables: dict) -> dict` that returns `data` or raises `BenchAppError`.
  - Use a 20 s timeout on every request.
  - Send `x-graphql-client-route: /schedule/[eventId]` on every call. That's the route the scorekeeper calls came from.

- [ ] **Step 4: Run the tests.** Expected: PASS.

- [ ] **Step 5: Commit.** Run `git add src/aha_benchapp/benchapp.py tests`, then `git commit -m "feat: BenchApp GraphQL client with dry-run"`.

### Task 5: Matching

**Files:**
- Create: `src/aha_benchapp/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `BoxScore` and `AhaPlayer` (Task 1), and the raw event / gameSheet dict shapes (from the Task 4 fixtures).
- Produces:
  - `normalize_team(name: str) -> str`: lowercase, strip accents, drop punctuation, collapse spaces, and remove every token matching `^[a-z]\d+$` (division labels such as `c1`).
  - `event_candidates(events: list[dict], game_date: date, opponent: str, team_id: int, tz: ZoneInfo) -> list[dict]`. A candidate is an event whose local date equals `game_date` and whose non-us team (`homeTeam`/`awayTeam` with `id != team_id`) matches `opponent` under `normalize_team`.
  - `our_side(event: dict, team_id: int) -> str`, which returns `"Home"` or `"Away"`.
  - `resolve_ours(players: list[dict], p: AhaPlayer, *, goalie: bool = False) -> int | None`. Matches by `playerNumberDisplay` (stripped) equal to `p.number`, and only when exactly one player matches. With `goalie=True`, that player also needs `isGoalie`.
  - `resolve_opponent(players: list[dict], p: AhaPlayer) -> int | None`. Tries a unique number match, then a unique case-insensitive `playerName == p.name` match.
  - `players` is a `gameSheet.homeTeam.players` or `awayTeam.players` list. The ID is `playerId`.

- [ ] **Step 1: Write the failing tests.** Build event dicts with a small helper `make_event(id, full, home=(id, name), away=(id, name))`. Use the real `dateAndTime.full` format from the Task 4 fixtures; adjust the literals below if it isn't ISO-8601 UTC.

```python
def test_team_names_match_variants():                       # Review Focus 2
    assert normalize_team("Spartans C1 C1") == normalize_team("Spartans C1") == normalize_team("Spartans")
    assert normalize_team("Spartans B") != normalize_team("Spartans")
    assert normalize_team("Les Étoiles du Nord C1") == "les etoiles du nord"

def test_find_event_late_game_uses_team_timezone():         # Review Focus 1
    ev = make_event(1, "2026-11-09T05:30:00Z", home=(1247980, "Les Etoiles du Nord"), away=(1528241, "Moose"))
    assert event_candidates([ev], date(2026, 11, 8), "Moose C1", 1247980, CHICAGO) == [ev]

def test_event_candidates_wrong_opponent_or_date():
    ev = make_event(1, "2026-11-08T20:00:00Z", home=(1247980, "Les Etoiles du Nord"), away=(1, "Sled Dogs"))
    assert event_candidates([ev], date(2026, 11, 8), "Moose C1", 1247980, CHICAGO) == []
    assert event_candidates([ev], date(2026, 11, 9), "Sled Dogs C1", 1247980, CHICAGO) == []

def test_resolve_ours_by_number_ignores_name():
    roster = [sheet_player(900016, "5", "Bobby S"), sheet_player(900004, "24", "Kev")]
    assert resolve_ours(roster, AhaPlayer("5", "R. Steckman")) == 900016
    assert resolve_ours(roster, AhaPlayer("99", "X. Nobody")) is None

def test_resolve_ours_duplicate_number_is_unmatched():
    roster = [sheet_player(1, "5", "A"), sheet_player(2, "5", "B")]
    assert resolve_ours(roster, AhaPlayer("5", "A")) is None

def test_resolve_ours_zero_vs_double_zero():
    assert resolve_ours([sheet_player(1, "00", "A")], AhaPlayer("0", "A")) is None

def test_resolve_goalie_requires_is_goalie():
    roster = [sheet_player(7, "30", "Skater", is_goalie=False)]
    assert resolve_ours(roster, AhaPlayer("30", "T. Agerter"), goalie=True) is None

def test_opponent_player_reused_by_number():                 # Review Focus 5
    roster = [sheet_player(900021, "80", "R. Monge", temp=True)]
    assert resolve_opponent(roster, AhaPlayer("80", "R. Monge")) == 900021

def test_opponent_player_falls_back_to_name():
    roster = [sheet_player(5, "", "J. Bader")]
    assert resolve_opponent(roster, AhaPlayer("37", "J. Bader")) == 5
```

- [ ] **Step 2: Run `./bin/python -m pytest tests/test_matching.py -v`.** Expected: FAIL.

- [ ] **Step 3: Implement `matching.py`.** Use `unicodedata.normalize("NFKD", ...)` to strip accents.

- [ ] **Step 4: Run the tests.** Expected: PASS.

- [ ] **Step 5: Commit.** Run `git add src/aha_benchapp/matching.py tests/test_matching.py`, then `git commit -m "feat: event and player matching"`.

### Task 6: State store

**Files:**
- Create: `src/aha_benchapp/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `GameResult` and `Status` (Task 1).
- Produces:
  - `class StateStore(Protocol)`: `get(game_id: int) -> GameResult | None` and `put(result: GameResult) -> None`.
  - `class MemoryStateStore`, which is used by tests and by the CLI with `--no-state`.
  - `class DynamoStateStore(table_name: str, client=None)`. `client` defaults to `boto3.client("dynamodb")`. The item has the attributes `game_id` (N, partition key), `status` (S), `event_id` (N, omitted when None), `warnings` (L of S), `created_point_ids` (L of N), and `updated_at` (S, ISO-8601 UTC).

- [ ] **Step 1: Write the failing tests.** Use `botocore.stub.Stubber` on a real `boto3.client("dynamodb", region_name="us-east-1")`; don't add moto.

```python
def test_dynamo_round_trip():
    # stub put_item with the expected Item, then get_item returning it;
    # assert get(28819) == GameResult(28819, Status.NEEDS_CLEANUP, 3766427, ["score mismatch"], [11, 12])

def test_dynamo_get_missing_returns_none(): ...

def test_memory_store_round_trip(): ...
```

- [ ] **Step 2: Run `./bin/python -m pytest tests/test_state.py -v`.** Expected: FAIL.

- [ ] **Step 3: Implement `state.py`.** For the Stubber's expected params, match `updated_at` with `botocore.stub.ANY`.

- [ ] **Step 4: Run the tests.** Expected: PASS.

- [ ] **Step 5: Commit.** Run `git add src/aha_benchapp/state.py tests/test_state.py`, then `git commit -m "feat: DynamoDB game state store"`.

### Task 7: Sync one game

**Files:**
- Create: `src/aha_benchapp/sync.py`
- Test: `tests/test_sync.py` and `tests/fakes.py`. `FakeClient` implements the `BenchAppClient` methods. It records `(method, args)` in `.calls`, returns increasing IDs from creates, and can be configured with `stats`, `has_points_value`, `sheet`, and `fail_on=(method, nth)`.

**Interfaces:**
- Consumes: Tasks 1, 3 (`enums`), 4 (the client methods and `BenchAppError`), 5, and 6.
- Produces:
  - `@dataclass SyncContext(client: BenchAppClient, store: StateStore, team_id: int, aha_team_id: int, tz: ZoneInfo, dry_run: bool)`
  - `sync_game(ctx: SyncContext, box: BoxScore, events: list[dict]) -> GameResult`. It never raises `BenchAppError`; errors become statuses or warnings. Every `BenchAppError` is also logged at ERROR level with its full message, and `str(error)` is included in the warning, so API breakage shows up in the email.

`sync_game` does these steps in order:
1. **Find the event.** Use `event_candidates` with the opponent, which is the `box.teams` entry whose `team_ids` entry ≠ `aha_team_id`. Zero candidates → `FAILED_NO_EVENT`. More than one → `FAILED_AMBIGUOUS_EVENT`. Neither case makes any client mutations.
2. **Check for existing points.** If `client.has_points(event_id)`, the result is `SKIPPED_EXISTING`.
3. **Resolve players.**
   - Read `game_sheet` and take `season_id` from `event["season"]["id"]`. Map each AHA team to its sheet side via `our_side`.
   - Resolve every player in goals, penalties, and goalies, with each `AhaPlayer` once per team. Our team uses `resolve_ours`, with `goalie=True` for goalies. The opponent uses `resolve_opponent`.
   - Unresolved players get `create_roster_player` on their team and then `set_attendance`.
   - Each unresolved player on our team adds the warning `"#<n> <name> isn't on the BenchApp roster; entered as a temp player"`.
   - Unless `dry_run`, `store.put(IN_PROGRESS)` before the first mutation.
4. **Goals.** Call `create_point` for each AHA goal, in AHA order, appending each ID to `created_point_ids`. A `BenchAppError` before the first point means `FAILED_API_ERROR`. One after it means `NEEDS_CLEANUP`.
5. **Verify the score** (skipped in `dry_run`). If `event_stats` doesn't equal `box.final`, mapped to home/away, the result is `NEEDS_CLEANUP` with the warning `"score mismatch: BenchApp a–b, AHA c–d"`, and steps 6–9 are skipped.
6. **Goalies.** Call `create_goalie` for each team's goalie. A `BenchAppError` becomes a warning.
7. **Penalties.** Look up `penalty_fields`; `None` becomes the warning `"penalty '<kind>' (<n> min) has no BenchApp equivalent; skipped"`. Then call `create_penalty`. A `BenchAppError` becomes a warning.
8. **Shots.**
   - If `box.shots == {}`, add the warning `"AHA hasn't posted shots yet"` and do nothing more for shots.
   - Otherwise, for each team and period: `n = aha_shots − (that team's goals in that period if enums.GOAL_COUNTS_AS_SHOT else 0)`. If `n < 0`, set it to 0 and add a warning. Then call `create_shot` n times.
   - Afterward (not in dry run), if `event_stats` shot totals ≠ AHA totals, add a warning.
9. **Period.** Call `set_period(PERIOD[OT if any goal is in OT, else THREE])`.
10. **Status.** The result is `SYNCED`, or `SYNCED_WITH_WARNINGS` if there were any warnings. Unless `dry_run`, `store.put` the result.

- [ ] **Step 1: Write the failing tests.** Use the `aha_game_28819.html` BoxScore, and test with `ctx.aha_team_id = 308` and `ctx.team_id = 1247980`. The `FakeClient` sheet has our roster with numbers 5/10/12/16/21/24/36/37/43/55/80/89/94, plus number 30 marked `isGoalie`. The opponent roster starts empty. `stats` default to the AHA final (Les home 3, Moose away 4) and the AHA shot totals.

```python
def test_goals_written_before_anything_else(ctx, box):
    sync_game(ctx, box, [EVENT])
    names = [c[0] for c in ctx.client.calls]
    last_point = max(i for i, n in enumerate(names) if n == "create_point")
    assert names.count("create_point") == 7
    assert all(n not in ("create_penalty", "create_shot", "create_goalie") for n in names[:last_point])

def test_opponent_players_created_as_temp_and_marked_in(ctx, box):
    sync_game(ctx, box, [EVENT])
    created = [c[1] for c in ctx.client.calls if c[0] == "create_roster_player"]
    assert {a[3] for a in created} == {"80", "8", "92", "78", "25", "37", "23"}   # the same 7 the manager created
    assert sum(1 for c in ctx.client.calls if c[0] == "set_attendance") == 7

def test_synced_status_and_state(ctx, box):
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.SYNCED and ctx.store.get(28819).status is Status.SYNCED

def test_skips_event_with_existing_points(ctx, box):
    ctx.client.has_points_value = True
    assert sync_game(ctx, box, [EVENT]).status is Status.SKIPPED_EXISTING
    assert not any(c[0].startswith(("create", "set")) for c in ctx.client.calls)

def test_no_matching_event(ctx, box):
    assert sync_game(ctx, box, []).status is Status.FAILED_NO_EVENT

def test_unmatched_teammate_becomes_temp_with_warning(ctx, box):   # #43 removed from our roster
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.SYNCED_WITH_WARNINGS and any("#43" in w for w in r.warnings)

def test_sub_goalie_on_skater_number_becomes_temp(ctx, box):       # #30 present but isGoalie False
    r = sync_game(ctx, box, [EVENT])
    assert any(c[0] == "create_roster_player" and c[1][3] == "30" for c in ctx.client.calls)

def test_api_error_before_any_goal_is_retryable(ctx, box):         # fail_on=("create_point", 1)
    assert sync_game(ctx, box, [EVENT]).status is Status.FAILED_API_ERROR

def test_api_error_after_first_goal_needs_cleanup(ctx, box):       # fail_on=("create_point", 2)
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.NEEDS_CLEANUP and len(r.created_point_ids) == 1

def test_score_mismatch_needs_cleanup_and_stops(ctx, box):         # stats score 3–3
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.NEEDS_CLEANUP
    assert not any(c[0] in ("create_penalty", "create_shot") for c in ctx.client.calls)

@pytest.mark.parametrize("auto, expected_p1", [(True, 11), (False, 12)])
def test_shot_counts_respect_goal_rule(ctx, box, monkeypatch, auto, expected_p1):
    monkeypatch.setattr(sync.enums, "GOAL_COUNTS_AS_SHOT", auto)
    sync_game(ctx, box, [EVENT])
    assert shots_for(ctx, team=1247980, period="One") == expected_p1

def test_shots_clamped_when_goals_exceed_shots(ctx, box, monkeypatch):   # Review Focus 3
    monkeypatch.setattr(sync.enums, "GOAL_COUNTS_AS_SHOT", True)
    shots = {**box.shots, LES: {**box.shots[LES], Period.ONE: 0}}
    r = sync_game(ctx, replace(box, shots=shots), [EVENT])
    assert shots_for(ctx, team=1247980, period="One") == 0 and any("shots" in w for w in r.warnings)

def test_missing_shots_is_warning(ctx, box):                       # Review Focus 4
    r = sync_game(ctx, replace(box, shots={}), [EVENT])
    assert r.status is Status.SYNCED_WITH_WARNINGS and "create_shot" not in [c[0] for c in ctx.client.calls]

def test_unknown_penalty_kind_is_warning(ctx, box):                # penalty kind "Being Too Handsome"
    r = sync_game(ctx, replace(box, penalties=(replace(box.penalties[0], kind="Being Too Handsome"),)), [EVENT])
    assert "create_penalty" not in [c[0] for c in ctx.client.calls] and r.status is Status.SYNCED_WITH_WARNINGS

def test_goalie_failure_is_warning(ctx, box):                      # fail_on=("create_goalie", 1)
    assert sync_game(ctx, box, [EVENT]).status is Status.SYNCED_WITH_WARNINGS

def test_dry_run_writes_no_state(ctx, box):
    ctx.dry_run = True
    sync_game(ctx, box, [EVENT])
    assert ctx.store.get(28819) is None
```

`EVENT` is a Task 5–style event dict: ID 3766427, our team home, Moose away, Nov 8 Central, and season 531381. `shots_for` counts the recorded `create_shot` calls for a team and period.

- [ ] **Step 2: Run `./bin/python -m pytest tests/test_sync.py -v`.** Expected: FAIL.

- [ ] **Step 3: Implement `sync_game`** following steps 1–10 above. Keep each numbered step in its own private function, and reference `enums.GOAL_COUNTS_AS_SHOT` through the module so the test can monkeypatch it.

- [ ] **Step 4: Run the tests.** Expected: PASS.

- [ ] **Step 5: Commit.** Run `git add src/aha_benchapp/sync.py tests`, then `git commit -m "feat: sync one AHA game into BenchApp"`.

### Task 8: Run loop, notifications, config, Lambda handler, CLI

**Files:**
- Create: `src/aha_benchapp/config.py`, `src/aha_benchapp/notify.py`, `src/aha_benchapp/handler.py`, `src/aha_benchapp/__main__.py`
- Modify: `src/aha_benchapp/sync.py`, to add `run`
- Test: `tests/test_config.py`, `tests/test_notify.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces (`config.py`):
  - `@dataclass(frozen=True) Config(aha_team_url: str, aha_team_id: int, benchapp_team_id: int, secret_id: str | None, state_table: str | None, notify_topic_arn: str | None, tz: ZoneInfo, dry_run: bool)`
  - `Config.from_env(env: Mapping[str, str] = os.environ)`. `aha_team_id` is the URL's last path segment. `DRY_RUN` is true for `"true"`, `"1"`, or `"yes"`, case-insensitive. `TEAM_TIMEZONE` defaults to `America/Chicago`. A missing `AHA_TEAM_URL` or `BENCHAPP_TEAM_ID` raises `ValueError` naming the variable.
  - `load_credentials(secret_id: str, client=None) -> tuple[str, str]`. Reads the Secrets Manager JSON `{"email", "password"}`.
- Produces (`notify.py`):
  - `should_notify(result: GameResult, prior: Status | None) -> bool`. True when the status is neither `SYNCED` nor `SKIPPED_EXISTING`, and it differs from `prior`.
  - `format_summary(results: list[GameResult]) -> str`. One block per game: the `https://www.ahahockey.com/game/<id>` URL, the status, the BenchApp event ID, each warning as a bulleted line, and for `NEEDS_CLEANUP` the created point IDs.
  - `class SnsNotifier(topic_arn: str, client=None)` and `class LogNotifier`, each with `notify(results: list[GameResult], priors: dict[int, Status | None]) -> None`. They send or log only results where `should_notify` is true, and do nothing if there are none. The SNS subject is `"AHA → BenchApp: <n> game(s) need a look"`.
- Produces (`sync.py`): `run(cfg: Config, *, session: requests.Session, client: BenchAppClient, store: StateStore, notifier, credentials: Callable[[], tuple[str, str]], only_game: int | None = None) -> list[GameResult]`
- Produces: `handler.lambda_handler(event, context) -> dict` and the CLI `python -m aha_benchapp [--game ID] [--dry-run]`.

`run` works like this:
- Get game IDs from `completed_game_ids(fetch(cfg.aha_team_url))`, or `[only_game]`.
- For each ID, look at the prior state:
  - `IN_PROGRESS` becomes a `NEEDS_CLEANUP` result with the warning `"a previous run stopped partway through this game"`. Store it unless dry run, and make no BenchApp calls for it.
  - A prior status that isn't retryable means the game is skipped.
- Keep at most 20 pending games. If none are pending, return without logging in.
- Otherwise `client.login(*credentials())` once and `client.events(cfg.benchapp_team_id)` once. Then, for each pending game, fetch and parse it, and pass it to `sync_game`.
  - `ParseError` becomes `FAILED_PARSE_ERROR` with the message as a warning.
  - `ManualEntryRequired` becomes `FAILED_MANUAL_ENTRY` with the reason as a warning.
  - Both are stored unless dry run.
- Finish with `notifier.notify(results, priors)`.
- Login errors propagate.

`lambda_handler` builds a `Config` from the env and wires up the real dependencies. In dry run, it uses a `LogNotifier` in place of SNS (my call: no emails while trialing). If `run` raises, it publishes `"AHA → BenchApp sync failed: <error>"` to SNS and re-raises, so the invocation shows as failed. It returns `{"results": [{"game_id", "status", "warnings"}...]}`.

The CLI reads the same env vars, but its credentials come from `BENCHAPP_EMAIL`/`BENCHAPP_PASSWORD`, and it always uses `MemoryStateStore` and `LogNotifier`. A live CLI run that records no state is still safe: the Lambda will see the points later and skip that game. It logs at INFO level to stderr, and prints `format_summary` of all results.

- [ ] **Step 1: Write the failing tests.** Serve the AHA pages with `responses` from the fixtures. Use `FakeClient`, `MemoryStateStore`, and a `FakeNotifier` that records its calls.

```python
def test_config_from_env():
    cfg = Config.from_env({"AHA_TEAM_URL": "https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308",
                           "BENCHAPP_TEAM_ID": "1247980", "DRY_RUN": "True"})
    assert cfg.aha_team_id == 308 and cfg.benchapp_team_id == 1247980 and cfg.dry_run
    assert cfg.tz == ZoneInfo("America/Chicago")

def test_config_missing_var_named():
    with pytest.raises(ValueError, match="BENCHAPP_TEAM_ID"):
        Config.from_env({"AHA_TEAM_URL": "https://x/team/a/1"})

@pytest.mark.parametrize("status, prior, expected", [
    (Status.SYNCED, None, False), (Status.SKIPPED_EXISTING, None, False),
    (Status.SYNCED_WITH_WARNINGS, None, True), (Status.FAILED_NO_EVENT, None, True),
    (Status.FAILED_NO_EVENT, Status.FAILED_NO_EVENT, False)])
def test_should_notify(status, prior, expected): ...

def test_format_summary_lists_warnings_and_cleanup_ids():
    text = format_summary([GameResult(28819, Status.NEEDS_CLEANUP, 3766427, ["score mismatch: BenchApp 3–3, AHA 3–4"], [11, 12])])
    assert "https://www.ahahockey.com/game/28819" in text and "score mismatch" in text and "11, 12" in text

def test_run_skips_games_already_done(deps):             # store has 28852 SYNCED
    results = run(cfg, **deps)
    assert [r.game_id for r in results] == [28819]

def test_run_in_progress_becomes_needs_cleanup(deps):    # store has 28819 IN_PROGRESS
    results = run(cfg, **deps)
    assert results[0].status is Status.NEEDS_CLEANUP
    assert not any(c[0].startswith(("create", "set")) for c in deps["client"].calls)

def test_run_nothing_pending_does_not_login(deps):       # both games SYNCED in store
    assert run(cfg, **deps) == [] and deps["client"].calls == []

def test_run_caps_at_20_games(deps, monkeypatch):        # completed_game_ids → 25 IDs
    assert len(run(cfg, **deps)) == 20

def test_run_parse_error_recorded_and_retried(deps):     # game page served as "<html></html>"
    assert run(cfg, **deps)[0].status is Status.FAILED_PARSE_ERROR
    assert deps["store"].get(28819).status.retryable

def test_run_manual_entry_recorded(deps):                # game page = aha_game_shootout.html
    r = run(cfg, **deps)[0]
    assert r.status is Status.FAILED_MANUAL_ENTRY and "shootout" in r.warnings[0]

def test_handler_notifies_and_reraises_on_login_failure(monkeypatch): ...
```

- [ ] **Step 2: Run `./bin/python -m pytest tests -v`.** Expected: the new tests FAIL, and everything earlier still passes.

- [ ] **Step 3: Implement `config.py`, `notify.py`, `run`, `handler.py`, and `__main__.py`.** Use `argparse` for the CLI.

- [ ] **Step 4: Run `./bin/python -m pytest tests -v`.** Expected: all tests PASS.

- [ ] **Step 5: Do a live dry run with the manager.** The manager exports `BENCHAPP_EMAIL`, `BENCHAPP_PASSWORD`, `AHA_TEAM_URL`, and `BENCHAPP_TEAM_ID=1247980`, then runs `PYTHONPATH=src ./bin/python -m aha_benchapp --dry-run`. Expected:
  - 28819 reports `skipped-existing`, since the manager scored it.
  - Each other completed game either lists `DRY RUN scorekeeperCreateEventPoint ...` lines with the right player IDs, or reports `skipped-existing`.
  - The manager confirms the logged plan looks right.

- [ ] **Step 6: Commit.** Run `git add src/aha_benchapp tests`, then `git commit -m "feat: run loop, notifications, Lambda handler, CLI"`.

### Task 9: SAM deployment and dry-run rollout

Steps 3 onward act on the manager's AWS account and BenchApp team. **Confirm with the manager before each of them.** The manager runs any command that involves their password.

**Files:**
- Create: `template.yaml`, and `samconfig.toml` (written by `sam deploy --guided`)

**Interfaces:**
- Consumes: `aha_benchapp.handler.lambda_handler` (Task 8) and the env var names in Global Constraints.

`template.yaml` (`Transform: AWS::Serverless-2016-10-31`) contains:
- **Parameters:**
  - `AhaTeamUrl`, default `https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308`
  - `BenchAppTeamId`, default `1247980`
  - `SecretId`, default `aha-benchapp/credentials`
  - `NotifyEmail`, no default
  - `DryRun`, allowed values `true`/`false`, default `true`
- **`SyncFunction` (`AWS::Serverless::Function`):**
  - `CodeUri: src`, `Handler: aha_benchapp.handler.lambda_handler`, `Runtime: python3.12`
  - `Architectures: [arm64]`, `Timeout: 300`, `MemorySize: 256`
  - Environment: all seven env vars from Global Constraints. `STATE_TABLE` and `NOTIFY_TOPIC_ARN` are `!Ref`s, and `TEAM_TIMEZONE` is `America/Chicago`.
  - Policies: `DynamoDBCrudPolicy` on the table, `SNSPublishMessagePolicy` on the topic, and `AWSSecretsManagerGetSecretValuePolicy` with `SecretArn: !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:${SecretId}-*`
  - Events: `Daily`, of type `ScheduleV2`, with `ScheduleExpression: cron(0 8 * * ? *)` and `ScheduleExpressionTimezone: America/Chicago`
- **`StateTable` (`AWS::DynamoDB::Table`):** `BillingMode: PAY_PER_REQUEST`, with key `game_id` of type `N` (`HASH`).
- **`NotifyTopic` (`AWS::SNS::Topic`):** one email `Subscription` to `!Ref NotifyEmail`.
- **Log group:** `AWS::Logs::LogGroup` for the function, with `RetentionInDays: 30`.

- [ ] **Step 1: Write `template.yaml`.** Then run `sam validate --lint`. Expected: `template.yaml is a valid SAM Template` with no lint errors. (If `sam` isn't installed, ask the manager to install it with `brew install aws-sam-cli`.)

- [ ] **Step 2: Build.** Run `sam build`. Expected: `Build Succeeded`, and `.aws-sam/build/SyncFunction/aha_benchapp/graphql/` contains the 13 `.graphql` files.

- [ ] **Step 3: The manager creates the secret** in their own terminal:
  `aws secretsmanager create-secret --name aha-benchapp/credentials --secret-string '{"email":"…","password":"…"}'`

- [ ] **Step 4: The manager deploys in dry-run mode** with `sam deploy --guided`. Use stack name `aha-benchapp-sync`, keep `DryRun=true`, and give their email for `NotifyEmail`. Then they confirm the SNS subscription email that AWS sends.

- [ ] **Step 5: Do a dry-run invoke.** Run `sam remote invoke SyncFunction --stack-name aha-benchapp-sync`, then `sam logs -n SyncFunction --stack-name aha-benchapp-sync`. Expected:
  - The same plan as the Task 8 local dry run.
  - No `create`/`set` requests sent: only `DRY RUN` log lines.
  - The DynamoDB table is still empty.

- [ ] **Step 6: Go live, after the manager reviews Step 5's logs and says go.**
  - Run `sam deploy --parameter-overrides DryRun=false`, then invoke once more.
  - Expected: each previously unscored game shows in BenchApp with the AHA final score and the record updated (`getTeamRecord`). Shot totals equal AHA's, and the DynamoDB items read `synced` or `synced-with-warnings`.
  - The manager spot-checks one game's box score in BenchApp against AHA.

- [ ] **Step 7: Commit.** Run `git add template.yaml samconfig.toml`, then `git commit -m "feat: SAM deployment for daily sync"`.
