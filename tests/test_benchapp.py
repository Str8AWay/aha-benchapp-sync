import json

import pytest
import requests
import responses

from aha_benchapp.benchapp import URL, BenchAppClient, BenchAppError
from tests import fixture

LOGIN = {"data": {"login": {"token": "tok123", "player": {"id": 900001}}}}


class Http:
    """Canned BenchApp responses, served in registration order."""

    def __init__(self, mock: responses.RequestsMock):
        self._mock = mock

    def reply(self, body, status=200):
        self._mock.add(responses.POST, URL, json=body, status=status)

    @property
    def calls(self):
        return self._mock.calls

    def sent(self, index=-1) -> dict:
        return json.loads(self.calls[index].request.body)


@pytest.fixture
def http():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield Http(mock)


def _logged_in(http: Http, dry_run: bool) -> BenchAppClient:
    http.reply(LOGIN)
    c = BenchAppClient(requests.Session(), dry_run=dry_run)
    c.login("me@example.com", "pw")
    return c


@pytest.fixture
def client(http):
    return _logged_in(http, dry_run=False)


@pytest.fixture
def dry_client(http):
    return _logged_in(http, dry_run=True)


def test_login_sends_credentials(http, client):
    sent = http.sent(0)
    assert sent["operationName"] == "login"
    assert sent["variables"] == {"input": {"email": "me@example.com", "password": "pw"}}


def test_create_point_sends_recorded_variables(http, client):
    http.reply({"data": {"createEventPoint": {"id": 870044}}})
    expected = json.loads(fixture("har_point_vars.json"))["input"]
    point_id = client.create_point(3766427, 531381, 1247980, "Home", "One", "11:20", 900016, (900004,))
    sent = http.sent()
    assert sent["operationName"] == "scorekeeperCreateEventPoint"
    assert sent["variables"]["input"] == expected
    assert point_id == 870044


def test_create_point_two_assists_and_type(http, client):
    http.reply({"data": {"createEventPoint": {"id": 1}}})
    client.create_point(1, 2, 3, "Away", "Two", "15:03", 10, (11, 12), point_type="PowerPlay")
    sent = http.sent()["variables"]["input"]
    assert (sent["assistOnePlayerId"], sent["assistTwoPlayerId"], sent["type"]) == (11, 12, "PowerPlay")


def test_create_penalty_sends_recorded_variables(http, client):
    http.reply({"data": {"createEventPenalty": {"id": 5}}})
    expected = json.loads(fixture("har_penalty_vars.json"))["input"]
    client.create_penalty(3766427, 531381, 1528241, "Away", "Two", "11:07", 900025,
                          {"kind": "Hooking", "duration": "Two", "type": "Minor"})
    assert http.sent()["variables"]["input"] == expected


def test_create_roster_player_returns_player_id(http, client):
    http.reply({"data": {"createRosterPlayer": {"id": 77, "player": {"id": 900021}}}})
    assert client.create_roster_player(1528241, 531381, "R. Monge", "80") == 900021
    assert http.sent()["variables"]["input"] == {
        "class": "Spare", "name": "R. Monge", "playerNumberDisplay": "80",
        "seasonId": 531381, "teamId": 1528241, "temp": True,
    }


def test_requests_carry_auth_and_client_headers(http, client):
    http.reply(json.loads(fixture("ba_scorekeeper_event.json")))
    client.event_stats(3766427)
    headers = http.calls[-1].request.headers
    assert headers["Authorization"] == "Bearer tok123"
    assert headers["x-user-id"] == "900001"
    assert headers["x-graphql-client-name"] == "web"
    assert headers["x-graphql-client-version"] == "1.0.0"
    assert headers["x-graphql-client-route"] == "/schedule/[eventId]"


def test_event_stats_counts_shots_per_team(http, client):
    http.reply(json.loads(fixture("ba_scorekeeper_event.json")))
    assert client.event_stats(3766427) == {
        "homeTeamScore": 3, "awayTeamScore": 4, "homeTeamShots": 39, "awayTeamShots": 55,
    }


EMPTY_EVENT = {"data": {"event": {"id": 3766428, "period": "One", "stats": {"homeTeamScore": 0, "awayTeamScore": 0},
                                 "homeTeam": {"id": 1}, "awayTeam": {"id": 2}, "shots": []}}}
NOTHING = {"data": {"eventPointsAndPenalties": {"points": [], "penalties": []}}}


def test_has_scorekeeping_with_points(http, client):
    http.reply(json.loads(fixture("ba_points_and_penalties.json")))
    assert client.has_scorekeeping(3766427) is True


def test_has_scorekeeping_with_only_penalties(http, client):
    http.reply({"data": {"eventPointsAndPenalties": {"points": [], "penalties": [{"id": 1}]}}})
    assert client.has_scorekeeping(1) is True


def test_has_scorekeeping_with_only_shots(http, client):
    http.reply(NOTHING)
    http.reply(json.loads(fixture("ba_scorekeeper_event.json")))
    assert client.has_scorekeeping(3766427) is True


def test_has_scorekeeping_empty_event(http, client):
    http.reply(NOTHING)
    http.reply(EMPTY_EVENT)
    assert client.has_scorekeeping(3766428) is False


def test_game_sheet_returns_raw_sheet(http, client):
    http.reply(json.loads(fixture("ba_game_sheet.json")))
    sheet = client.game_sheet(3766427)
    assert sheet["awayTeam"]["team"]["id"] == 1528241
    assert len(sheet["awayTeam"]["players"]) == 7


def test_events_pages_until_short_page(http, client):
    event = json.loads(fixture("ba_events.json"))["data"]["events"][0]
    http.reply({"data": {"events": [event] * 50}})
    http.reply({"data": {"events": [event] * 3}})
    assert len(client.events(1247980)) == 53
    first, second = http.sent(-2)["variables"], http.sent(-1)["variables"]
    assert first["eventsInput"]["teamId"] == 1247980 and first["eventsInput"]["includePrevious"] is True
    assert (first["take"], first["skip"], second["skip"]) == (50, 0, 50)


def test_graphql_errors_raise_with_details(http, client):
    http.reply({"errors": [{"message": "Not authorized", "extensions": {"code": "UNAUTHENTICATED"}}]})
    with pytest.raises(BenchAppError, match="scorekeeperPointsAndPenalties.*Not authorized"):
        client.has_scorekeeping(1)


def test_http_error_raises(http, client):
    http.reply({}, status=502)
    with pytest.raises(BenchAppError, match="502"):
        client.game_sheet(1)


def test_login_failure_raises(http):
    http.reply({"errors": [{"message": "Invalid email or password"}]})
    with pytest.raises(BenchAppError, match="Invalid email or password"):
        BenchAppClient(requests.Session()).login("me@example.com", "bad")


def test_dry_run_mutations_send_nothing(http, dry_client):
    before = len(http.calls)
    ids = [dry_client.create_shot(1, 2, "One") for _ in range(3)]
    dry_client.set_period(1, "Three")
    assert len(http.calls) == before
    assert ids == [-1, -2, -3]


def test_dry_run_still_reads(http, dry_client):
    http.reply(json.loads(fixture("ba_game_sheet.json")))
    assert dry_client.game_sheet(3766427)["homeTeam"]["team"]["id"] == 1247980
