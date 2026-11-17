"""Settings from the environment, and the BenchApp login from SSM Parameter Store."""
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
    credentials_parameter: str | None
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
            credentials_parameter=env.get("BENCHAPP_CREDENTIALS_PARAMETER") or None,
            state_table=env.get("STATE_TABLE") or None,
            notify_topic_arn=env.get("NOTIFY_TOPIC_ARN") or None,
            dry_run=env.get("DRY_RUN", "").strip().lower() in ("true", "1", "yes"),
        )


def load_credentials(parameter: str, client=None) -> tuple[str, str]:
    client = client or boto3.client("ssm")
    stored = json.loads(client.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"])
    return stored["email"], stored["password"]


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value
