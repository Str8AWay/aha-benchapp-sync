"""Fetch and parse AHA Hockey team and game pages."""
import datetime
import re

import requests
from bs4 import BeautifulSoup

from aha_benchapp.models import AhaPlayer, BoxScore, Goal, ManualEntryRequired, ParseError, Penalty, Period

_GAME_HREF = re.compile(r"^/game/(\d+)$")


def fetch(url: str, session: requests.Session) -> str:
    response = session.get(url, headers={"User-Agent": "aha-benchapp-sync"}, timeout=20)
    response.raise_for_status()
    return response.text


def completed_game_ids(team_html: str) -> list[int]:
    """IDs of games whose team-page link reads "Box score", in page order."""
    soup = BeautifulSoup(team_html, "html.parser")
    ids: list[int] = []
    for link in soup.select('a[href^="/game/"]'):
        match = _GAME_HREF.match(link["href"])
        if match and link.get_text(strip=True) == "Box score":
            game_id = int(match.group(1))
            if game_id not in ids:
                ids.append(game_id)
    return ids


_TEAM_HREF = re.compile(r"/team/[^/]+/(\d+)")
_PLAYED_ON = re.compile(r"played on\s+([A-Za-z]+ \d{1,2})(?:st|nd|rd|th),\s+(\d{4})")
_NUMBERED = re.compile(r"^#(\S+?)\.\s+(.+)$")
_SUB_GOALIE = re.compile(r"^Sub Goalie:\s*(.+)$")
_CAPTAIN = re.compile(r"\s+[CA]$")
_PERIOD_LABELS = {"1": Period.ONE, "2": Period.TWO, "3": Period.THREE, "OT": Period.OT}
_PERIOD_COLUMNS = {"Period 1": Period.ONE, "Period 2": Period.TWO, "Period 3": Period.THREE, "Overtime": Period.OT}


def parse_game(game_id: int, html: str) -> BoxScore:
    """Parse an AHA game page into a BoxScore.

    Raises ManualEntryRequired for games the sync must not enter (shootouts,
    forfeits) and ParseError for pages it doesn't recognize.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = _tables_by_headline(soup)
    if "Scoring" not in tables or "Goals" not in tables:
        raise ParseError(f"game {game_id}: no Scoring/Goals tables")

    teams, team_ids, final = _parse_scoring(tables["Scoring"])
    goals = tuple(_parse_goal(cells, teams) for cells in _rows(tables["Goals"]))
    for team in teams:
        listed = sum(1 for g in goals if g.team == team)
        if listed != final[team]:
            raise ManualEntryRequired(
                f"{team}'s final score is {final[team]} but AHA lists {listed} goals (forfeit?)"
            )

    return BoxScore(
        game_id=game_id,
        date=_parse_date(soup),
        teams=teams,
        team_ids=team_ids,
        final=final,
        goals=goals,
        penalties=tuple(_parse_penalty(c, teams) for c in _rows(tables.get("Penalties"))),
        shots=_parse_shots(tables.get("Shots on Goal"), teams),
        goalies=_parse_goalies(tables.get("Goalies"), teams),
    )


def _tables_by_headline(soup: BeautifulSoup) -> dict:
    tables = {}
    for headline in soup.select("h3.headline"):
        table = headline.find_next("table")
        if table is not None:
            tables[headline.get_text(strip=True)] = table
    return tables


def _rows(table) -> list[list[str]]:
    if table is None:
        return []
    return [[td.get_text(" ", strip=True) for td in tr.find_all("td")] for tr in table.select("tbody tr")]


def _period_columns(table) -> list[Period | None]:
    """Map each header column after "Team" to a Period, or None for "Final"."""
    columns = []
    for header in [td.get_text(strip=True) for td in table.select("thead td")][1:]:
        if header == "Final":
            columns.append(None)
        elif "shoot" in header.lower() or header == "SO":
            raise ManualEntryRequired("game was decided by a shootout")
        elif header in _PERIOD_COLUMNS:
            columns.append(_PERIOD_COLUMNS[header])
        else:
            raise ParseError(f"unknown column {header!r}")
    return columns


def _parse_scoring(table) -> tuple[tuple[str, str], tuple[int, int], dict[str, int]]:
    _period_columns(table)  # rejects shootouts and unknown layouts
    names, ids, final = [], [], {}
    for side in ("home", "away"):
        cell = table.select_one(f"#goals-{side}-final")
        link = cell.find_parent("tr").select_one("a[href]") if cell else None
        match = _TEAM_HREF.search(link["href"]) if link else None
        if not match:
            raise ParseError(f"no {side} team in scorebox")
        name = link.get_text(" ", strip=True)
        names.append(name)
        ids.append(int(match.group(1)))
        final[name] = _int(cell.get_text(strip=True))
    return (names[0], names[1]), (ids[0], ids[1]), final


def _parse_goal(cells: list[str], teams: tuple[str, str]) -> Goal:
    if len(cells) != 7:
        raise ParseError(f"unexpected goal row {cells}")
    scorer, team, assist1, assist2, strength, period, time = cells
    assists = tuple(_player(a) for a in (assist1, assist2) if a)
    return Goal(_team(team, teams), _period(period), time, _player(scorer), assists, strength)


def _parse_penalty(cells: list[str], teams: tuple[str, str]) -> Penalty:
    if len(cells) != 6:
        raise ParseError(f"unexpected penalty row {cells}")
    player, team, kind, period, minutes, time = cells
    on_bench = "(Bench)" in player
    return Penalty(
        _team(team, teams), None if on_bench else _player(player), kind, _period(period), _int(minutes), time
    )


def _parse_shots(table, teams: tuple[str, str]) -> dict[str, dict[Period, int]]:
    if table is None:
        return {}
    columns = _period_columns(table)
    shots = {}
    for cells in _rows(table):
        team, counts = cells[0], cells[1:]
        shots[_team(team, teams)] = {
            period: _int(count) for period, count in zip(columns, counts) if period is not None
        }
    return shots


def _parse_goalies(table, teams: tuple[str, str]) -> dict[str, AhaPlayer]:
    goalies: dict[str, AhaPlayer] = {}
    for cells in _rows(table):
        goalies.setdefault(_team(cells[1], teams), _player(cells[0]))  # first listed is the starter
    return goalies


def _parse_date(soup: BeautifulSoup) -> datetime.date:
    match = _PLAYED_ON.search(soup.get_text(" "))
    if not match:
        raise ParseError("no game date")
    return datetime.datetime.strptime(f"{match.group(1)} {match.group(2)}", "%B %d %Y").date()


def _player(text: str) -> AhaPlayer:
    text = " ".join(text.split())
    if match := _NUMBERED.match(text):
        number, name = match.groups()
    elif match := _SUB_GOALIE.match(text):
        number, name = "", match.group(1)
    elif text:
        number, name = "", text
    else:
        raise ParseError("empty player cell")
    return AhaPlayer(number, _CAPTAIN.sub("", name))


def _team(name: str, teams: tuple[str, str]) -> str:
    if name not in teams:
        raise ParseError(f"unknown team {name!r}")
    return name


def _period(label: str) -> Period:
    if label not in _PERIOD_LABELS:
        raise ParseError(f"unknown period {label!r}")
    return _PERIOD_LABELS[label]


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ParseError(f"expected a number, got {text!r}") from None
