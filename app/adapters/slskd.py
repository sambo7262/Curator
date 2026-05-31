# Curator SlskdClient — the thin, hand-owned Curator→slskd REST surface (ACQ-01/02/03, IMPORT-05).
# This is Curator's FIRST and ONLY client for the slskd /api/v0 API: submit a search, poll its
# state, read its responses, enqueue a download, poll the transfer's state/bytes, and cancel.
#
# It mirrors lidarr.py's constructor and defensive posture exactly, with two slskd-specific
# differences: the base appends /api/v0 (not /api/v1) and the auth header is the CAPITALIZED
# X-API-Key (not the Servarr X-Api-Key). slskd is reached ONLY via gluetun's published port
# (http://<NAS-IP>:5030, Pitfall 7) — the host comes from settings.slskd_url, NEVER hardcoded
# and NEVER a container name (slskd runs network_mode: service:gluetun and is not on synobridge).
#
# Error posture: slskd is now the PRIMARY download path, so a hard fault must SURFACE
# (raise_for_status on every call) rather than being silently swallowed — exactly like
# lidarr.py (the primary music path). The CircuitBreaker seam already exists if Phase 5 wants
# to wrap transient slskd outages; this client deliberately does NOT wrap here.
#
# Every response field is read with .get() (never subscript): the JSON crosses an untrusted
# peer→slskd→Curator boundary, and one malformed/absent field must surface as a handled None,
# not a KeyError (T-04-06). The X-API-Key lives ONLY in self._headers, sourced from settings —
# never logged, never echoed into an exception message (T-04-07).
#
# The injected httpx.Client makes the whole surface offline-provable with httpx.MockTransport /
# respx (no live slskd) — see tests/test_slskd_client.py.
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferHandle:
    """An OPAQUE handle to an enqueued slskd transfer. acquire.py holds it across the watch/cancel
    calls WITHOUT ever reading a username (which is a SELECTOR-ONLY Candidate field) — the firewall
    boundary for the uploader identity. The username/transfer_id fields are slskd-internal addressing,
    not core vocabulary; acquire treats the whole handle as a token.

    `landing_dir_name` (A2 — pinned live 2026-05-31, 04-05-LIVE-PROBE.md) is the NEUTRAL leaf-of-the-
    remote-folder directory name slskd actually lands the files in under the downloads root. slskd
    uses ONLY the last path segment of the peer's remote folder (peer `music\\ZHU\\BLACK MIDAS (2026)`
    -> local `<downloads_root>/BLACK MIDAS (2026)/`), flat, with no `<username>/` or `<batchId>/`
    subdir. acquire reads this neutral string (NOT the wire `folder` key) to resolve the real import
    source + purge/quarantine target. It is a plain dir name, not *arr/slskd wire vocabulary."""

    username: str
    transfer_id: str
    landing_dir_name: str = ""


def _remote_folder_leaf(remote_dir: str) -> str:
    """Return the last path segment of a slskd remote folder, splitting on BOTH `\\` and `/`.

    slskd reports peer paths with `\\` (backslash) separators (e.g. `music\\ZHU\\BLACK MIDAS (2026)`)
    and lands the files locally under ONLY that last segment (A2). Pure, defensive string logic; an
    empty/odd input yields "" (the caller falls back to its own deterministic label)."""
    if not isinstance(remote_dir, str):
        return ""
    leaf = remote_dir.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return leaf.strip()

