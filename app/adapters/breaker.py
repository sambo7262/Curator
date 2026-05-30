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

log = logging.getLogger(__name__)


class CircuitBreaker:
    """Drop-in ArrAdapter wrapper isolating a fault-prone inner adapter (Readarr).

    open  : >= fail_threshold consecutive recent failures -> get_wanted() short-circuits to [].
    closed: a successful get_wanted() resets the failure count.
    Either way, get_wanted() NEVER raises and NEVER returns anything but a list.
    """

    def __init__(self, inner, fail_threshold: int = 3, on_open=None):
        self._inner = inner
        self.fail_threshold = fail_threshold
        self._on_open = on_open
        self._failures = 0
        # Mirror the inner adapter's identity so the breaker is a transparent ArrAdapter.
        self.app = getattr(inner, "app", "readarr")

    def _open(self) -> bool:
        """The breaker is open once consecutive failures reach the threshold."""
        return self._failures >= self.fail_threshold

    def _record_failure(self, exc: Exception) -> None:
        self._failures += 1
        log.warning(
            "circuit breaker recorded failure %d/%d for %s: %s",
            self._failures, self.fail_threshold, self.app, exc,
        )
        if self._open() and self._on_open is not None:
            self._on_open(self._failures)

    def get_wanted(self) -> list:
        """Return the inner adapter's gaps, or [] if the breaker is open or the inner call faults."""
        if self._open():
            # Skip Readarr entirely this run — do not even attempt the inner call.
            log.warning("circuit breaker OPEN for %s; skipping (returning [])", self.app)
            return []
        try:
            result = self._inner.get_wanted()
            self._failures = 0   # success closes the breaker
            return result
        except Exception as e:   # last-resort: a Readarr fault NEVER propagates to the core
            self._record_failure(e)
            return []
