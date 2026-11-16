import json
from datetime import date

from aha_benchapp.matching import event_candidates, normalize_team, our_side, resolve_opponent, resolve_ours
from aha_benchapp.models import AhaPlayer
from tests import fixture

LES = 1247980


def make_event(event_id, day, home, away, time="16:20:00"):
    return {
        "id": event_id,
        "homeTeam": {"id": home[0], "name": home[1]},
        "awayTeam": {"id": away[0], "name": away[1]},
        "dateAndTime": {"date": day, "time": time, "full": f"{day} {time}", "status": "Past"},
    }


def sheet_player(player_id, number, name, *, is_goalie=False, temp=False):
    return {"playerId": player_id, "playerNumberDisplay": number, "playerName": name, "isGoalie": is_goalie, "temp": temp}


def test_team_names_match_variants():  # Review Focus 2
    assert normalize_team("Spartans C1 C1") == normalize_team("Spartans C1") == normalize_team("Spartans")
    assert normalize_team("Spartans B") != normalize_team("Spartans")
    assert normalize_team("C1 Les Étoiles du Nord") == "les etoiles du nord"
    assert normalize_team("Maroons Black C1") == "maroons black"


def test_find_event_late_game_uses_team_timezone():  # Review Focus 1: BenchApp's date is already local
    ev = make_event(1, "2026-11-08", (LES, "C1 Les Etoiles du Nord"), (1528241, "Moose C1"), time="23:30:00")
    assert event_candidates([ev], date(2026, 11, 8), "Moose C1", LES) == [ev]


def test_event_candidates_real_events():
    events = json.loads(fixture("ba_events.json"))["data"]["events"]
    assert [e["id"] for e in event_candidates(events, date(2026, 11, 8), "Moose C1", LES)] == [3766427]
    assert [e["id"] for e in event_candidates(events, date(2026, 11, 13), "Sled Dogs C1", LES)] == [3766428]


def test_event_candidates_wrong_opponent_or_date():
    ev = make_event(1, "2026-11-08", (LES, "Les Etoiles du Nord"), (1, "Sled Dogs"))
    assert event_candidates([ev], date(2026, 11, 8), "Moose C1", LES) == []
    assert event_candidates([ev], date(2026, 11, 9), "Sled Dogs C1", LES) == []


def test_event_candidates_returns_every_match():
    a = make_event(1, "2026-11-08", (LES, "Les Etoiles du Nord"), (2, "Moose"))
    b = make_event(2, "2026-11-08", (3, "Moose C1"), (LES, "Les Etoiles du Nord"))
    assert event_candidates([a, b], date(2026, 11, 8), "Moose C1", LES) == [a, b]


def test_our_side():
    ev = make_event(1, "2026-11-08", (LES, "Les"), (2, "Moose"))
    assert our_side(ev, LES) == "Home"
    assert our_side(ev, 2) == "Away"


def test_resolve_ours_by_number_ignores_name():
    roster = [sheet_player(900016, "5", "Bobby S"), sheet_player(900004, "24", "Kev")]
    assert resolve_ours(roster, AhaPlayer("5", "R. Steckman")) == 900016
    assert resolve_ours(roster, AhaPlayer("24", "K. Sherlund")) == 900004
    assert resolve_ours(roster, AhaPlayer("99", "X. Nobody")) is None


def test_resolve_ours_numberless_player_is_unmatched():
    assert resolve_ours([sheet_player(1, "", "A")], AhaPlayer("", "A")) is None


def test_resolve_ours_duplicate_number_is_unmatched():
    roster = [sheet_player(1, "5", "A"), sheet_player(2, "5", "B")]
    assert resolve_ours(roster, AhaPlayer("5", "A")) is None


def test_resolve_ours_zero_vs_double_zero():
    assert resolve_ours([sheet_player(1, "00", "A")], AhaPlayer("0", "A")) is None


def test_resolve_goalie_requires_is_goalie():
    roster = [sheet_player(7, "30", "Skater"), sheet_player(8, "31", "Goalie", is_goalie=True)]
    assert resolve_ours(roster, AhaPlayer("30", "T. Agerter"), goalie=True) is None
    assert resolve_ours(roster, AhaPlayer("31", "G. Oalie"), goalie=True) == 8


def test_opponent_player_reused_by_number():  # Review Focus 5
    roster = [sheet_player(900021, "80", "R. Monge", temp=True)]
    assert resolve_opponent(roster, AhaPlayer("80", "R. Monge")) == 900021


def test_opponent_player_falls_back_to_name():
    roster = [sheet_player(5, "", "J. Bader")]
    assert resolve_opponent(roster, AhaPlayer("37", "j. bader")) == 5
    assert resolve_opponent(roster, AhaPlayer("", "J. Bader")) == 5


def test_opponent_player_unknown():
    assert resolve_opponent([sheet_player(5, "8", "J. Fleischhacker")], AhaPlayer("80", "R. Monge")) is None


def test_event_candidates_skips_events_without_two_teams():
    practice = {"id": 9, "type": "Practice", "homeTeam": {"id": LES, "name": "Les"}, "awayTeam": None,
                "dateAndTime": {"date": "2026-11-08", "time": "10:00:00", "full": "2026-11-08 10:00:00", "status": "Past"}}
    game = make_event(1, "2026-11-08", (LES, "C1 Les Etoiles du Nord"), (1528241, "Moose C1"))
    assert event_candidates([practice, game], date(2026, 11, 8), "Moose C1", LES) == [game]
