"""Run the sync locally: PYTHONPATH=src python -m aha_benchapp [--game ID] [--dry-run]

Reads the same environment variables as the Lambda. State isn't kept between
runs and notifications are logged, never sent; BenchApp's own "already has
points" check keeps a live local run from double-entering a game.
"""
import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

import requests

from aha_benchapp.benchapp import BenchAppClient
from aha_benchapp.config import Config
from aha_benchapp.notify import LogNotifier, format_summary
from aha_benchapp.state import MemoryStateStore
from aha_benchapp.sync import run

CREDENTIALS_FILE = Path.home() / ".config" / "aha-benchapp" / "credentials.json"


def local_credentials() -> tuple[str, str]:
    """BENCHAPP_EMAIL/BENCHAPP_PASSWORD, else the credentials file."""
    if os.environ.get("BENCHAPP_EMAIL") and os.environ.get("BENCHAPP_PASSWORD"):
        return os.environ["BENCHAPP_EMAIL"], os.environ["BENCHAPP_PASSWORD"]
    stored = json.loads(CREDENTIALS_FILE.read_text())
    return stored["email"], stored["password"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aha_benchapp", description="Copy AHA box scores into BenchApp.")
    parser.add_argument("--game", type=int, help="sync only this AHA game ID")
    parser.add_argument("--dry-run", action="store_true", help="log BenchApp writes instead of sending them")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = Config.from_env()
    if args.dry_run:
        cfg = replace(cfg, dry_run=True)
    session = requests.Session()
    results = run(
        cfg,
        session=session,
        client=BenchAppClient(session, dry_run=cfg.dry_run),
        store=MemoryStateStore(),
        notifier=LogNotifier(),
        credentials=local_credentials,
        only_game=args.game,
    )
    print(format_summary(results) if results else "nothing to sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
