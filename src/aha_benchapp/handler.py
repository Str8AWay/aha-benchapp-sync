"""AWS Lambda entry point, run daily by EventBridge Scheduler."""
import logging

import requests

from aha_benchapp.benchapp import BenchAppClient
from aha_benchapp.config import Config, load_credentials
from aha_benchapp.notify import LogNotifier, SnsNotifier
from aha_benchapp.state import DynamoStateStore
from aha_benchapp.sync import run

logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, context) -> dict:
    cfg = Config.from_env()
    sns = SnsNotifier(cfg.notify_topic_arn) if cfg.notify_topic_arn else None
    session = requests.Session()
    try:
        results = run(
            cfg,
            session=session,
            client=BenchAppClient(session, dry_run=cfg.dry_run),
            store=DynamoStateStore(cfg.state_table),
            notifier=LogNotifier() if cfg.dry_run or sns is None else sns,
            credentials=lambda: load_credentials(cfg.secret_id),
        )
    except Exception as error:
        if sns and not cfg.dry_run:
            sns.publish("AHA → BenchApp sync failed", str(error))
        raise
    return {"results": [{"game_id": r.game_id, "status": r.status.value, "warnings": r.warnings} for r in results]}
