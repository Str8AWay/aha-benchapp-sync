import pytest
import requests
import responses

from aha_benchapp.config import Config
from aha_benchapp.models import GameResult, Status
from aha_benchapp.state import MemoryStateStore
from aha_benchapp.sync import run
from tests import fixture
from tests.fakes import LES_BA, FakeClient

TEAM_URL = "https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308"
CFG = Config(TEAM_URL, 308, LES_BA, None, None, None, False)


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, results, priors):
        self.calls.append((results, priors))


@pytest.fixture
def aha():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(responses.GET, TEAM_URL, body=fixture("aha_team_308.html"))
        yield mock


def serve_game(aha, game_id, name=None):
    aha.add(responses.GET, f"https://www.ahahockey.com/game/{game_id}", body=fixture(name or f"aha_game_{game_id}.html"))


@pytest.fixture
def deps(aha):
    return {"session": requests.Session(), "client": FakeClient(), "store": MemoryStateStore(),
            "notifier": FakeNotifier(), "credentials": lambda: ("me@example.com", "pw")}


def test_run_syncs_completed_games(aha, deps):
    serve_game(aha, 28819)
    serve_game(aha, 28852)
    results = run(CFG, **deps)
    assert [(r.game_id, r.status) for r in results] == [(28819, Status.SYNCED), (28852, Status.SYNCED)]
    assert deps["client"].names().count("login") == 1 and deps["client"].names().count("events") == 1
    assert deps["notifier"].calls == [(results, {28819: None, 28852: None})]


def test_run_skips_games_already_done(aha, deps):
    deps["store"].put(GameResult(28852, Status.SYNCED))
    serve_game(aha, 28819)
    assert [r.game_id for r in run(CFG, **deps)] == [28819]


def test_run_retries_retryable_failures(aha, deps):
    deps["store"].put(GameResult(28852, Status.SYNCED))
    deps["store"].put(GameResult(28819, Status.FAILED_NO_EVENT))
    serve_game(aha, 28819)
    results = run(CFG, **deps)
    assert results[0].status is Status.SYNCED
    assert deps["notifier"].calls[0][1] == {28819: Status.FAILED_NO_EVENT}


def test_run_in_progress_becomes_needs_cleanup(aha, deps):
    deps["store"].put(GameResult(28852, Status.SYNCED))
    deps["store"].put(GameResult(28819, Status.IN_PROGRESS, 3766427, created_point_ids=[5]))
    results = run(CFG, **deps)
    assert results[0].status is Status.NEEDS_CLEANUP
    assert results[0].created_point_ids == [5] and "partway" in results[0].warnings[0]
    assert deps["store"].get(28819).status is Status.NEEDS_CLEANUP
    assert not any(n.startswith(("create", "set")) for n in deps["client"].names())


def test_run_nothing_pending_does_not_login(deps):
    for game_id in (28819, 28852):
        deps["store"].put(GameResult(game_id, Status.SYNCED))
    assert run(CFG, **deps) == []
    assert deps["client"].calls == []


def test_run_caps_at_20_games(aha, deps, monkeypatch):
    monkeypatch.setattr("aha_benchapp.sync.completed_game_ids", lambda html: list(range(1, 26)))
    for game_id in range(1, 26):
        serve_game(aha, game_id, "aha_game_28819.html")
    assert len(run(CFG, **deps)) == 20


def test_run_parse_error_recorded_and_retried(aha, deps):
    deps["store"].put(GameResult(28852, Status.SYNCED))
    aha.add(responses.GET, "https://www.ahahockey.com/game/28819", body="<html></html>")
    assert run(CFG, **deps)[0].status is Status.FAILED_PARSE_ERROR
    assert deps["store"].get(28819).status.retryable


def test_run_manual_entry_recorded(aha, deps):
    deps["store"].put(GameResult(28852, Status.SYNCED))
    serve_game(aha, 28819, "aha_game_shootout.html")
    r = run(CFG, **deps)[0]
    assert r.status is Status.FAILED_MANUAL_ENTRY and "shootout" in r.warnings[0]
    assert not deps["store"].get(28819).status.retryable


def test_run_only_game(aha, deps):
    serve_game(aha, 28852)
    assert [r.game_id for r in run(CFG, **deps, only_game=28852)] == [28852]


def test_run_dry_run_stores_nothing(aha, deps):
    serve_game(aha, 28819)
    serve_game(aha, 28852)
    run(Config(TEAM_URL, 308, LES_BA, None, None, None, True), **deps)
    assert deps["store"].get(28819) is None and deps["store"].get(28852) is None
