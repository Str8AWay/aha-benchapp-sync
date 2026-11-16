"""Per-game sync state, keyed by AHA game ID."""
import copy
import time
from datetime import datetime, timezone
from typing import Protocol

import boto3

from aha_benchapp.models import GameResult, Status


class StateStore(Protocol):
    def get(self, game_id: int) -> GameResult | None: ...

    def put(self, result: GameResult) -> None: ...

    def acquire_lock(self, owner: str, seconds: int) -> bool:
        """Take the run lock unless another run holds an unexpired one."""
        ...

    def release_lock(self, owner: str) -> None: ...


_LOCK_KEY = {"game_id": {"N": "0"}}  # AHA game IDs are positive, so 0 is free for the run lock


class MemoryStateStore:
    def __init__(self):
        self._results: dict[int, GameResult] = {}
        self._lock: tuple[str, float] | None = None

    def get(self, game_id: int) -> GameResult | None:
        return self._results.get(game_id)

    def put(self, result: GameResult) -> None:
        self._results[result.game_id] = copy.deepcopy(result)

    def acquire_lock(self, owner: str, seconds: int) -> bool:
        if self._lock and self._lock[1] > time.time():
            return False
        self._lock = (owner, time.time() + seconds)
        return True

    def release_lock(self, owner: str) -> None:
        if self._lock and self._lock[0] == owner:
            self._lock = None


class DynamoStateStore:
    def __init__(self, table_name: str, client=None):
        self._table = table_name
        self._client = client or boto3.client("dynamodb")

    def get(self, game_id: int) -> GameResult | None:
        item = self._client.get_item(
            TableName=self._table, Key={"game_id": {"N": str(game_id)}}, ConsistentRead=True
        ).get("Item")
        if item is None:
            return None
        return GameResult(
            game_id=int(item["game_id"]["N"]),
            status=Status(item["status"]["S"]),
            event_id=int(item["event_id"]["N"]) if "event_id" in item else None,
            warnings=[w["S"] for w in item["warnings"]["L"]],
            created_point_ids=[int(p["N"]) for p in item["created_point_ids"]["L"]],
        )

    def put(self, result: GameResult) -> None:
        item = {
            "game_id": {"N": str(result.game_id)},
            "status": {"S": result.status.value},
            "warnings": {"L": [{"S": w} for w in result.warnings]},
            "created_point_ids": {"L": [{"N": str(p)} for p in result.created_point_ids]},
            "updated_at": {"S": datetime.now(timezone.utc).isoformat()},
        }
        if result.event_id is not None:
            item["event_id"] = {"N": str(result.event_id)}
        self._client.put_item(TableName=self._table, Item=item)

    def acquire_lock(self, owner: str, seconds: int) -> bool:
        try:
            self._client.put_item(
                TableName=self._table,
                Item={**_LOCK_KEY, "lock_owner": {"S": owner}, "lock_expires": {"N": str(int(time.time()) + seconds)}},
                ConditionExpression="attribute_not_exists(game_id) OR lock_expires < :now",
                ExpressionAttributeValues={":now": {"N": str(int(time.time()))}},
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def release_lock(self, owner: str) -> None:
        try:
            self._client.delete_item(
                TableName=self._table,
                Key=_LOCK_KEY,
                ConditionExpression="lock_owner = :owner",
                ExpressionAttributeValues={":owner": {"S": owner}},
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            pass  # expired and taken over by another run
