"""Copy AHA box scores into BenchApp's scorekeeper."""
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn

import requests

from aha_benchapp import enums
from aha_benchapp.aha import completed_game_ids, fetch, parse_game
from aha_benchapp.benchapp import BenchAppClient, BenchAppError
from aha_benchapp.config import Config
from aha_benchapp.matching import (event_candidates, find_our_temp, number_shared_by, our_side, resolve_opponent,
                                   resolve_ours)
from aha_benchapp.models import AhaPlayer, BoxScore, GameResult, ManualEntryRequired, Period, Status
from aha_benchapp.state import StateStore

log = logging.getLogger(__name__)


@dataclass
class SyncContext:
    client: BenchAppClient
    store: StateStore
    team_id: int  # our BenchApp team
    aha_team_id: int
    dry_run: bool


@dataclass(frozen=True)
class _Side:
    """One team in the game, as BenchApp knows it."""
    team_id: int
    home_away: str
    players: list[dict]
    ours: bool


class _Stop(Exception):
    """Ends the sync early with `status`."""

    def __init__(self, status: Status):
        self.status = status


def sync_game(ctx: SyncContext, box: BoxScore, events: list[dict]) -> GameResult:
    """Write one AHA game into its BenchApp event. Errors become statuses or warnings, never exceptions."""
    result = GameResult(box.game_id, Status.IN_PROGRESS, warnings=list(box.parse_warnings))
    try:
        _GameSync(ctx, box, result).run(events)
    except _Stop as stop:
        result.status = stop.status
    except Exception as error:  # a surprise anywhere else: don't take the rest of the run down with it
        log.exception("game %s: unexpected error", box.game_id)
        result.warnings.append(_describe(error))
        result.status = Status.NEEDS_CLEANUP if result.created_point_ids else Status.FAILED_API_ERROR
    else:
        result.status = Status.SYNCED_WITH_WARNINGS if result.warnings else Status.SYNCED
    _save(ctx, result)
    return result


def _describe(error: Exception) -> str:
    return str(error) if isinstance(error, BenchAppError) else f"unexpected {type(error).__name__}: {error}"


def _save(ctx: SyncContext, result: GameResult) -> None:
    if not ctx.dry_run:
        ctx.store.put(result)


