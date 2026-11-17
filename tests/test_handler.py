import pytest

from aha_benchapp import handler
from aha_benchapp.benchapp import BenchAppError
from aha_benchapp.models import GameResult, Status

ENV = {
    "AHA_TEAM_URL": "https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308",
    "BENCHAPP_TEAM_ID": "1247980",
    "BENCHAPP_CREDENTIALS_PARAMETER": "/aha-benchapp/credentials",
    "STATE_TABLE": "state",
    "NOTIFY_TOPIC_ARN": "arn:topic",
}


class RecordingSns:
    def __init__(self, topic_arn, client=None):
        self.published = PUBLISHED

    def notify(self, results, priors):
        pass

    def publish(self, subject, message):
        self.published.append((subject, message))


PUBLISHED: list = []


@pytest.fixture(autouse=True)
def wiring(monkeypatch):
    PUBLISHED.clear()
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(handler, "SnsNotifier", RecordingSns)
    monkeypatch.setattr(handler, "DynamoStateStore", lambda table: object())


def test_handler_returns_results(monkeypatch):
    monkeypatch.setattr(handler, "run", lambda cfg, **deps: [GameResult(28852, Status.SYNCED_WITH_WARNINGS, warnings=["w"])])
    assert handler.lambda_handler({}, None) == {
        "results": [{"game_id": 28852, "status": "synced-with-warnings", "warnings": ["w"]}]
    }


def test_handler_notifies_and_reraises_on_login_failure(monkeypatch):
    def failing_run(cfg, **deps):
        raise BenchAppError('login: [{"message": "Invalid email or password"}]')

    monkeypatch.setattr(handler, "run", failing_run)
    with pytest.raises(BenchAppError):
        handler.lambda_handler({}, None)
    assert PUBLISHED == [("AHA → BenchApp sync failed", 'login: [{"message": "Invalid email or password"}]')]


def test_handler_dry_run_uses_log_notifier(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    seen = {}

    def recording_run(cfg, **deps):
        seen.update(deps)
        return []

    monkeypatch.setattr(handler, "run", recording_run)
    handler.lambda_handler({}, None)
    assert isinstance(seen["notifier"], handler.LogNotifier)


def test_handler_passes_lambda_time_budget(monkeypatch):
    seen = {}

    def recording_run(cfg, **deps):
        seen["left"] = deps["seconds_left"]()
        return []

    class Context:
        def get_remaining_time_in_millis(self):
            return 250_000

    monkeypatch.setattr(handler, "run", recording_run)
    handler.lambda_handler({}, Context())
    assert seen["left"] == 250.0
