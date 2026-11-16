"""Tell the manager about games that need a look."""
import logging

import boto3

from aha_benchapp.models import GameResult, Status

log = logging.getLogger(__name__)

_QUIET = {Status.SYNCED, Status.SKIPPED_EXISTING}


def should_notify(result: GameResult, prior: Status | None) -> bool:
    return result.status not in _QUIET and result.status != prior


def format_summary(results: list[GameResult]) -> str:
    blocks = []
    for r in results:
        lines = [f"https://www.ahahockey.com/game/{r.game_id}: {r.status.value}"]
        if r.event_id is not None:
            lines.append(f"  BenchApp event {r.event_id}")
        lines += [f"  - {warning}" for warning in r.warnings]
        if r.status is Status.NEEDS_CLEANUP and r.created_point_ids:
            lines.append(f"  Points this sync created: {', '.join(map(str, r.created_point_ids))}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _notable(results: list[GameResult], priors: dict[int, Status | None]) -> list[GameResult]:
    return [r for r in results if should_notify(r, priors.get(r.game_id))]


class SnsNotifier:
    def __init__(self, topic_arn: str, client=None):
        self._topic_arn = topic_arn
        self._client = client or boto3.client("sns")

    def notify(self, results: list[GameResult], priors: dict[int, Status | None]) -> None:
        notable = _notable(results, priors)
        if notable:
            self.publish(f"AHA → BenchApp: {len(notable)} game(s) need a look", format_summary(notable))

    def publish(self, subject: str, message: str) -> None:
        self._client.publish(TopicArn=self._topic_arn, Subject=subject, Message=message)


class LogNotifier:
    def notify(self, results: list[GameResult], priors: dict[int, Status | None]) -> None:
        notable = _notable(results, priors)
        if notable:
            log.info("would notify about %d game(s):\n%s", len(notable), format_summary(notable))
