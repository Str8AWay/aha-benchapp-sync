"""Data shared by the AHA parser, the BenchApp client, and the sync."""
import datetime
from dataclasses import dataclass, field
from enum import Enum


class Period(Enum):
    ONE = 1
    TWO = 2
    THREE = 3
    OT = 4


@dataclass(frozen=True)
class AhaPlayer:
    number: str  # kept as text so "0" and "00" stay distinct
    name: str


@dataclass(frozen=True)
class Goal:
    team: str
    period: Period
    time: str
    scorer: AhaPlayer
    assists: tuple[AhaPlayer, ...]
    strength: str = ""  # AHA's tag: "", "PP", "SH", "EN", "PP EN"


@dataclass(frozen=True)
class Penalty:
    team: str
    player: AhaPlayer | None  # None for bench penalties
    kind: str
    period: Period
    minutes: int
    time: str


@dataclass(frozen=True)
class BoxScore:
    game_id: int
    date: datetime.date
    teams: tuple[str, str]  # AHA's listed order
    team_ids: tuple[int, int]
    final: dict[str, int]
    goals: tuple[Goal, ...]
    penalties: tuple[Penalty, ...]
    shots: dict[str, dict[Period, int]]  # {} when AHA hasn't posted shots
    goalies: dict[str, AhaPlayer]
    parse_warnings: tuple[str, ...] = ()  # stats sections that couldn't be read


class Status(str, Enum):
    IN_PROGRESS = "in-progress"
    SYNCED = "synced"
    SYNCED_WITH_WARNINGS = "synced-with-warnings"
    SKIPPED_EXISTING = "skipped-existing"
    NEEDS_CLEANUP = "needs-cleanup"
    FAILED_MANUAL_ENTRY = "failed-manual-entry"
    FAILED_NO_EVENT = "failed-no-event"
    FAILED_AMBIGUOUS_EVENT = "failed-ambiguous-event"
    FAILED_PARSE_ERROR = "failed-parse-error"
    FAILED_API_ERROR = "failed-api-error"

    @property
    def retryable(self) -> bool:
        return self in _RETRYABLE


_RETRYABLE = {
    Status.FAILED_NO_EVENT,
    Status.FAILED_AMBIGUOUS_EVENT,
    Status.FAILED_PARSE_ERROR,
    Status.FAILED_API_ERROR,
}


@dataclass
class GameResult:
    game_id: int
    status: Status
    event_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    created_point_ids: list[int] = field(default_factory=list)


class ParseError(Exception):
    """The AHA page didn't look like a box score we understand."""


class ManualEntryRequired(Exception):
    """The game can't be entered automatically; the message says why."""
