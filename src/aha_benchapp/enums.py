"""AHA → BenchApp value maps.

Values come from the BenchApp website bundle (introspection is disabled);
see docs/benchapp-api-notes.md.
"""
import re

from aha_benchapp.models import Period

PERIOD: dict[Period, str] = {
    Period.ONE: "One",
    Period.TWO: "Two",
    Period.THREE: "Three",
    Period.OT: "OT",
}

# Event 3766427's 7 goals created no goal-linked shots; see the API notes.
GOAL_COUNTS_AS_SHOT: bool = False

# BenchApp's ice hockey penalty catalog; `kind` is sent as the display name.
_CATALOG = [
    "Abuse of Officials", "Aggressor", "Boarding", "Boarding (Major)", "Body-Checking",
    "Broken Stick", "Butt-Ending", "Charging", "Checking from Behind", "Checking to the Head",
    "Clipping", "Closing Hand on Puck", "Cross-Checking", "Delay of Game", "Diving/Embellishment",
    "Elbowing", "Faceoff Violation", "Fighting", "Game Misconduct", "Goalie Interference",
    "High-Sticking", "Holding", "Holding the Stick", "Hooking", "Illegal Equipment",
    "Instigator", "Interference", "Kicking", "Kneeing", "Leaving the Bench", "Match Penalty",
    "Misconduct", "Puck Over Glass", "Roughing", "Slashing", "Slew Footing", "Spearing",
    "Throwing Stick", "Too Many Men", "Tripping", "Unsportsmanlike Conduct",
]

# AHA names that don't normalize to a catalog name.
_ALIASES = {
    "Body Check": "Body-Checking",
    "Unsportmanlike Conduct": "Unsportsmanlike Conduct",
    "Game Ejection": "Game Misconduct",
    "Too Many Players": "Too Many Men",
    "Face Off Interference": "Faceoff Violation",
    "10 Minute Misconduct": "Misconduct",
}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


PENALTY_KINDS: dict[str, str] = {_normalize(name): name for name in _CATALOG} | {
    _normalize(aha): benchapp for aha, benchapp in _ALIASES.items()
}

_DURATIONS = {2: "Two", 4: "Four", 5: "Five", 10: "Ten"}
_TYPES_BY_MINUTES = {2: "Minor", 4: "DoubleMinor", 5: "Major"}

_POINT_TYPES = {"PP": "PowerPlay", "SH": "ShortHanded", "EN": "EmptyNet"}


def penalty_fields(kind: str, minutes: int) -> dict[str, str] | None:
    """BenchApp's kind/duration/type for an AHA penalty, or None if it has no equivalent."""
    benchapp_kind = PENALTY_KINDS.get(_normalize(kind))
    duration = _DURATIONS.get(minutes)
    if benchapp_kind == "Game Misconduct":
        penalty_type = "GameMisconduct"
    else:
        penalty_type = _TYPES_BY_MINUTES.get(minutes)
    if benchapp_kind is None or duration is None or penalty_type is None:
        return None
    return {"kind": benchapp_kind, "duration": duration, "type": penalty_type}


def point_type(strength: str) -> str:
    """BenchApp point type for AHA's goal tag ("PP", "SH", "EN"; first tag wins)."""
    tags = strength.split()
    return _POINT_TYPES.get(tags[0], "EvenStrength") if tags else "EvenStrength"
