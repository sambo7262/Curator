"""Phase-5 backoff schedule proof (STATE-03 / D-08): backoff_for is a pure function mapping an
attempt count to the next-retry delay in seconds — exponential 1h -> 6h -> 24h, capped at 24h.

Pure stdlib; no DB, no network — the fastest possible feedback target.
"""
from state.repo import BACKOFF_SECONDS, backoff_for


def test_backoff_schedule_1h_6h_24h():
    """Attempt 1 -> 1h, attempt 2 -> 6h, attempt 3 -> 24h (the documented D-08 ladder)."""
    assert backoff_for(1) == 3600       # 1 hour
    assert backoff_for(2) == 21600      # 6 hours
    assert backoff_for(3) == 86400      # 24 hours


def test_backoff_caps_at_24h():
    """Attempts beyond the ladder length stay capped at 24h (never grows unbounded)."""
    assert backoff_for(4) == 86400
    assert backoff_for(5) == 86400
    assert backoff_for(100) == 86400


def test_backoff_clamps_non_positive_attempts():
    """A 0 / negative attempt count clamps to the first rung (defensive — never IndexErrors)."""
    assert backoff_for(0) == 3600
    assert backoff_for(-1) == 3600


def test_backoff_seconds_table_shape():
    """The exported schedule is exactly the three documented rungs in ascending order."""
    assert BACKOFF_SECONDS == [3600, 21600, 86400]
