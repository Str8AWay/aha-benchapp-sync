"""Match AHA games and players to BenchApp events and roster players."""
import re
import unicodedata
from datetime import date

from aha_benchapp.models import AhaPlayer

_DIVISION = re.compile(r"^[a-z]\d+$")  # "c1", "b3"


def normalize_team(name: str) -> str:
    """Lowercase, accent- and punctuation-free, with division labels ("C1") removed."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9 ]", " ", ascii_name.lower()).split()
    return " ".join(word for word in words if not _DIVISION.match(word))


def event_candidates(events: list[dict], game_date: date, opponent: str, team_id: int) -> list[dict]:
    """Events on `game_date` (BenchApp's local date) against `opponent`."""
    wanted = normalize_team(opponent)
    return [
        event
        for event in events
        if event.get("homeTeam") and event.get("awayTeam")  # practices have no opponent
        and event["dateAndTime"]["date"] == game_date.isoformat()
        and normalize_team(_other_team(event, team_id)["name"]) == wanted
    ]


def our_side(event: dict, team_id: int) -> str:
    return "Home" if event["homeTeam"]["id"] == team_id else "Away"


def resolve_ours(players: list[dict], p: AhaPlayer, *, goalie: bool = False) -> int | None:
    """Our roster player with p's jersey number (names are nicknames in BenchApp, so they're ignored)."""
    if not p.number:
        return None
    matches = [player for player in players if player["playerNumberDisplay"].strip() == p.number]
    if len(matches) != 1 or (goalie and not matches[0]["isGoalie"]):
        return None
    return matches[0]["playerId"]


def resolve_opponent(players: list[dict], p: AhaPlayer) -> int | None:
    """Opponent roster player by unique number, else by unique name."""
    if p.number:
        by_number = [player for player in players if player["playerNumberDisplay"].strip() == p.number]
        if len(by_number) == 1:
            return by_number[0]["playerId"]
    by_name = [player for player in players if player["playerName"].strip().lower() == p.name.lower()]
    return by_name[0]["playerId"] if len(by_name) == 1 else None


def _other_team(event: dict, team_id: int) -> dict:
    return event["awayTeam"] if event["homeTeam"]["id"] == team_id else event["homeTeam"]
