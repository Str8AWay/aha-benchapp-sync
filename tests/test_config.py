import json

import boto3
import pytest
from botocore.stub import Stubber

from aha_benchapp.config import Config, load_credentials

URL = "https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308"


def test_config_from_env():
    cfg = Config.from_env({"AHA_TEAM_URL": URL, "BENCHAPP_TEAM_ID": "1247980", "DRY_RUN": "True",
                           "BENCHAPP_CREDENTIALS_PARAMETER": "/aha-benchapp/credentials", "STATE_TABLE": "t", "NOTIFY_TOPIC_ARN": "arn"})
    assert cfg == Config(URL, 308, 1247980, "/aha-benchapp/credentials", "t", "arn", True)


@pytest.mark.parametrize("value, expected", [("true", True), ("1", True), ("YES", True), ("false", False), ("", False)])
def test_config_dry_run_values(value, expected):
    assert Config.from_env({"AHA_TEAM_URL": URL, "BENCHAPP_TEAM_ID": "1", "DRY_RUN": value}).dry_run is expected


def test_config_optional_values_default_to_none():
    cfg = Config.from_env({"AHA_TEAM_URL": URL + "/", "BENCHAPP_TEAM_ID": "1"})
    assert (cfg.aha_team_id, cfg.credentials_parameter, cfg.state_table, cfg.notify_topic_arn, cfg.dry_run) == (308, None, None, None, False)


@pytest.mark.parametrize("missing", ["AHA_TEAM_URL", "BENCHAPP_TEAM_ID"])
def test_config_missing_var_named(missing):
    env = {"AHA_TEAM_URL": URL, "BENCHAPP_TEAM_ID": "1"}
    del env[missing]
    with pytest.raises(ValueError, match=missing):
        Config.from_env(env)


def test_config_rejects_url_without_team_id():
    with pytest.raises(ValueError, match="AHA_TEAM_URL"):
        Config.from_env({"AHA_TEAM_URL": "https://www.ahahockey.com/team/les-etoiles", "BENCHAPP_TEAM_ID": "1"})


def test_load_credentials():
    client = boto3.client("ssm", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(client)
    stub.add_response("get_parameter", {"Parameter": {"Value": json.dumps({"email": "a@b.c", "password": "pw"})}},
                      {"Name": "/aha-benchapp/credentials", "WithDecryption": True})
    with stub:
        assert load_credentials("/aha-benchapp/credentials", client) == ("a@b.c", "pw")
