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
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# --- Transfer terminal-state substrings (A3 — [ASSUMED], pending the 04-05 live probe) ----------
# slskd reports a transfer's lifecycle as a compound `state` string (e.g. "Completed, Succeeded").
# The neutral progress interpretation (terminal success vs failure vs in-progress) lives in
# acquire.py (04-04) — this client only EXPOSES the raw transfer dict. These named constants are
# defined HERE so 04-05 can re-pin the exact live strings in ONE place once probed on the NAS; the
# fixtures (slskd/transfer_*.json) already use these literals. Do NOT bury state parsing in the
# client beyond this module-level vocabulary.
STATE_COMPLETED_SUCCEEDED = "Completed, Succeeded"   # [ASSUMED A3] terminal success substring set
STATE_IN_PROGRESS = "InProgress"                     # [ASSUMED A3] still-transferring
STATE_FAILED = "Completed, Errored"                  # [ASSUMED A3] terminal failure substring set
# Substrings acquire.py (04-04) will test the live `state` against (re-pinned live by 04-05):
TERMINAL_SUCCESS_SUBSTRINGS = ("Succeeded",)
TERMINAL_FAILURE_SUBSTRINGS = ("Errored", "Failed", "Cancelled", "Rejected")


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
