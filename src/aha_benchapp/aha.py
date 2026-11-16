"""Fetch and parse AHA Hockey team and game pages."""
import re

import requests
from bs4 import BeautifulSoup

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
