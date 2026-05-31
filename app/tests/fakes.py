"""05-02-owned shared fakes (Wave 0).

This module is created by plan 05-02 (NOT conftest.py, which 05-01 solely owns this wave)
so the two waves have ZERO same-wave file overlap. The shares / reconcile / scheduler test
modules import `from tests.fakes import FakeSlskd` (or `from fakes import FakeSlskd` per the
test import root) directly — no conftest fixture is needed.

`FakeSlskd` exposes the NEUTRAL slskd seam the Phase-5 core services compose:
  - `get_shared_file_count()` -> injectable int (the neutral count core/shares.py consumes;
    the real adapter reads the `shares.files` wire key, the fake just returns the int).
  - `rescan_shares()` -> injectable bool, with a `rescan_calls` counter so a test can assert
    the self-heal triggered a rescan exactly once.
  - the search / transfer / enqueue / cancel no-ops the later scheduler path reuses, so the
    same fake drops into the scheduler/reconcile tests in Waves 1/2.

It speaks ONLY the neutral shapes (int / bool / the opaque TransferHandle) — it never makes a
caller read a wire key, so it is firewall-safe for the core-side tests that import it.
"""
from typing import List, Optional


class FakeSlskd:
    """A neutral-seam slskd stand-in for the Phase-5 shares + orchestration tests.

    Shares seam (the 05-02/05-03 focus):
      count_sequence : successive get_shared_file_count() return values (last repeats). A bare
                       int is accepted and treated as a constant. Defaults to a healthy 1234.
      rescan_result  : what rescan_shares() returns (True=started / False=already-scanning).
                       rescan_shares() also increments rescan_calls (call-counted self-heal).

    Search/transfer seam (reused by the scheduler/reconcile path in later waves): minimal no-ops
    that record what they were asked to do so a test can assert dispatch behavior.
    """

    app = "slskd"

    def __init__(
        self,
        count_sequence=1234,
        rescan_result: bool = True,
        responses: Optional[list] = None,
    ):
        # Normalize a bare int into a one-element sequence (constant count).
        if isinstance(count_sequence, int):
            self._count_sequence: List[int] = [count_sequence]
        else:
            self._count_sequence = list(count_sequence)
        self._count_idx = 0
        self._rescan_result = rescan_result

        # Observability for assertions.
        self.rescan_calls = 0
        self.count_calls = 0
        self.enqueued = []        # (username, files)
        self.cancelled = []       # (username, transfer_id)
        self.searches = []        # every search text submitted

        self._responses = list(responses) if responses is not None else []

    # --- shares ensure / self-heal seam (the neutral int + bool core/shares.py consumes) --------

    def get_shared_file_count(self) -> int:
        """Return the next scripted shared-file count (the last element repeats)."""
        self.count_calls += 1
        idx = min(self._count_idx, len(self._count_sequence) - 1)
        self._count_idx += 1
        return self._count_sequence[idx]

    def rescan_shares(self) -> bool:
        """Record + return the rescan result (call-counted so a test asserts exactly-once)."""
        self.rescan_calls += 1
        return self._rescan_result

    # --- search / transfer / enqueue / cancel no-ops (reused by the scheduler path) -------------

    def search(self, text: str) -> str:
        self.searches.append(text)
        return f"sid-{len(self.searches)}"

    def search_is_complete(self, search_id: str) -> bool:
        return True

    def search_responses(self, search_id: str) -> list:
        return list(self._responses)

    def enqueue_candidate(self, candidate):
        from adapters.slskd import TransferHandle, _remote_folder_leaf

        self.enqueued.append((candidate.username, list(candidate.files)))
        return TransferHandle(
            username=candidate.username,
            transfer_id=candidate.username,
            landing_dir_name=_remote_folder_leaf(getattr(candidate, "folder", "")),
        )

    def transfer_progress(self, handle):
        from core.acquire import TransferProgress

        return TransferProgress(terminal="success", bytes_done=1)

    def cancel_transfer(self, handle, remove: bool = True) -> None:
        self.cancelled.append((handle.username, handle.transfer_id))
