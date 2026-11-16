"""Per-game sync state, keyed by AHA game ID."""
import copy
from datetime import datetime, timezone
from typing import Protocol

import boto3

from aha_benchapp.models import GameResult, Status


class StateStore(Protocol):
    def get(self, game_id: int) -> GameResult | None: ...

    def put(self, result: GameResult) -> None: ...


class MemoryStateStore:
    def __init__(self):
        self._results: dict[int, GameResult] = {}

    def get(self, game_id: int) -> GameResult | None:
        return self._results.get(game_id)

    def put(self, result: GameResult) -> None:
        self._results[result.game_id] = copy.deepcopy(result)


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
