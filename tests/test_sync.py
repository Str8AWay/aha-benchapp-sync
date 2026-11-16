from dataclasses import replace

import pytest

from aha_benchapp import sync
from aha_benchapp.aha import parse_game
from aha_benchapp.models import Period, Status
from aha_benchapp.state import MemoryStateStore
from aha_benchapp.sync import SyncContext, sync_game
from tests import fixture
from tests.fakes import EVENT, LES_BA, MOOSE_BA, FakeClient, roster_player

LES = "Les Etoiles du Nord C1"


@pytest.fixture
def box():
    return parse_game(28819, fixture("aha_game_28819.html"))


@pytest.fixture
def ctx():
    return SyncContext(client=FakeClient(), store=MemoryStateStore(), team_id=LES_BA, aha_team_id=308, dry_run=False)


def calls(ctx, name):
    return [args for method, args in ctx.client.calls if method == name]


def shots_for(ctx, team, period):
    return sum(1 for args in calls(ctx, "create_shot") if args[1] == team and args[2] == period)


def mutations(ctx):
    return [m for m in ctx.client.names() if m.startswith(("create", "set"))]


def test_goals_written_before_anything_else(ctx, box):
    sync_game(ctx, box, [EVENT])
    names = ctx.client.names()
    last_point = max(i for i, n in enumerate(names) if n == "create_point")
    assert names.count("create_point") == 7
    assert all(n not in ("create_penalty", "create_shot", "create_goalie") for n in names[:last_point])


def test_goal_details(ctx, box):
    sync_game(ctx, box, [EVENT])
    first = calls(ctx, "create_point")[0]
    # event, season, team, homeAway, period, time, scorer #5, assists (#24,), type
    assert first == (3766427, 531381, LES_BA, "Home", "One", "11:20", 1005, (1024,), "EvenStrength")
    moose_goal = calls(ctx, "create_point")[1]
    assert moose_goal[2:6] == (MOOSE_BA, "Away", "One", "06:54")


def test_opponent_players_created_as_temp_and_marked_in(ctx, box):
    sync_game(ctx, box, [EVENT])
    created = calls(ctx, "create_roster_player")
    assert {a[3] for a in created} == {"80", "8", "92", "78", "25", "37", "23"}  # the same 7 the manager created
    assert all(a[0] == MOOSE_BA and a[1] == 531381 for a in created)
    assert len(calls(ctx, "set_attendance")) == 7


def test_existing_opponent_players_reused(ctx, box):
    ctx.client.away_players = [roster_player("80", temp=True), roster_player("8", temp=True)]
    sync_game(ctx, box, [EVENT])
    assert {a[3] for a in calls(ctx, "create_roster_player")} == {"92", "78", "25", "37", "23"}


def test_synced_status_and_state(ctx, box):
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.SYNCED, r.warnings
    assert r.event_id == 3766427 and len(r.created_point_ids) == 7
    assert ctx.store.get(28819).status is Status.SYNCED


def test_goalies_penalty_shots_and_period(ctx, box):
    sync_game(ctx, box, [EVENT])
    goalies = calls(ctx, "create_goalie")
    assert (3766427, LES_BA, 1030) in goalies and len(goalies) == 2
    (penalty,) = calls(ctx, "create_penalty")
    assert penalty[2:6] == (MOOSE_BA, "Away", "Two", "11:07")
    assert penalty[7] == {"kind": "Hooking", "duration": "Two", "type": "Minor"}
    assert [shots_for(ctx, LES_BA, p) for p in ("One", "Two", "Three")] == [12, 17, 10]
    assert [shots_for(ctx, MOOSE_BA, p) for p in ("One", "Two", "Three")] == [15, 18, 10]
    assert calls(ctx, "set_period") == [(3766427, "Three")]


def test_skips_event_with_existing_points(ctx, box):
    ctx.client.has_points_value = True
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.SKIPPED_EXISTING and r.event_id == 3766427
    assert mutations(ctx) == []


def test_no_matching_event(ctx, box):
    assert sync_game(ctx, box, []).status is Status.FAILED_NO_EVENT
    assert mutations(ctx) == []


def test_ambiguous_event(ctx, box):
    assert sync_game(ctx, box, [EVENT, {**EVENT, "id": 1}]).status is Status.FAILED_AMBIGUOUS_EVENT
    assert mutations(ctx) == []


def test_unmatched_teammate_becomes_temp_with_warning(ctx, box):
    ctx.client.home_players = [p for p in ctx.client.home_players if p["playerNumberDisplay"] != "43"]
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.SYNCED_WITH_WARNINGS
    assert any("#43" in w and "temp player" in w for w in r.warnings)
    assert any(a[0] == LES_BA and a[3] == "43" for a in calls(ctx, "create_roster_player"))


def test_sub_goalie_on_skater_number_becomes_temp(ctx, box):
    ctx.client.home_players = [p for p in ctx.client.home_players if p["playerNumberDisplay"] != "30"] + [roster_player("30")]
    r = sync_game(ctx, box, [EVENT])
    assert any(a[0] == LES_BA and a[3] == "30" for a in calls(ctx, "create_roster_player"))
    assert any("#30" in w for w in r.warnings)


