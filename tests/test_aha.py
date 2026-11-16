from datetime import date

import pytest

from aha_benchapp.aha import completed_game_ids, parse_game
from aha_benchapp.models import AhaPlayer, Goal, ManualEntryRequired, ParseError, Penalty, Period, Status
from tests import fixture


def test_completed_game_ids_only_box_scores():
    ids = completed_game_ids(fixture("aha_team_308.html"))
    assert ids == [28819, 28852]  # links labeled "Box score"
    assert 28970 not in ids  # labeled "Game Info" (upcoming)


def test_status_retryable():
    assert Status.FAILED_NO_EVENT.retryable
    assert not Status.FAILED_MANUAL_ENTRY.retryable
    assert not Status.NEEDS_CLEANUP.retryable


LES, MOOSE = "Les Etoiles du Nord C1", "Moose C1"


def test_parse_game_28819():
    box = parse_game(28819, fixture("aha_game_28819.html"))
    assert box.game_id == 28819
    assert box.date == date(2026, 11, 8)
    assert box.teams == (LES, MOOSE)
    assert box.team_ids == (308, 547)
    assert box.final == {LES: 3, MOOSE: 4}
    assert len(box.goals) == 7
    assert box.goals[0] == Goal(LES, Period.ONE, "11:20", AhaPlayer("5", "R. Steckman"), (AhaPlayer("24", "K. Sherlund"),))
    assert box.goals[2].assists == (AhaPlayer("92", "R. Riepe"), AhaPlayer("78", "S. Angell"))
    assert box.penalties == (Penalty(MOOSE, AhaPlayer("23", "A. Cleary"), "Hooking", Period.TWO, 2, "11:07"),)
    assert box.shots == {
        LES: {Period.ONE: 12, Period.TWO: 17, Period.THREE: 10},
        MOOSE: {Period.ONE: 15, Period.TWO: 18, Period.THREE: 10},
    }
    assert box.goalies == {LES: AhaPlayer("30", "T. Agerter"), MOOSE: AhaPlayer("37", "J. Bader")}


def test_parse_game_overtime_goal():
    box = parse_game(28817, fixture("aha_game_ot.html"))
    assert box.goals[-1].period is Period.OT
    assert box.final == {"Leftovers": 4, "House Hippos": 5}
    assert box.shots["House Hippos"][Period.OT] == 2


def test_parse_game_real_page_quirks():
    box = parse_game(28817, fixture("aha_game_ot.html"))
    assert box.goals[0].scorer == AhaPlayer("9", "J. Barthelmy")  # double space collapsed
    assert box.goals[0].assists[1] == AhaPlayer("15", "A. Bakken")  # captain "C" stripped
    assert box.goals[0].strength == "EN"
    assert box.goals[4].strength == "PP"
    assert box.goals[1].strength == ""
    assert box.goalies["House Hippos"] == AhaPlayer("", "A. Ledo")  # "Sub Goalie: A. Ledo"


def test_parse_game_bench_penalty_has_no_player():
    box = parse_game(28816, fixture("aha_game_bench_penalty.html"))
    bench = [p for p in box.penalties if p.kind == "Too Many Players"]
    assert len(bench) == 1 and bench[0].player is None


def test_parse_game_shootout_requires_manual_entry():
    with pytest.raises(ManualEntryRequired, match="shootout"):
        parse_game(1, fixture("aha_game_shootout.html"))


def test_parse_game_forfeit_requires_manual_entry():
    with pytest.raises(ManualEntryRequired, match="final score"):
        parse_game(1, fixture("aha_game_forfeit.html"))


def test_parse_game_without_shots_table():  # Review Focus 4
    box = parse_game(28819, fixture("aha_game_no_shots.html"))
    assert box.shots == {}
    assert len(box.goals) == 7


def test_parse_game_unrecognized_page():
    with pytest.raises(ParseError):
        parse_game(1, "<html><body>nothing here</body></html>")


def _without_section_rows(html: str, headline: str, rows: str) -> str:
    """Replace the tbody of the table after `headline` with `rows`."""
    start = html.index(f'<h3 class="headline">{headline}</h3>')
    body_start = html.index("<tbody>", start) + len("<tbody>")
    body_end = html.index("</tbody>", body_start)
    return html[:body_start] + rows + html[body_end:]


def test_parse_game_bad_shots_section_is_a_warning():
    html = _without_section_rows(fixture("aha_game_28819.html"), "Shots on Goal",
                                 "<tr><td>Les Etoiles du Nord C1</td><td></td><td></td><td></td><td></td></tr>")
    box = parse_game(28819, html)
    assert box.shots == {}
    assert len(box.goals) == 7 and box.final == {LES: 3, MOOSE: 4}
    assert any("shots" in w.lower() for w in box.parse_warnings)


def test_parse_game_goalie_placeholder_row_is_a_warning():
    html = _without_section_rows(fixture("aha_game_28819.html"), "Goalies",
                                 '<tr><td colspan="5">No goalies recorded</td></tr>')
    box = parse_game(28819, html)
    assert box.goalies == {}
    assert any("goalies" in w.lower() for w in box.parse_warnings)


def test_parse_game_bad_penalty_row_is_a_warning():
    html = _without_section_rows(fixture("aha_game_28819.html"), "Penalties",
                                 "<tr><td>#23. A. Cleary</td><td>Moose C1</td><td>Hooking</td><td>9</td><td>2</td><td>11:07</td></tr>")
    box = parse_game(28819, html)
    assert box.penalties == ()
    assert any("penalties" in w.lower() for w in box.parse_warnings)


def test_parse_game_clean_page_has_no_parse_warnings():
    assert parse_game(28819, fixture("aha_game_28819.html")).parse_warnings == ()