class _GameSync:
    def __init__(self, ctx: SyncContext, box: BoxScore, result: GameResult):
        self.ctx, self.box, self.result = ctx, box, result
        self.client = ctx.client
        self.event: dict = {}
        self.season_id = 0
        self.sides: dict[str, _Side] = {}  # AHA team name -> side
        self.player_ids: dict[tuple[str, AhaPlayer, bool], int] = {}

    def run(self, events: list[dict]) -> None:
        self._find_event(events)
        self._read_event()
        _save(self.ctx, self.result)  # in-progress marker before the first write
        self._resolve_players()
        self._write_goals()
        self._verify_score()
        self._write_goalies()
        self._write_penalties()
        self._write_shots()
        self._set_period()

    # 1–2: find the event and make sure it's untouched

    def _find_event(self, events: list[dict]) -> None:
        opponents = [team for team, aha_id in zip(self.box.teams, self.box.team_ids) if aha_id != self.ctx.aha_team_id]
        if len(opponents) != 1:
            self.result.warnings.append(f"AHA team {self.ctx.aha_team_id} isn't in this game")
            raise _Stop(Status.FAILED_PARSE_ERROR)
        candidates = event_candidates(events, self.box.date, opponents[0], self.ctx.team_id)
        if not candidates:
            self.result.warnings.append(f"no BenchApp game on {self.box.date} vs {opponents[0]}")
            raise _Stop(Status.FAILED_NO_EVENT)
        if len(candidates) > 1:
            ids = ", ".join(str(e["id"]) for e in candidates)
            self.result.warnings.append(f"several BenchApp games on {self.box.date} vs {opponents[0]}: {ids}")
            raise _Stop(Status.FAILED_AMBIGUOUS_EVENT)
        self.event = candidates[0]
        self.result.event_id = self.event["id"]
        self.season_id = self.event["season"]["id"]

    def _read_event(self) -> None:
        try:
            if self.client.has_scorekeeping(self.event["id"]):
                raise _Stop(Status.SKIPPED_EXISTING)
            sheet = self.client.game_sheet(self.event["id"])
        except _Stop:
            raise
        except Exception as error:
            self._api_failure(error, Status.FAILED_API_ERROR)
        our_home_away = our_side(self.event, self.ctx.team_id)
        sheet_sides = {"Home": sheet["homeTeam"], "Away": sheet["awayTeam"]}
        for team, aha_id in zip(self.box.teams, self.box.team_ids):
            ours = aha_id == self.ctx.aha_team_id
            home_away = our_home_away if ours else ("Away" if our_home_away == "Home" else "Home")
            sheet_side = sheet_sides[home_away]
            self.sides[team] = _Side(sheet_side["team"]["id"], home_away, sheet_side["players"], ours)

    # 3: players

    def _resolve_players(self) -> None:
        needed: list[tuple[str, AhaPlayer, bool]] = []
        for goal in self.box.goals:
            needed += [(goal.team, p, False) for p in (goal.scorer, *goal.assists)]
        needed += [(p.team, p.player, False) for p in self.box.penalties if p.player]
        needed += [(team, player, True) for team, player in self.box.goalies.items()]
        try:
            for key in dict.fromkeys(needed):
                self._resolve(*key)
        except Exception as error:
            self._api_failure(error, Status.FAILED_API_ERROR)

    def _resolve(self, team: str, player: AhaPlayer, goalie: bool) -> None:
        side = self.sides[team]
        key = (team, player, goalie and side.ours)
        if key in self.player_ids:
            return
        if side.ours:
            player_id = resolve_ours(side.players, player, goalie=goalie)
            if player_id is None and (player_id := find_our_temp(side.players, player)) is not None:
                self.result.warnings.append(
                    f"#{player.number} {player.name} isn't on the BenchApp roster; credited to their existing temp player"
                )
        else:
            player_id = resolve_opponent(side.players, player)
        if player_id is None:
            player_id = self.client.create_roster_player(side.team_id, self.season_id, player.name, player.number)
            self.client.set_attendance(self.event["id"], self.season_id, side.team_id, player_id)
            if side.ours:
                self.result.warnings.append(f"#{player.number} {player.name} {self._why_unmatched(side, player)}; "
                                            "entered as a temp player")
        elif side.ours and self._status(side, player_id) != "In":
            self.client.set_attendance(self.event["id"], self.season_id, side.team_id, player_id)
        self.player_ids[key] = player_id

    @staticmethod
    def _why_unmatched(side: _Side, player: AhaPlayer) -> str:
        shared = number_shared_by(side.players, player)
        if shared > 1:
            return f"couldn't be told apart: {shared} BenchApp players share #{player.number}"
        return "isn't on the BenchApp roster"

    @staticmethod
    def _status(side: _Side, player_id: int) -> str:
        return next(p["status"] for p in side.players if p["playerId"] == player_id)

    def _id(self, team: str, player: AhaPlayer, goalie: bool = False) -> int:
        return self.player_ids[(team, player, goalie and self.sides[team].ours)]

    # 4–5: goals, then confirm the score

    def _write_goals(self) -> None:
        for goal in self.box.goals:
            side = self.sides[goal.team]
            try:
                point_id = self.client.create_point(
                    self.event["id"], self.season_id, side.team_id, side.home_away, enums.PERIOD[goal.period],
                    goal.time, self._id(goal.team, goal.scorer), tuple(self._id(goal.team, a) for a in goal.assists),
                    point_type=enums.point_type(goal.strength),
                )
            except Exception as error:
                self._api_failure(error, Status.NEEDS_CLEANUP if self._may_have_written() else Status.FAILED_API_ERROR)
            self.result.created_point_ids.append(point_id)
            _save(self.ctx, self.result)

    def _may_have_written(self) -> bool:
        """After a failed goal write: could BenchApp hold some of this game now? (A timeout may still land.)"""
        if self.result.created_point_ids:
            return True
        try:
            return self.client.has_scorekeeping(self.event["id"])
        except Exception:
            return True  # can't tell, so assume the worst

    def _verify_score(self) -> None:
        if self.ctx.dry_run:
            return
        try:
            stats = self.client.event_stats(self.event["id"])
        except Exception as error:
            self._api_failure(error, Status.NEEDS_CLEANUP)
        expected = {side.home_away: self.box.final[team] for team, side in self.sides.items()}
        actual = {"Home": stats["homeTeamScore"], "Away": stats["awayTeamScore"]}
        if actual != expected:
            self.result.warnings.append(
                f"score mismatch: BenchApp {actual['Home']}–{actual['Away']}, AHA {expected['Home']}–{expected['Away']}"
            )
            raise _Stop(Status.NEEDS_CLEANUP)

    # 6–9: best effort; failures become warnings

    def _write_goalies(self) -> None:
        for team, goalie in self.box.goalies.items():
            self._best_effort(f"goalie {goalie.name}", lambda: self.client.create_goalie(
                self.event["id"], self.sides[team].team_id, self._id(team, goalie, goalie=True)))

    def _write_penalties(self) -> None:
        for penalty in self.box.penalties:
            label = f"penalty '{penalty.kind}' ({penalty.minutes} min)"
            fields = enums.penalty_fields(penalty.kind, penalty.minutes, bench=penalty.player is None)
            if fields is None:
                self.result.warnings.append(f"{label} has no BenchApp equivalent; skipped")
                continue
            side = self.sides[penalty.team]
            player_id = None if penalty.player is None else self._id(penalty.team, penalty.player)
            self._best_effort(label, lambda: self.client.create_penalty(
                self.event["id"], self.season_id, side.team_id, side.home_away, enums.PERIOD[penalty.period],
                penalty.time, player_id, fields))

    def _write_shots(self) -> None:
        if not self.box.shots:
            self.result.warnings.append("AHA hasn't posted shots yet")
            return
        for team, by_period in self.box.shots.items():
            side = self.sides[team]
            for period, aha_shots in by_period.items():
                goals = sum(1 for g in self.box.goals if g.team == team and g.period is period)
                count = aha_shots - (goals if enums.GOAL_COUNTS_AS_SHOT else 0)
                if count < 0:
                    self.result.warnings.append(f"{team} has more goals than shots in period {period.name}; entered no shots")
                    count = 0
                for _ in range(count):
                    if not self._best_effort(f"{team} shots", lambda: self.client.create_shot(
                            self.event["id"], side.team_id, enums.PERIOD[period])):
                        break
        self._verify_shots()

    def _verify_shots(self) -> None:
        if self.ctx.dry_run:
            return
        try:
            stats = self.client.event_stats(self.event["id"])
        except Exception as error:
            self.result.warnings.append(f"couldn't check shot totals: {_describe(error)}")
            return
        for team, side in self.sides.items():
            expected = sum(self.box.shots.get(team, {}).values())
            actual = stats[f"{side.home_away.lower()}TeamShots"]
            if actual != expected:
                self.result.warnings.append(f"{team} shot total is {actual} in BenchApp, {expected} on AHA")

    def _set_period(self) -> None:
        last = Period.OT if any(g.period is Period.OT for g in self.box.goals) else Period.THREE
        self._best_effort("setting the period", lambda: self.client.set_period(self.event["id"], enums.PERIOD[last]))

    # helpers

    def _best_effort(self, label: str, write) -> bool:
        try:
            write()
            return True
        except Exception as error:
            log.error("game %s: %s failed: %s", self.box.game_id, label, _describe(error))
            self.result.warnings.append(f"{label} failed: {_describe(error)}")
            return False

    def _api_failure(self, error: Exception, status: Status) -> NoReturn:
        log.error("game %s: %s", self.box.game_id, _describe(error))
        self.result.warnings.append(_describe(error))
        raise _Stop(status)