def test_referenced_teammate_marked_in(ctx, box):
    ctx.client.home_players = [roster_player("24", status="Out") if p["playerNumberDisplay"] == "24" else p
                               for p in ctx.client.home_players]
    sync_game(ctx, box, [EVENT])
    assert (3766427, 531381, LES_BA, 1024) in calls(ctx, "set_attendance")


def test_api_error_before_any_goal_is_retryable(ctx, box):
    ctx.client.fail_on = ("create_point", 1)
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.FAILED_API_ERROR and r.created_point_ids == []
    assert any("simulated failure" in w for w in r.warnings)
    assert ctx.store.get(28819).status is Status.FAILED_API_ERROR


def test_api_error_reading_event_is_retryable(ctx, box):
    ctx.client.fail_on = ("has_points", 1)
    assert sync_game(ctx, box, [EVENT]).status is Status.FAILED_API_ERROR


def test_api_error_after_first_goal_needs_cleanup(ctx, box):
    ctx.client.fail_on = ("create_point", 2)
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.NEEDS_CLEANUP and len(r.created_point_ids) == 1
    assert ctx.store.get(28819).created_point_ids == r.created_point_ids


def test_score_mismatch_needs_cleanup_and_stops(ctx, box):
    ctx.client.stats_override = {"homeTeamScore": 3, "awayTeamScore": 3, "homeTeamShots": 0, "awayTeamShots": 0}
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.NEEDS_CLEANUP
    assert any("score mismatch: BenchApp 3–3, AHA 3–4" in w for w in r.warnings)
    assert not any(n in ("create_penalty", "create_shot", "create_goalie") for n in ctx.client.names())


@pytest.mark.parametrize("auto, expected_p1", [(True, 11), (False, 12)])
def test_shot_counts_respect_goal_rule(ctx, box, monkeypatch, auto, expected_p1):
    monkeypatch.setattr(sync.enums, "GOAL_COUNTS_AS_SHOT", auto)
    sync_game(ctx, box, [EVENT])
    assert shots_for(ctx, LES_BA, "One") == expected_p1


def test_shots_clamped_when_goals_exceed_shots(ctx, box, monkeypatch):  # Review Focus 3
    monkeypatch.setattr(sync.enums, "GOAL_COUNTS_AS_SHOT", True)
    shots = {**box.shots, LES: {**box.shots[LES], Period.ONE: 0}}
    r = sync_game(ctx, replace(box, shots=shots), [EVENT])
    assert shots_for(ctx, LES_BA, "One") == 0
    assert any("shots" in w for w in r.warnings)


def test_shot_total_mismatch_is_warning(ctx, box):
    ctx.client.stats_override = {"homeTeamScore": 3, "awayTeamScore": 4, "homeTeamShots": 1, "awayTeamShots": 43}
    r = sync_game(ctx, box, [EVENT])
    assert r.status is Status.SYNCED_WITH_WARNINGS and any("shot" in w for w in r.warnings)


def test_missing_shots_is_warning(ctx, box):  # Review Focus 4
    r = sync_game(ctx, replace(box, shots={}), [EVENT])
    assert r.status is Status.SYNCED_WITH_WARNINGS and "AHA hasn't posted shots yet" in r.warnings
    assert "create_shot" not in ctx.client.names()


def test_unknown_penalty_kind_is_warning(ctx, box):
    r = sync_game(ctx, replace(box, penalties=(replace(box.penalties[0], kind="Being Too Handsome"),)), [EVENT])
    assert "create_penalty" not in ctx.client.names()
    assert r.status is Status.SYNCED_WITH_WARNINGS and any("Being Too Handsome" in w for w in r.warnings)


def test_bench_penalty_is_warning(ctx, box):
    r = sync_game(ctx, replace(box, penalties=(replace(box.penalties[0], player=None),)), [EVENT])
    assert "create_penalty" not in ctx.client.names()
    assert any("bench" in w for w in r.warnings)


def test_goalie_failure_is_warning(ctx, box):
    ctx.client.fail_on = ("create_goalie", 1)
    assert sync_game(ctx, box, [EVENT]).status is Status.SYNCED_WITH_WARNINGS


def test_overtime_goal_sets_ot_period(ctx, box):
    ot_goal = replace(box.goals[-1], period=Period.OT)
    r = sync_game(ctx, replace(box, goals=box.goals[:-1] + (ot_goal,)), [EVENT])
    assert calls(ctx, "create_point")[-1][4] == "OT"
    assert calls(ctx, "set_period") == [(3766427, "OT")]
    assert r.status is Status.SYNCED


def test_power_play_goal_type(ctx, box):
    pp_goal = replace(box.goals[0], strength="PP")
    sync_game(ctx, replace(box, goals=(pp_goal,) + box.goals[1:]), [EVENT])
    assert calls(ctx, "create_point")[0][8] == "PowerPlay"


def test_dry_run_writes_no_state(ctx, box):
    ctx.dry_run = True
    sync_game(ctx, box, [EVENT])
    assert ctx.store.get(28819) is None
    assert "event_stats" not in ctx.client.names()
