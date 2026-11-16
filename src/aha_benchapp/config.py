"""Settings from the environment, and the BenchApp login from Secrets Manager."""
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

import boto3


@dataclass(frozen=True)
class Config:
    aha_team_url: str
    aha_team_id: int
    benchapp_team_id: int
    secret_id: str | None
    state_table: str | None
    notify_topic_arn: str | None
    dry_run: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        url = _required(env, "AHA_TEAM_URL")
        team_id = url.rstrip("/").rsplit("/", 1)[-1]
        if not team_id.isdigit():
            raise ValueError(f"AHA_TEAM_URL should end with the AHA team ID: {url}")
        return cls(
            aha_team_url=url,
            aha_team_id=int(team_id),
            benchapp_team_id=int(_required(env, "BENCHAPP_TEAM_ID")),
            secret_id=env.get("BENCHAPP_SECRET_ID") or None,
            state_table=env.get("STATE_TABLE") or None,
            notify_topic_arn=env.get("NOTIFY_TOPIC_ARN") or None,
            dry_run=env.get("DRY_RUN", "").strip().lower() in ("true", "1", "yes"),
        )


def load_credentials(secret_id: str, client=None) -> tuple[str, str]:
    client = client or boto3.client("secretsmanager")
    secret = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])
    return secret["email"], secret["password"]


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value
