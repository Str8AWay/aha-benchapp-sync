import pytest

from aha_benchapp.enums import PENALTY_KINDS, PERIOD, penalty_fields, point_type
from aha_benchapp.models import Period


def test_penalty_fields_hooking_minor():
    assert penalty_fields("Hooking", 2) == {"kind": "Hooking", "duration": "Two", "type": "Minor"}


def test_penalty_fields_matches_ignoring_case_and_spaces():
    assert penalty_fields("high sticking", 2)["kind"] == PENALTY_KINDS["highsticking"] == "High-Sticking"


@pytest.mark.parametrize(
    "aha_kind, benchapp_kind",
    [
        ("Body Check", "Body-Checking"),
        ("Unsportmanlike Conduct", "Unsportsmanlike Conduct"),
        ("Face Off Interference", "Faceoff Violation"),
        ("Cross Checking", "Cross-Checking"),
    ],
)
def test_penalty_fields_aliases(aha_kind, benchapp_kind):
    assert penalty_fields(aha_kind, 2)["kind"] == benchapp_kind


def test_penalty_fields_game_ejection_is_game_misconduct():
    assert penalty_fields("Game Ejection", 10) == {"kind": "Game Misconduct", "duration": "Ten", "type": "GameMisconduct"}


def test_penalty_fields_major_and_double_minor():
    assert penalty_fields("Fighting", 5)["type"] == "Major"
    assert penalty_fields("High Sticking", 4) == {"kind": "High-Sticking", "duration": "Four", "type": "DoubleMinor"}


def test_penalty_fields_unmappable():
    assert penalty_fields("Being Too Handsome", 2) is None
    assert penalty_fields("10 Minute Misconduct", 10) is None
    assert penalty_fields("Penalty Shot", 0) is None


def test_periods_cover_overtime():
    assert set(PERIOD) == set(Period)
    assert PERIOD[Period.OT] == "OT"


@pytest.mark.parametrize(
    "strength, expected",
    [("", "EvenStrength"), ("PP", "PowerPlay"), ("SH", "ShortHanded"), ("EN", "EmptyNet"), ("PP EN", "PowerPlay"), ("??", "EvenStrength")],
)
def test_point_type(strength, expected):
    assert point_type(strength) == expected
