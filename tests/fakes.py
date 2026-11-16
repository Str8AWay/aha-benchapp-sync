"""In-memory stand-in for BenchAppClient: records calls, derives stats from what was written."""
import itertools

from aha_benchapp.benchapp import BenchAppError

LES_BA, MOOSE_BA = 1247980, 1528241
OUR_NUMBERS = ["5", "10", "12", "16", "20", "21", "24", "36", "37", "43", "55", "80", "89", "92", "94"]  # real roster minus inactive

EVENT = {
    "id": 3766427,
    "type": "Game",
    "homeOrAway": "Home",
    "homeTeam": {"id": LES_BA, "name": "C1 Les Etoiles du Nord"},
    "awayTeam": {"id": MOOSE_BA, "name": "Moose C1"},
    "season": {"id": 531381},
    "dateAndTime": {"date": "2026-11-08", "time": "16:20:00", "full": "2026-11-08 16:20:00", "status": "Past"},
}


SLED_EVENT = {
    **EVENT,
    "id": 3766428,
    "homeOrAway": "Away",
    "homeTeam": {"id": 1528300, "name": "Sled Dogs"},
    "awayTeam": {"id": LES_BA, "name": "C1 Les Etoiles du Nord"},
    "dateAndTime": {"date": "2026-11-13", "time": "20:00:00", "full": "2026-11-13 20:00:00", "status": "Past"},
}


def roster_player(number, *, is_goalie=False, status="In", temp=False):
    return {"playerId": 1000 + int(number), "playerNumberDisplay": number, "playerName": f"Player {number}",
            "isGoalie": is_goalie, "temp": temp, "status": status}


def our_roster():
    return [roster_player(n) for n in OUR_NUMBERS] + [roster_player("30", is_goalie=True)]


class FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.has_scorekeeping_value = False
        self.failure_lands = False  # the failing write was saved by BenchApp anyway (e.g. a timeout)
        self.home_players = our_roster()
        self.away_players: list[dict] = []
        self.stats_override: dict | None = None
        self.fail_on: tuple[str, int] | None = None
        self._ids = itertools.count(100)
        self._counts: dict[str, int] = {}

    def _record(self, method: str, *args):
        self._counts[method] = self._counts.get(method, 0) + 1
        if self.fail_on == (method, self._counts[method]):
            if self.failure_lands:
                self.calls.append((method, args))
            raise BenchAppError(f"{method}: simulated failure")
        self.calls.append((method, args))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def login(self, email, password):
        self._record("login", email)

    def events(self, team_id):
        self._record("events", team_id)
        return [SLED_EVENT, EVENT]

    def has_scorekeeping(self, event_id):
        self._record("has_scorekeeping", event_id)
        written = any(name.startswith(("create_point", "create_penalty", "create_shot")) and args[0] == event_id
                      for name, args in self.calls)
        return self.has_scorekeeping_value or written

    def game_sheet(self, event_id):
        self._record("game_sheet", event_id)
        event = SLED_EVENT if event_id == SLED_EVENT["id"] else EVENT
        ours, theirs = {"team": {"id": LES_BA}, "players": self.home_players}, {"players": self.away_players}
        if event["homeTeam"]["id"] == LES_BA:
            return {"homeTeam": ours, "awayTeam": {**theirs, "team": event["awayTeam"]}}
        return {"homeTeam": {**theirs, "team": event["homeTeam"]}, "awayTeam": ours}

    def event_stats(self, event_id):
        self._record("event_stats", event_id)
        if self.stats_override is not None:
            return self.stats_override

        event = SLED_EVENT if event_id == SLED_EVENT["id"] else EVENT
        home, away = event["homeTeam"]["id"], event["awayTeam"]["id"]

        def count(method, team_id):
            return sum(1 for name, args in self.calls
                       if name == method and args[0] == event_id and args[_TEAM_ARG[method]] == team_id)

        return {
            "homeTeamScore": count("create_point", home), "awayTeamScore": count("create_point", away),
            "homeTeamShots": count("create_shot", home), "awayTeamShots": count("create_shot", away),
        }

    def create_roster_player(self, team_id, season_id, name, number):
        self._record("create_roster_player", team_id, season_id, name, number)
        return next(self._ids)

    def set_attendance(self, event_id, season_id, team_id, player_id):
        self._record("set_attendance", event_id, season_id, team_id, player_id)

    def create_point(self, event_id, season_id, team_id, home_away, period, time, goal_player_id, assist_ids, *, point_type="EvenStrength"):
        self._record("create_point", event_id, season_id, team_id, home_away, period, time, goal_player_id, assist_ids, point_type)
        return next(self._ids)

    def create_penalty(self, event_id, season_id, team_id, home_away, period, time, player_id, fields):
        self._record("create_penalty", event_id, season_id, team_id, home_away, period, time, player_id, fields)
        return next(self._ids)

    def create_shot(self, event_id, team_id, period):
        self._record("create_shot", event_id, team_id, period)
        return next(self._ids)

    def create_goalie(self, event_id, team_id, player_id):
        self._record("create_goalie", event_id, team_id, player_id)
        return next(self._ids)

    def set_period(self, event_id, period):
        self._record("set_period", event_id, period)


_TEAM_ARG = {"create_point": 2, "create_shot": 1}