# --- Transfer terminal-state substrings (A3 — PINNED LIVE 2026-05-31, see 04-05-LIVE-PROBE.md) ---
# slskd reports a transfer's lifecycle as a compound flag `state` string "<phase>, <completion>".
# LIVE-OBSERVED terminal success on the NAS = "Completed, Succeeded" (ZHU – BLACK MIDAS, 14 FLAC
# tracks, 2026-05-31). The neutral progress interpretation (terminal success vs failure vs
# in-progress) lives in acquire.py (04-04) — this client only EXPOSES the raw transfer dict and
# interprets the state here in transfer_progress. These named constants are pinned HERE in ONE place;
# the fixtures (slskd/transfer_*.json) use the same literals. Do NOT bury state parsing in the
# client beyond this module-level vocabulary.
#
# ROBUST RULE (the substring rule is authoritative, not the exact literals): a transfer is
# TERMINAL iff its state contains "Completed"; SUCCESS iff it also contains "Succeeded"; any other
# terminal "Completed, *" (e.g. "Completed, Errored", "Completed, Cancelled") is a FAILURE -> fall
# to the next candidate. Only the success family was observed live; the failure family is inferred,
# so we keep the substring rule (not an unobserved exact literal) but retain named constants for the
# expected failure/cancelled strings as documentation.
STATE_COMPLETED_SUCCEEDED = "Completed, Succeeded"   # [LIVE-OBSERVED A3] terminal success
STATE_IN_PROGRESS = "InProgress"                     # still-transferring (no "Completed")
STATE_FAILED = "Completed, Errored"                  # [inferred A3] terminal failure family
STATE_CANCELLED = "Completed, Cancelled"             # [inferred A3] terminal cancelled family
# The substrings acquire.py's progress seam tests the live `state` against. "Completed" gates
# terminality; the success/failure substrings then disambiguate the completion half (A3 rule):
TERMINAL_PHASE_SUBSTRING = "Completed"
TERMINAL_SUCCESS_SUBSTRINGS = ("Succeeded",)
TERMINAL_FAILURE_SUBSTRINGS = ("Errored", "Failed", "Cancelled", "Rejected", "Aborted")