MAX_GAMES_PER_RUN = 20
MIN_SECONDS_PER_GAME = 120  # headroom for one game's ~100 BenchApp calls
LOCK_SECONDS = 1000  # longer than the 900 s Lambda timeout


def run(cfg: Config, *, session: requests.Session, client: BenchAppClient, store: StateStore, notifier,
        credentials: Callable[[], tuple[str, str]], only_game: int | None = None,
        seconds_left: Callable[[], float] | None = None) -> list[GameResult]:
    """Sync every completed AHA game that isn't finished in state. Login errors propagate.

    With `seconds_left`, stops before a game that might not finish in time; the
    rest are picked up next run.
    """
    game_ids = [only_game] if only_game else completed_game_ids(fetch(cfg.aha_team_url, session))
    stored = {game_id: store.get(game_id) for game_id in game_ids}
    priors = {game_id: prior.status if prior else None for game_id, prior in stored.items()}
    todo = [g for g, prior in stored.items() if prior is None or prior.status.retryable or prior.status is Status.IN_PROGRESS]
    results: list[GameResult] = []
    if not todo:
        return results

    owner = uuid.uuid4().hex
    if not cfg.dry_run and not store.acquire_lock(owner, LOCK_SECONDS):
        log.warning("another sync is running; leaving %d game(s) for the next run", len(todo))
        return results
    try:
        pending = []
        for game_id in todo:
            prior = stored[game_id]
            if prior is not None and prior.status is Status.IN_PROGRESS:
                prior.status = Status.NEEDS_CLEANUP
                prior.warnings.insert(0, "a previous run stopped partway through this game")
                _save_result(cfg, store, prior)
                results.append(prior)
            else:
                pending.append(game_id)
        if pending:
            _sync_pending(cfg, session, client, store, credentials, pending[:MAX_GAMES_PER_RUN], seconds_left, results)
    finally:
        if not cfg.dry_run:
            store.release_lock(owner)

    notifier.notify(results, {r.game_id: priors.get(r.game_id) for r in results})
    return results


