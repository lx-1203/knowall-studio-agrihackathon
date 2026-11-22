"""Real-library and legacy-state scheduling regressions; no external calls."""
from datetime import datetime, timedelta, timezone
import math

import pytest
from fsrs import Card, Rating, Scheduler, State
import app.core.memory as memory


class Clock(datetime):
    current = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    Clock.current = datetime(2026, 10, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(memory, "datetime", Clock)
    return Clock


def test_successful_due_reviews_no_longer_collapse_to_one_day(clock):
    scheduler = memory.FSRS()
    state = scheduler.review(scheduler.init_card(), 3)
    intervals = []
    for _ in range(5):
        clock.current = state["next_review_at"].replace(tzinfo=timezone.utc)
        state = scheduler.review(state, 3)
        intervals.append((state["next_review_at"] - state["last_review_at"]).total_seconds() / 86400)
        assert state["stability"] > 0, "successful recall collapsed stability"
    assert intervals[-1] > intervals[0] > 1


@pytest.mark.parametrize("rating", [1, 2, 3, 4])
def test_first_rating_delegates_to_real_upstream_library(clock, rating):
    scheduler = memory.FSRS()
    actual = scheduler.review(scheduler.init_card(), rating)
    official = Scheduler(learning_steps=(timedelta(minutes=1),),
                         relearning_steps=(timedelta(minutes=10),), enable_fuzzing=False)
    expected, _ = official.review_card(Card(card_id=1, due=clock.current), Rating(rating), clock.current)
    assert actual["stability"] == pytest.approx(expected.stability)
    assert actual["difficulty"] == pytest.approx(expected.difficulty)
    assert actual["next_review_at"] == expected.due.replace(tzinfo=None)
    assert actual["fsrs_version"] == "fsrs-6"


@pytest.mark.parametrize("rating", [0, 5, -1, True, 2.5, "3"])
def test_invalid_rating_rejected_without_mutating_input(rating):
    state = memory.FSRS().init_card()
    before = dict(state)
    with pytest.raises(ValueError, match="rating"):
        memory.FSRS().review(state, rating)
    assert state == before


def test_aware_utc_and_offset_dates_produce_same_result(clock):
    scheduler = memory.FSRS()
    state = scheduler.review(scheduler.init_card(), 4)
    clock.current += timedelta(days=8)
    aware = dict(state, last_review_at=state["last_review_at"].replace(tzinfo=timezone.utc),
                 next_review_at=state["next_review_at"].replace(tzinfo=timezone.utc))
    offset = dict(aware, last_review_at=aware["last_review_at"].astimezone(timezone(timedelta(hours=8))))
    assert scheduler.review(state, 3) == scheduler.review(aware, 3) == scheduler.review(offset, 3)


def test_legacy_normalized_difficulty_converted_only_once(clock):
    scheduler = memory.FSRS()
    state = dict(scheduler.init_card(), fsrs_version=None, state="review", stability=5, difficulty=0.5,
                 review_count=7, last_review_at=clock.current.replace(tzinfo=None) - timedelta(days=5))
    actual = scheduler.review(state, 3)
    official = Scheduler(learning_steps=(timedelta(minutes=1),), enable_fuzzing=False)
    card = Card(card_id=1, state=State.Review, stability=5, difficulty=5.5,
                due=clock.current, last_review=clock.current - timedelta(days=5))
    expected, _ = official.review_card(card, Rating.Good, clock.current)
    assert actual["difficulty"] == pytest.approx(expected.difficulty)
    assert actual["review_count"] == 8 and state["difficulty"] == 0.5
    clock.current = actual["next_review_at"].replace(tzinfo=timezone.utc)
    again = scheduler.review(actual, 3)
    assert again["fsrs_version"] == "fsrs-6" and again["review_count"] == 9
    assert 1 <= again["difficulty"] <= 10


@pytest.mark.parametrize("stability", [0, -3, float("nan")])
def test_damaged_legacy_state_recovers_without_erasing_review_count(clock, stability):
    scheduler = memory.FSRS()
    legacy = dict(scheduler.init_card(), fsrs_version=None, stability=stability, difficulty=1, state="review", review_count=23)
    actual = scheduler.review(legacy, 3)
    assert math.isfinite(actual["stability"]) and actual["stability"] > 0
    assert actual["review_count"] == 24 and actual["fsrs_version"] == "fsrs-6"


def test_again_and_good_restore_review_state(clock):
    scheduler = memory.FSRS()
    state = scheduler.review(scheduler.init_card(), 4)
    clock.current = state["next_review_at"].replace(tzinfo=timezone.utc)
    state = scheduler.review(state, 1)
    assert state["state"] == "relearning"
    assert state["next_review_at"] - state["last_review_at"] == timedelta(minutes=10)
    clock.current = state["next_review_at"].replace(tzinfo=timezone.utc)
    assert scheduler.review(state, 3)["state"] == "review"


def test_legacy_17_parameter_vector_is_not_silently_used():
    with pytest.raises(ValueError, match="21"):
        memory.FSRS([0.5] * 17)


def test_versioned_minimum_difficulty_is_not_legacy_rescaled(clock):
    scheduler = memory.FSRS()
    state = dict(scheduler.init_card(), state="review", stability=5, difficulty=1,
                 fsrs_version="fsrs-6", last_review_at=clock.current.replace(tzinfo=None) - timedelta(days=5))
    assert scheduler.review(state, 4)["difficulty"] == 1
