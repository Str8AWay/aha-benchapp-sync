from aha_benchapp.aha import completed_game_ids
from aha_benchapp.models import Status
from tests import fixture


def test_completed_game_ids_only_box_scores():
    ids = completed_game_ids(fixture("aha_team_308.html"))
    assert ids == [28819, 28852]  # links labeled "Box score"
    assert 28970 not in ids  # labeled "Game Info" (upcoming)


def test_status_retryable():
    assert Status.FAILED_NO_EVENT.retryable
    assert not Status.FAILED_MANUAL_ENTRY.retryable
    assert not Status.NEEDS_CLEANUP.retryable