def _save_result(cfg: Config, store: StateStore, result: GameResult) -> None:
    if not cfg.dry_run:
        store.put(result)


def _sync_pending(cfg: Config, session: requests.Session, client: BenchAppClient, store: StateStore,
                  credentials: Callable[[], tuple[str, str]], pending: list[int],
                  seconds_left: Callable[[], float] | None, results: list[GameResult]) -> None:
    client.login(*credentials())
    events = client.events(cfg.benchapp_team_id)
    ctx = SyncContext(client, store, cfg.benchapp_team_id, cfg.aha_team_id, cfg.dry_run)
    for game_id in pending:
        if seconds_left and seconds_left() < MIN_SECONDS_PER_GAME:
            log.warning("stopping before game %s to stay within the time limit", game_id)
            break
        results.append(_sync_one(ctx, session, game_id, events))
        log.info("game %s: %s", game_id, results[-1].status.value)


def _sync_one(ctx: SyncContext, session: requests.Session, game_id: int, events: list[dict]) -> GameResult:
    try:
        box = parse_game(game_id, fetch(f"https://www.ahahockey.com/game/{game_id}", session))
    except ManualEntryRequired as reason:
        result = GameResult(game_id, Status.FAILED_MANUAL_ENTRY, warnings=[f"enter this game by hand: {reason}"])
    except Exception as error:  # an unreadable page must not stop the other games
        log.exception("game %s: couldn't read the AHA page", game_id)
        result = GameResult(game_id, Status.FAILED_PARSE_ERROR, warnings=[f"couldn't read the AHA page: {error}"])
    else:
        return sync_game(ctx, box, events)
    _save(ctx, result)
    return result
