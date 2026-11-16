import json

from aha_benchapp import __main__ as cli
from aha_benchapp.models import GameResult, Status
from aha_benchapp.notify import LogNotifier
from aha_benchapp.state import MemoryStateStore


def test_cli_passes_game_and_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("AHA_TEAM_URL", "https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308")
    monkeypatch.setenv("BENCHAPP_TEAM_ID", "1247980")
    monkeypatch.delenv("DRY_RUN", raising=False)
    seen = {}

    def fake_run(cfg, **deps):
        seen.update(cfg=cfg, **deps)
        return [GameResult(28852, Status.SYNCED, 3766428)]

    monkeypatch.setattr(cli, "run", fake_run)
    assert cli.main(["--game", "28852", "--dry-run"]) == 0
    assert seen["cfg"].dry_run and seen["only_game"] == 28852
    assert isinstance(seen["store"], MemoryStateStore) and isinstance(seen["notifier"], LogNotifier)
    assert "https://www.ahahockey.com/game/28852: synced" in capsys.readouterr().out


def test_cli_credentials_from_env(monkeypatch):
    monkeypatch.setenv("BENCHAPP_EMAIL", "a@b.c")
    monkeypatch.setenv("BENCHAPP_PASSWORD", "pw")
    assert cli.local_credentials() == ("a@b.c", "pw")


def test_cli_credentials_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("BENCHAPP_EMAIL", raising=False)
    monkeypatch.delenv("BENCHAPP_PASSWORD", raising=False)
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"email": "x@y.z", "password": "secret"}))
    monkeypatch.setattr(cli, "CREDENTIALS_FILE", path)
    assert cli.local_credentials() == ("x@y.z", "secret")
