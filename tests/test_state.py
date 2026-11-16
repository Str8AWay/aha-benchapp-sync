import boto3
from botocore.stub import ANY, Stubber

from aha_benchapp.models import GameResult, Status
from aha_benchapp.state import DynamoStateStore, MemoryStateStore

TABLE = "aha-benchapp-state"
RESULT = GameResult(28819, Status.NEEDS_CLEANUP, 3766427, ["score mismatch"], [11, 12])
ITEM = {
    "game_id": {"N": "28819"},
    "status": {"S": "needs-cleanup"},
    "event_id": {"N": "3766427"},
    "warnings": {"L": [{"S": "score mismatch"}]},
    "created_point_ids": {"L": [{"N": "11"}, {"N": "12"}]},
    "updated_at": {"S": "2026-11-16T12:00:00+00:00"},
}


def _stubbed():
    client = boto3.client("dynamodb", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    return client, Stubber(client)


def test_dynamo_round_trip():
    client, stub = _stubbed()
    stub.add_response("put_item", {}, {"TableName": TABLE, "Item": {**ITEM, "updated_at": {"S": ANY}}})
    stub.add_response("get_item", {"Item": ITEM}, {"TableName": TABLE, "Key": {"game_id": {"N": "28819"}}, "ConsistentRead": True})
    with stub:
        store = DynamoStateStore(TABLE, client)
        store.put(RESULT)
        assert store.get(28819) == RESULT
    stub.assert_no_pending_responses()


def test_dynamo_omits_missing_event_id():
    client, stub = _stubbed()
    item = {
        "game_id": {"N": "1"}, "status": {"S": "failed-parse-error"},
        "warnings": {"L": []}, "created_point_ids": {"L": []}, "updated_at": {"S": ANY},
    }
    stub.add_response("put_item", {}, {"TableName": TABLE, "Item": item})
    with stub:
        DynamoStateStore(TABLE, client).put(GameResult(1, Status.FAILED_PARSE_ERROR))


def test_dynamo_get_missing_returns_none():
    client, stub = _stubbed()
    stub.add_response("get_item", {}, {"TableName": TABLE, "Key": {"game_id": {"N": "5"}}, "ConsistentRead": True})
    with stub:
        assert DynamoStateStore(TABLE, client).get(5) is None


def test_memory_store_round_trip():
    store = MemoryStateStore()
    assert store.get(28819) is None
    store.put(RESULT)
    assert store.get(28819) == RESULT


def test_memory_store_keeps_a_snapshot():
    store = MemoryStateStore()
    result = GameResult(1, Status.IN_PROGRESS)
    store.put(result)
    result.created_point_ids.append(99)
    assert store.get(1).created_point_ids == []
