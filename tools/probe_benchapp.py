"""Read-only BenchApp API probe: works out how auth is sent and saves sample responses.

Usage: python -I tools/probe_benchapp.py EVENT_ID TEAM_ID OUT_DIR

Credentials come from BENCHAPP_EMAIL / BENCHAPP_PASSWORD, or else from
~/.config/aha-benchapp/credentials.json ({"email": ..., "password": ...}).
Only `login` and queries are sent; nothing is created or changed.
Responses are written to OUT_DIR as JSON (they contain personal data; keep
OUT_DIR out of git).
"""
import json
import os
import sys
from pathlib import Path

import requests

URL = "https://graphql.benchapp.com/graphql"
GRAPHQL_DIR = Path(__file__).resolve().parent.parent / "src" / "aha_benchapp" / "graphql"
BASE_HEADERS = {
    "content-type": "application/json",
    "x-graphql-client-name": "web",
    "x-graphql-client-version": "1.0.0",
    "x-graphql-client-route": "/schedule/[eventId]",
}


def credentials() -> tuple[str, str]:
    if os.environ.get("BENCHAPP_EMAIL") and os.environ.get("BENCHAPP_PASSWORD"):
        return os.environ["BENCHAPP_EMAIL"], os.environ["BENCHAPP_PASSWORD"]
    stored = json.loads((Path.home() / ".config" / "aha-benchapp" / "credentials.json").read_text())
    return stored["email"], stored["password"]


def call(operation: str, variables: dict, headers: dict) -> dict:
    body = {"operationName": operation, "query": (GRAPHQL_DIR / f"{operation}.graphql").read_text(), "variables": variables}
    response = requests.post(URL, json=body, headers={**BASE_HEADERS, **headers}, timeout=20)
    return {"status": response.status_code, "body": response.json() if response.content else None}


def main(event_id: int, team_id: int, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    email, password = credentials()
    login = call("login", {"input": {"email": email, "password": password}}, {})
    data = (login["body"] or {}).get("data") or {}
    if not data.get("login"):
        sys.exit(f"login failed: status {login['status']}, errors {(login['body'] or {}).get('errors')}")
    token, player_id = data["login"]["token"], data["login"]["player"]["id"]
    print(f"login ok; player.id={player_id}; token length {len(token)}")

    variants = {
        "bearer": {"authorization": f"Bearer {token}", "x-user-id": str(player_id)},
        "bare": {"authorization": token, "x-user-id": str(player_id)},
    }
    auth = None
    for name, headers in variants.items():
        result = call("eventDetailsScreenContent", {"id": event_id, "returnBothTeamsPlayers": False}, headers)
        ok = bool(((result["body"] or {}).get("data") or {}).get("event"))
        print(f"auth {name}: status {result['status']}, event returned: {ok}, errors: {(result['body'] or {}).get('errors')}")
        if ok and auth is None:
            auth = headers
            (out / "eventDetailsScreenContent.json").write_text(json.dumps(result["body"], indent=2))
    if auth is None:
        sys.exit("no auth variant worked")

    samples = {
        "eventListScreenContent": {"eventsInput": {"includePrevious": True, "leagueId": None, "order": "desc", "seasonId": None, "teamId": team_id}, "returnBothTeamsPlayers": False, "skip": 0, "take": 10},
        "scorekeeperEvent": {"eventId": event_id},
        "scorekeeperRostersPage": {"eventId": event_id},
        "scorekeeperPointsAndPenalties": {"eventId": event_id},
    }
    for operation, variables in samples.items():
        result = call(operation, variables, auth)
        (out / f"{operation}.json").write_text(json.dumps(result["body"], indent=2))
        print(f"{operation}: status {result['status']}, errors: {(result['body'] or {}).get('errors')}")

    shots = json.loads((out / "scorekeeperEvent.json").read_text())["data"]["event"]["shots"]
    counts: dict = {}
    for shot in shots:
        key = (shot["team"]["id"], shot["period"], "goal" if shot["goalId"] else "manual")
        counts[key] = counts.get(key, 0) + 1
    print(f"event {event_id} shots by (team, period, linked to goal): {dict(sorted(counts.items()))}")

    events = json.loads((out / "eventListScreenContent.json").read_text())["data"]["events"]
    for event in events:
        if event["id"] == event_id:
            print(f"event {event_id} stats: {event['stats']}, homeTeam {event['homeTeam']['name']}, dateAndTime {event['dateAndTime']}")


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
