import boto3
import pytest
from botocore.stub import ANY, Stubber

from aha_benchapp.models import GameResult, Status
from aha_benchapp.notify import SnsNotifier, format_summary, should_notify


@pytest.mark.parametrize("status, prior, expected", [
    (Status.SYNCED, None, False),
    (Status.SKIPPED_EXISTING, None, False),
    (Status.SYNCED_WITH_WARNINGS, None, True),
    (Status.FAILED_NO_EVENT, None, True),
    (Status.FAILED_NO_EVENT, Status.FAILED_NO_EVENT, False),
    (Status.NEEDS_CLEANUP, Status.IN_PROGRESS, True),
])
def test_should_notify(status, prior, expected):
    assert should_notify(GameResult(1, status), prior) is expected


def test_format_summary_lists_warnings_and_cleanup_ids():
    text = format_summary([GameResult(28819, Status.NEEDS_CLEANUP, 3766427, ["score mismatch: BenchApp 3–3, AHA 3–4"], [11, 12])])
    assert "https://www.ahahockey.com/game/28819" in text
    assert "needs-cleanup" in text and "3766427" in text
    assert "  - score mismatch" in text
    assert "11, 12" in text


def _sns():
    client = boto3.client("sns", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    return client, Stubber(client)


def test_sns_notifier_publishes_only_notable_results():
    client, stub = _sns()
    stub.add_response("publish", {"MessageId": "1"},
                      {"TopicArn": "arn:topic", "Subject": "AHA → BenchApp: 1 game(s) need a look", "Message": ANY})
    with stub:
        SnsNotifier("arn:topic", client).notify(
            [GameResult(1, Status.SYNCED), GameResult(2, Status.FAILED_NO_EVENT, warnings=["no BenchApp game"])],
            {1: None, 2: None},
        )
    stub.assert_no_pending_responses()


def test_sns_notifier_silent_when_nothing_notable():
    client, stub = _sns()
    with stub:  # any publish call would fail: no responses are queued
        SnsNotifier("arn:topic", client).notify([GameResult(1, Status.SYNCED)], {1: None})