class SlskdClient:
    """Thin defensive client over the slskd /api/v0 search + transfer API.

    Constructed with the slskd base URL (settings.slskd_url, gluetun-published), the API key
    (settings.slskd_api_key), and an injected httpx.Client (offline-testable). A None/empty key
    is a hard, clearly-reported construction error — slskd is the primary download path, so a
    misconfigured key must fail loudly at startup, not produce an opaque header-encoding error on
    the first request (mirrors LidarrAdapter's CR-01 fail-fast).
    """

    app = "slskd"

    def __init__(self, base_url: str, api_key: str, client: httpx.Client):
        if not api_key:
            raise ValueError("SLSKD_API_KEY is required (slskd is the primary download path)")
        self._base = base_url.rstrip("/") + "/api/v0"
        self._client = client
        self._headers = {"X-API-Key": api_key}   # slskd auth header: CAPITAL API (not Servarr's X-Api-Key)

    # --- search ---------------------------------------------------------------------------------

    def search(self, text: str) -> Optional[str]:
        """POST /searches with {"searchText": text}; return the new search id (.get(), None-safe).

        A response missing `id` yields None rather than a KeyError — the caller (04-04) treats a
        None search id as a failed submit and surfaces/retries it.
        """
        r = self._client.post(
            f"{self._base}/searches",
            headers=self._headers,
            json={"searchText": text},
            timeout=30.0,
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            return None
        return body.get("id")

    def search_state(self, search_id: str) -> dict:
        """GET /searches/{id}; return the state dict (isComplete/responseCount/fileCount readable)."""
        r = self._client.get(
            f"{self._base}/searches/{search_id}",
            headers=self._headers,
            timeout=30.0,
        )
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, dict) else {}

    def search_is_complete(self, search_id: str) -> bool:
        """NEUTRAL seam over search_state: True iff slskd reports the search complete.

        This is the firewall boundary for the collection-window poll: the *arr/slskd wire key
        `isComplete` is read HERE (in the client) and only the neutral bool crosses to core/acquire.py.
        A malformed/absent flag reads as False (keep polling until the window deadline — T-04-06).
        """
        return bool(self.search_state(search_id).get("isComplete"))

    def search_responses(self, search_id: str) -> list:
        """GET /searches/{id}/responses; return the list of per-peer response items.

        A non-list body (malformed/unexpected) degrades to [] (T-04-06) so one bad response can
        never crash the collection window in 04-04.
        """
        r = self._client.get(
            f"{self._base}/searches/{search_id}/responses",
            headers=self._headers,
            timeout=30.0,
        )
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else []

    # --- transfers (enqueue / watch / cancel) ---------------------------------------------------

    def enqueue(self, username: str, files: list) -> None:
        """POST /transfers/downloads/{username} with the files list as the body (enqueue download).

        `files` is the list of {filename, size} dicts the caller selected (the gate-chosen
        candidate's files). slskd is primary: a non-2xx surfaces via raise_for_status.
        """
        r = self._client.post(
            f"{self._base}/transfers/downloads/{username}",
            headers=self._headers,
            json=files,
            timeout=30.0,
        )
        r.raise_for_status()

    def transfer(self, username: str, transfer_id: str) -> dict:
        """GET /transfers/downloads/{username}/{id}; return the transfer dict.

        `state` + `bytesTransferred` are read by the caller via .get() (absent → None, never a
        KeyError) so the 04-04 stall watch interprets progress defensively (T-04-06).
        """
        r = self._client.get(
            f"{self._base}/transfers/downloads/{username}/{transfer_id}",
            headers=self._headers,
            timeout=30.0,
        )
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, dict) else {}

    def enqueue_candidate(self, candidate) -> "TransferHandle":
        """NEUTRAL enqueue seam: take a whole neutral Candidate and enqueue its files as a download.

        The uploader identity (Candidate.username — a SELECTOR-ONLY field) and the slskd wire keys
        (`filename`, `size`) are read HERE, in the client, NEVER in core/acquire.py: the loop hands
        across the chosen Candidate and gets back an OPAQUE TransferHandle to watch/cancel, so the
        firewall (and the matching!=selection grep) holds over acquire.py. Returns the handle that
        addresses this transfer for transfer_progress()/cancel().
        """
        files = candidate.audio_files() or candidate.files
        body = [{"filename": f.filename, "size": f.size_bytes} for f in files]
        self.enqueue(candidate.username, body)
        # A2: resolve the dir slskd will actually land the files in — the leaf of the peer's remote
        # folder. Prefer the candidate's folder; if absent, derive it from a file's directory portion
        # (slskd filenames use `\` separators). The handle carries this NEUTRAL dir name so acquire
        # can point the import + purge at the real landing folder without reading any wire key.
        leaf = _remote_folder_leaf(candidate.folder)
        if not leaf and files:
            first = files[0].filename or ""
            parent = first.replace("\\", "/").rstrip("/").rsplit("/", 1)
            leaf = _remote_folder_leaf(parent[0]) if len(parent) > 1 else ""
        # slskd keys downloads by username; the handle carries it opaquely for the watch/cancel calls.
        return TransferHandle(
            username=candidate.username,
            transfer_id=candidate.username,
            landing_dir_name=leaf,
        )

    def transfer_progress(self, handle: "TransferHandle"):
        """NEUTRAL progress seam over transfer(): return acquire.TransferProgress(terminal, bytes_done).

        Takes the opaque TransferHandle (so acquire never names a username) and interprets the slskd
        wire keys (`state`, `bytesTransferred`) + the A3 terminal-state substrings HERE, handing core
        only the neutral shape:
          terminal   : "success" | "failure" | None (still in progress)
          bytes_done : the monotonically-non-decreasing byte counter the stall watch diffs

        TERMINAL_SUCCESS/FAILURE_SUBSTRINGS are the module-level named constants 04-05 re-pins live.
        An absent counter reads as 0 (no progress) — never a KeyError (T-04-06).
        """
        from core.acquire import TransferProgress

        t = self.transfer(handle.username, handle.transfer_id)
        state = t.get("state") or ""
        bytes_done = t.get("bytesTransferred") or 0
        # A3 robust rule: a transfer is TERMINAL only once its state contains "Completed"; then the
        # completion half disambiguates success vs failure. "Succeeded" => success; any other
        # terminal ("Completed, Errored"/"Cancelled"/...) => failure. Anything without "Completed"
        # (e.g. "InProgress", "Queued", "Initializing") is still running -> terminal None.
        terminal = None
        if TERMINAL_PHASE_SUBSTRING in state:
            if any(s in state for s in TERMINAL_SUCCESS_SUBSTRINGS):
                terminal = "success"
            else:
                # Any terminal completion that is NOT a success is a failure (fall to next candidate),
                # whether or not it matches a named failure substring (the family is open-ended).
                terminal = "failure"
        return TransferProgress(terminal=terminal, bytes_done=bytes_done)

    def cancel_transfer(self, handle: "TransferHandle", remove: bool = True) -> None:
        """NEUTRAL cancel seam: cancel the transfer addressed by the opaque handle (remove=True drops
        the partial from slskd's own list). acquire calls THIS (never naming a username)."""
        self.cancel(handle.username, handle.transfer_id, remove=remove)

    def cancel(self, username: str, transfer_id: str, remove: bool = True) -> None:
        """DELETE /transfers/downloads/{username}/{id}?remove={bool}; cancel (and optionally remove).

        remove=true tells slskd to also drop the partial from its own download list; the caller
        (04-04) owns the staging-dir purge separately via core.staging.
        """
        r = self._client.delete(
            f"{self._base}/transfers/downloads/{username}/{transfer_id}",
            headers=self._headers,
            params={"remove": "true" if remove else "false"},
            timeout=30.0,
        )
        r.raise_for_status()
