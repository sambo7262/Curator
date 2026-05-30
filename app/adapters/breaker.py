# Curator CircuitBreaker — the structural guarantee that "books never gate music" (ARR-02).
# Wraps an inner ArrAdapter (the ReadarrAdapter): after `fail_threshold` consecutive failures
# the breaker opens and get_wanted() returns [] WITHOUT even attempting the inner call, so a
# hard-down / hung Readarr cannot stall the detection run. On ANY exception from the inner
# adapter the breaker records a failure and returns [] — a Readarr fault NEVER propagates to
# the core. A success resets the failure count (closes the breaker).
#
# A ~30-line hand-rolled breaker is the right scale here (single-process homelab); a library
# would be overkill. The breaker is a drop-in ArrAdapter — it exposes the inner adapter's `app`.
import logging
import time

log = logging.getLogger(__name__)


class CircuitBreaker:
    """Drop-in ArrAdapter wrapper isolating a fault-prone inner adapter (Readarr).

    closed   : normal operation; a successful get_wanted() resets the failure count.
    open      : >= fail_threshold consecutive recent failures -> get_wanted() short-circuits to []
                WITHOUT attempting the inner call, until reset_after seconds have elapsed.
    half-open : once reset_after has elapsed since the breaker opened, the NEXT get_wanted()
                attempts ONE trial inner call — success closes the breaker (Readarr recovered),
                failure re-arms the open timer for another reset_after window.

    The cooldown is a PASSIVE elapsed-time check on the next call (a monotonic clock read), NOT a
    scheduler/background loop — so books re-enable automatically once Readarr recovers instead of
    staying disabled until a container restart (WR-04). Either way, get_wanted() NEVER raises and
    NEVER returns anything but a list.
    """

    def __init__(self, inner, fail_threshold: int = 3, reset_after: float = 300.0, on_open=None):
        self._inner = inner
        self.fail_threshold = fail_threshold
        self.reset_after = reset_after        # seconds before a half-open trial call is allowed
        self._on_open = on_open
        self._failures = 0
        self._opened_at = None                # monotonic timestamp the breaker last opened, or None
        # Mirror the inner adapter's identity so the breaker is a transparent ArrAdapter.
        self.app = getattr(inner, "app", "readarr")

    def _now(self) -> float:
        """Monotonic clock (immune to wall-clock jumps) for the cooldown elapsed-time check."""
        return time.monotonic()

    def _tripped(self) -> bool:
        """True once consecutive failures reach the threshold (the failure count itself is open)."""
        return self._failures >= self.fail_threshold

    def _cooldown_elapsed(self) -> bool:
        """True once reset_after seconds have passed since the breaker opened (half-open window)."""
        return self._opened_at is not None and (self._now() - self._opened_at) >= self.reset_after

    def _open(self) -> bool:
        """Backwards-compatible 'is the breaker latched open' check (failure threshold reached)."""
        return self._tripped()

    def _record_failure(self, exc: Exception) -> None:
        self._failures += 1
        if self._tripped() and self._opened_at is None:
            # Just crossed the threshold — stamp the open time so the cooldown can elapse.
            self._opened_at = self._now()
        log.warning(
            "circuit breaker recorded failure %d/%d for %s: %s",
            self._failures, self.fail_threshold, self.app, exc,
        )
        if self._tripped() and self._on_open is not None:
            self._on_open(self._failures)

    def _close(self) -> None:
        """Reset to the closed state after a successful (or successful trial) inner call."""
        self._failures = 0
        self._opened_at = None

    def get_wanted(self) -> list:
        """Return the inner adapter's gaps, or [] if the breaker is open or the inner call faults.

        While open, short-circuits to [] until reset_after elapses; then allows ONE half-open trial
        call whose outcome either closes the breaker (recovery) or re-arms the open timer.
        """
        if self._tripped() and not self._cooldown_elapsed():
            # Still cooling down — skip Readarr entirely this run; do not attempt the inner call.
            log.warning("circuit breaker OPEN for %s; skipping (returning [])", self.app)
            return []
        # Either closed, or open-but-cooldown-elapsed (half-open): attempt ONE inner call.
        if self._tripped():
            log.info("circuit breaker HALF-OPEN for %s; attempting one trial call", self.app)
        try:
            result = self._inner.get_wanted()
            self._close()   # success (incl. a half-open trial) closes the breaker
            return result
        except Exception as e:   # last-resort: a Readarr fault NEVER propagates to the core
            # On a half-open trial failure, re-arm the timer for another full cooldown window.
            self._opened_at = self._now() if self._tripped() else self._opened_at
            self._record_failure(e)
            return []
