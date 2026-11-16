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


def test_memory_lock_excludes_a_second_run():  # review I-5
    store = MemoryStateStore()
    assert store.acquire_lock("run-a", 1000)
    assert not store.acquire_lock("run-b", 1000)
    store.release_lock("run-a")
    assert store.acquire_lock("run-b", 1000)


def test_dynamo_acquire_lock_is_conditional():
    client, stub = _stubbed()
    stub.add_response("put_item", {}, {
        "TableName": TABLE,
        "Item": {"game_id": {"N": "0"}, "lock_owner": {"S": "run-a"}, "lock_expires": {"N": ANY}},
        "ConditionExpression": "attribute_not_exists(game_id) OR lock_expires < :now",
        "ExpressionAttributeValues": {":now": {"N": ANY}},
    })
    stub.add_client_error("put_item", "ConditionalCheckFailedException")
    with stub:
        store = DynamoStateStore(TABLE, client)
        assert store.acquire_lock("run-a", 1000) is True
        assert store.acquire_lock("run-b", 1000) is False


def test_dynamo_release_lock_only_releases_own_lock():
    client, stub = _stubbed()
    stub.add_response("delete_item", {}, {
        "TableName": TABLE, "Key": {"game_id": {"N": "0"}},
        "ConditionExpression": "lock_owner = :owner",
        "ExpressionAttributeValues": {":owner": {"S": "run-a"}},
    })
    stub.add_client_error("delete_item", "ConditionalCheckFailedException")
    with stub:
        store = DynamoStateStore(TABLE, client)
        store.release_lock("run-a")
        store.release_lock("run-a")  # already gone or taken over: not an error
