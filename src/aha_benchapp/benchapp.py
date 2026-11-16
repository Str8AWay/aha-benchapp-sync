"""Thin client for BenchApp's private GraphQL API (see docs/benchapp-api-notes.md)."""
import itertools
import json
import logging
from pathlib import Path

import requests

URL = "https://graphql.benchapp.com/graphql"
_QUERIES = Path(__file__).parent / "graphql"
_CLIENT_HEADERS = {
    "x-graphql-client-name": "web",
    "x-graphql-client-version": "1.0.0",
    "x-graphql-client-route": "/schedule/[eventId]",
}
_PAGE_SIZE = 50

log = logging.getLogger(__name__)


class BenchAppError(Exception):
    """A BenchApp request failed; the message carries the operation and the server's errors."""


class BenchAppClient:
    def __init__(self, session: requests.Session, *, dry_run: bool = False):
        self._session = session
        self._dry_run = dry_run
        self._auth: dict[str, str] = {}
        self._fake_ids = itertools.count(-1, -1)

    def login(self, email: str, password: str) -> None:
        data = self._call("login", {"input": {"email": email, "password": password}})
        login = data["login"]
        self._auth = {"Authorization": f"Bearer {login['token']}", "x-user-id": str(login["player"]["id"])}

    # Reads

    def events(self, team_id: int) -> list[dict]:
        events: list[dict] = []
        for skip in itertools.count(0, _PAGE_SIZE):
            page = self._call("eventListScreenContent", {
                "eventsInput": {"includePrevious": True, "leagueId": None, "order": "desc", "seasonId": None, "teamId": team_id},
                "returnBothTeamsPlayers": False,
                "skip": skip,
                "take": _PAGE_SIZE,
            })["events"]
            events.extend(page)
            if len(page) < _PAGE_SIZE:
                return events
        raise AssertionError("unreachable")

    def game_sheet(self, event_id: int) -> dict:
        return self._call("scorekeeperRostersPage", {"eventId": event_id})["gameSheet"]

    def event_stats(self, event_id: int) -> dict:
        """Score from `stats`; shots counted from the shot list (stats' shot totals are always 0)."""
        event = self._call("scorekeeperEvent", {"eventId": event_id})["event"]
        shot_teams = [shot["team"]["id"] for shot in event["shots"]]
        return {
            "homeTeamScore": event["stats"]["homeTeamScore"],
            "awayTeamScore": event["stats"]["awayTeamScore"],
            "homeTeamShots": shot_teams.count(event["homeTeam"]["id"]),
            "awayTeamShots": shot_teams.count(event["awayTeam"]["id"]),
        }

    def has_points(self, event_id: int) -> bool:
        return bool(self._call("scorekeeperPointsAndPenalties", {"eventId": event_id})["eventPointsAndPenalties"]["points"])

    # Writes (logged, not sent, in dry run)

    def create_roster_player(self, team_id: int, season_id: int, name: str, number: str) -> int:
        data = self._mutate("scorekeeperCreateRosterPlayer", {"input": {
            "class": "Spare", "name": name, "playerNumberDisplay": number,
            "seasonId": season_id, "teamId": team_id, "temp": True,
        }})
        return data["createRosterPlayer"]["player"]["id"] if data else next(self._fake_ids)

    def set_attendance(self, event_id: int, season_id: int, team_id: int, player_id: int) -> None:
        self._mutate("scorekeeperSetAttendance", {"input": {
            "eventId": event_id, "playerId": player_id, "seasonId": season_id, "status": "In", "teamId": team_id,
        }})

    def create_point(self, event_id: int, season_id: int, team_id: int, home_away: str, period: str, time: str,
                     goal_player_id: int, assist_ids: tuple[int, ...], *, point_type: str = "EvenStrength") -> int:
        assists = list(assist_ids) + [None, None]
        return self._created("scorekeeperCreateEventPoint", "createEventPoint", {"input": {
            "assistOnePlayerId": assists[0], "assistTwoPlayerId": assists[1], "eventId": event_id,
            "goalPlayerId": goal_player_id, "homeAway": home_away, "period": period,
            "seasonId": season_id, "teamId": team_id, "time": time, "type": point_type,
        }})

    def create_penalty(self, event_id: int, season_id: int, team_id: int, home_away: str, period: str, time: str,
                       player_id: int, fields: dict[str, str]) -> int:
        return self._created("scorekeeperCreateEventPenalty", "createEventPenalty", {"input": {
            "duration": fields["duration"], "eventId": event_id, "homeAway": home_away, "kind": fields["kind"],
            "period": period, "playerId": player_id, "seasonId": season_id, "teamId": team_id,
            "time": time, "type": fields["type"],
        }})

    def create_shot(self, event_id: int, team_id: int, period: str) -> int:
        return self._created("scorekeeperCreateEventShot", "createEventShot",
                             {"input": {"eventId": event_id, "period": period, "teamId": team_id}})

    def create_goalie(self, event_id: int, team_id: int, player_id: int) -> int:
        return self._created("manageGoaliesCreateEventGoalie", "createEventGoalie", {"input": {
            "eventId": event_id, "period": "One", "playerId": player_id, "teamId": team_id, "time": "00:00",
        }})

    def set_period(self, event_id: int, period: str) -> None:
        self._mutate("scorekeeperSetPeriod", {"id": event_id, "period": period})

    # Transport

    def _created(self, operation: str, field: str, variables: dict) -> int:
        data = self._mutate(operation, variables)
        return data[field]["id"] if data else next(self._fake_ids)

    def _mutate(self, operation: str, variables: dict) -> dict | None:
        if self._dry_run:
            log.info("DRY RUN %s %s", operation, json.dumps(variables, sort_keys=True))
            return None
        return self._call(operation, variables)

    def _call(self, operation: str, variables: dict) -> dict:
        body = {"operationName": operation, "query": (_QUERIES / f"{operation}.graphql").read_text(), "variables": variables}
        try:
            response = self._session.post(URL, json=body, headers={**_CLIENT_HEADERS, **self._auth}, timeout=20)
        except requests.RequestException as error:
            raise BenchAppError(f"{operation}: {error}") from error
        if not response.ok:
            raise BenchAppError(f"{operation}: HTTP {response.status_code}")
        payload = response.json()
        if payload.get("errors") or not payload.get("data"):
            raise BenchAppError(f"{operation}: {json.dumps(payload.get('errors'))}")
        return payload["data"]
