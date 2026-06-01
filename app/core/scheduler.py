# Curator scheduler — the daemon at the heart of Phase 5 (REL-01 / GAP-03 / STATE-03 write side).
# A stdlib daemon thread polls on the configured interval and runs ONE self-contained cycle each tick:
#   batched detect -> ensure_shares -> eligibility select -> per-item queue check -> bounded dispatch
#   -> apply_result (attempt / backoff / permanently-unavailable).
# It composes the Wave-0/1 surface (repo eligibility+backoff, core.shares.ensure_shares,
# adapter.get_queue_status, core.acquire.acquire_item + INFRA_EXC) into one running loop while
# preserving the single-writer model under bounded parallelism.
#
# Firewall (PITFALL #6): this module is CORE — it speaks ONLY neutral shapes (the repo DAOs, a neutral
# bool from get_queue_status, the neutral acquire_item outcome strings, ensure_shares' bool). It carries
# ZERO *arr/slskd wire vocabulary. The neutral log identity (app:id) copies acquire._identity.
#
# Locked decisions honored:
#   D-04  MAX_CONCURRENT bounds simultaneous acquisitions (ThreadPoolExecutor(max_workers=...)).
#   D-05  ACQ_ENABLED is the kill-switch (re-read each cycle); ACQ_DRY_RUN runs no side effects.
#   D-02  the per-item queue check (active Usenet grab) short-circuits BEFORE acquire (no burn).
#   D-15  detection is the batched one-txn detect_gaps (Task 1).
#   D-16  ONE sqlite connection; every write serialized through the shared writer lock (LockedConn /
#         the `lock`). A worker NEVER opens a second connection (sqlite3 forbids concurrent use).
#   REL-02 an INFRA_EXC anywhere in run_one -> infra-skip -> apply_result writes nothing (no burn).
#   Pitfall 1 the ~1493-gap backlog is flood-controlled by the per-cycle LIMIT (room) + MAX_CONCURRENT.
#   Pitfall 5 a cycle exception is logged and the loop CONTINUES (the daemon never dies).
import datetime as _dt
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List

from adapters.base import GapItem
from core.acquire import INFRA_EXC, acquire_item  # the SINGLE infra classifier (05-02) + the loop (04)
from core.gap_detector import build_adapters, detect_gaps
from core.shares import ensure_shares
from state import repo

log = logging.getLogger(__name__)

# Drain model (owner UX 2026-06: "just search continuously until everything left is stuck, then stall
# out"): a cycle pulls the WHOLE eligible set and works it to exhaustion, NOT a fixed slice. The flood
# control is no longer a LIMIT — it is the bounded executor (MAX_CONCURRENT) plus the natural per-item
# search-collection window (~12s), which together pace slskd gently while the backlog drains. DRAIN_ROOM
# is just a sanity ceiling on a single SELECT (far above any realistic ledger) so the query is bounded.
DRAIN_ROOM = 100_000


def _identity(item: GapItem) -> str:
    """Neutral log identity — app:id ONLY (no keys/tokens; copies acquire._identity)."""
    return f"{item.arr_app}:{item.arr_id}"


def _now_dt() -> _dt.datetime:
    """UTC now as an aware datetime — the single wall-clock source for the cycle cutoffs."""
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    """Format an aware datetime as the ledger's ISO-8601 'Z' string (matches repo._now_iso)."""
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LockedConn:
    """A thin writer-lock proxy over the single sqlite connection (Shape B, D-16).

    Wrapping conn.execute under the shared lock means acquire_item's existing `conn.execute(...)`
    calls are serialized for free when a worker is handed a LockedConn instead of the raw connection
    — there is never a second sqlite connection, and two workers can never touch the one connection
    concurrently (sqlite3 forbids it). Read or write, every execute goes through the lock."""

    def __init__(self, conn, lock: threading.Lock):
        self._conn = conn
        self._lock = lock

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        # Delegate any other attribute (row_factory, etc.) to the wrapped connection.
        return getattr(self._conn, name)


def run_one(item: GapItem, adapter, slskd, conn, settings) -> str:
    """Resolve ONE eligible item to a neutral outcome string. Returns one of:
      "imported" | "partial" | "quarantined" | "stuck"   (a genuine acquire_item verdict)
      "skip-usenet-active"                    (an active/queued Usenet grab — D-02, no burn)
      "infra-skip"                            (an INFRA_EXC on the queue check OR during acquire — no burn)
      "dry-run"                               (ACQ_DRY_RUN — search/gate/log only, zero side effects, D-05)

    The queue check runs FIRST: a truthy get_queue_status means Usenet is already handling this item,
    so Curator yields (fallback-only) and burns no attempt. An INFRA_EXC on EITHER the queue check or
    the acquire flow classifies as infra-skip (the world is unreachable — never push an available item
    toward permanently-unavailable on a VPN flap, REL-02). ACQ_DRY_RUN short-circuits to a log-only
    path that does NOT call the side-effecting acquire flow (no download/import/status/attempt write)."""
    # D-02 Usenet-race check FIRST — an INFRA_EXC here is infra-skip (no burn), not a genuine skip.
    try:
        if adapter.get_queue_status(item):
            log.info("%s: active Usenet grab -> skip (fallback-only, no burn)", _identity(item))
            return "skip-usenet-active"
    except INFRA_EXC:
        log.info("%s: infra fault on queue check -> infra-skip (no burn)", _identity(item))
        return "infra-skip"

    # D-05 dry-run: log the would-be acquisition and return WITHOUT any side effect.
    if settings.acq_dry_run:
        log.info("%s: DRY-RUN (would acquire; no download/import/write)", _identity(item))
        return "dry-run"

    # Genuine acquisition. An INFRA_EXC mid-acquire is infra-skip (no burn); any other outcome is the
    # acquire verdict (imported/quarantined/stuck) which apply_result then persists.
    try:
        return acquire_item(item, adapter, slskd, conn, settings)
    except INFRA_EXC:
        log.info("%s: infra fault during acquire -> infra-skip (no burn)", _identity(item))
        return "infra-skip"


def apply_result(conn, lock: threading.Lock, item: GapItem, outcome: str, settings) -> None:
    """Persist the outcome of a run_one (the STATE-03 write side). All writes serialized on `lock`.

      "imported"                       -> set_status('imported'); attempt_count reset to 0.
      "partial"                        -> attempt_count reset to 0; next_attempt_at = now + partial
                                          cooldown; status 'partial' (progress, not a failure — revisit
                                          later for the missing tracks, never permanently-unavailable).
      "stuck"                          -> a search/match MISS (0 candidates, gate-declined, or found-
                                          but-undownloadable). NEVER exiled (owner policy 2026-06:
                                          never permanently ignore a gap). attempt_count += 1 (for
                                          observability only), status stays 'stuck', next_attempt_at =
                                          now + acq_recheck_seconds — it rests, then the daemon re-sweeps
                                          it later (a fuller/again-online source or a matcher improvement
                                          may have appeared). Once the backlog is all-'stuck' the cycle
                                          finds nothing eligible and goes quiet until a recheck comes due.
      "quarantined"                    -> we DID download it but the *arr rejected the import (re-importing
                                          the same reject won't help). attempt_count += 1; if it reaches
                                          acq_max_attempts -> 'permanently-unavailable', next_attempt_at =
                                          now + dormant (30-day re-check); else status 'quarantined',
                                          next_attempt_at = now + backoff.
      "infra-skip" / "skip-usenet-active" / "dry-run" / "error-skip"
                                       -> NO write (item stays eligible next cycle).

    Note acquire_item ALREADY drove the status during its own run (searching/downloading/imported/
    quarantined/stuck) via the connection it was handed; apply_result owns the BACKOFF accounting
    (attempt_count + next_attempt_at) + the terminal transition that acquire_item does not know about.
    For "imported" we also reset attempt_count to 0 so a later re-detect starts clean.

    "error-skip" is an UNEXPECTED per-item fault contained in dispatch (e.g. a transient slskd 409 on
    a duplicate search) — it burns no attempt (the world may simply be busy; never push an item toward
    permanently-unavailable on a transient fault) and the item retries next cycle."""
    if outcome in ("infra-skip", "skip-usenet-active", "dry-run", "error-skip"):
        return  # no write — the item remains eligible for a later cycle (no burned attempt)

    now = _now_dt()
    if outcome == "imported":
        # acquire_item already set status='imported' on the same conn; reset the attempt counter.
        with lock:
            repo.record_attempt(conn, item.arr_app, item.arr_id, 0, None, "imported")
        return

    if outcome == "partial":
        # Partial album completion: real new tracks landed but the album is still incomplete. This is
        # PROGRESS, not a failure — reset the attempt counter (never march a progressing item toward
        # permanently-unavailable) and park it on the long partial cooldown so Curator revisits later
        # for the missing tracks instead of re-downloading the same partial every cycle. acquire_item
        # already set status='partial' on the same conn.
        next_at = _iso(now + _dt.timedelta(seconds=settings.acq_partial_cooldown_seconds))
        with lock:
            repo.record_attempt(conn, item.arr_app, item.arr_id, 0, next_at, "partial")
        log.info("%s: partial import -> revisit cooldown (next at %s)", _identity(item), next_at)
        return

    if outcome == "stuck":
        # Search/match MISS — never exiled. Rest on the recheck cooldown and stay 'stuck' (a
        # recheckable resting state, NOT permanently-unavailable). attempt_count is bumped for
        # observability only; it never trips a give-up. acquire_item already set status='stuck' on
        # the same conn. Once every remaining gap is 'stuck' the next select_eligible returns nothing
        # and the daemon goes quiet until a recheck comes due (the owner's expected "stalls out" UX).
        with lock:
            row = repo.get_gap(conn, item.arr_app, item.arr_id)
            prev = row["attempt_count"] if row is not None else 0
            attempt_count = prev + 1
            next_at = _iso(now + _dt.timedelta(seconds=settings.acq_recheck_seconds))
            repo.record_attempt(conn, item.arr_app, item.arr_id, attempt_count, next_at, "stuck")
        log.info("%s: stuck (attempt %d) -> recheck at %s (never exiled)",
                 _identity(item), attempt_count, next_at)
        return

    # Genuine import failure: quarantined. We downloaded files but the *arr rejected the import, so
    # re-importing the same reject won't help — bump the attempt counter and, at the cap, exile to a
    # 30-day dormant re-check (a new/better source may appear). This is the ONLY path that escalates.
    with lock:
        row = repo.get_gap(conn, item.arr_app, item.arr_id)
        prev = row["attempt_count"] if row is not None else 0
        attempt_count = prev + 1
        if attempt_count >= settings.acq_max_attempts:
            next_at = _iso(now + _dt.timedelta(seconds=settings.acq_dormant_seconds))
            status = "permanently-unavailable"
            log.info("%s: quarantine attempt %d >= max %d -> permanently-unavailable (dormant recheck in %ds)",
                     _identity(item), attempt_count, settings.acq_max_attempts, settings.acq_dormant_seconds)
        else:
            next_at = _iso(now + _dt.timedelta(seconds=repo.backoff_for(attempt_count)))
            status = "quarantined"
            log.info("%s: quarantine attempt %d -> backoff (retry at %s)",
                     _identity(item), attempt_count, next_at)
        repo.record_attempt(conn, item.arr_app, item.arr_id, attempt_count, next_at, status)


def dispatch(items: List[GapItem], by_app: Dict[str, Any], slskd, conn, lock: threading.Lock,
             settings) -> List[str]:
    """Run run_one over the eligible items in a ThreadPoolExecutor bounded at MAX_CONCURRENT (D-04 /
    Pitfall 1 hard cap), preserving input order in the returned outcome list. Each worker is handed a
    LockedConn so acquire_item's own conn.execute writes are serialized through the shared lock too
    (no second connection, no concurrent use of the one connection — D-16). An item whose arr_app has
    no adapter is skipped as infra-skip (no burn). Returns the per-item outcome strings (order-aligned).

    Per-item fault containment (Pitfall 5 at item granularity): run_one classifies the INFRA_EXC family
    itself, but ANY other unexpected exception from one item (e.g. a slskd 409 Conflict on a duplicate
    search — an HTTPStatusError, NOT infra) must NOT escape the executor and abort the whole cycle
    (which would skip apply_result for every other item). _work catches it, logs it, and maps it to a
    no-burn 'error-skip' so the remaining items in the pass still run and persist their outcomes."""
    locked = LockedConn(conn, lock)
    max_workers = max(int(settings.max_concurrent), 1)

    def _work(item: GapItem) -> str:
        adapter = by_app.get(item.arr_app)
        if adapter is None:
            log.warning("%s: no adapter for arr_app -> infra-skip (no burn)", _identity(item))
            return "infra-skip"
        try:
            return run_one(item, adapter, slskd, locked, settings)
        except Exception as e:  # noqa: BLE001 — one item's fault must never abort the whole cycle
            log.exception("%s: unexpected fault -> error-skip (no burn); cycle continues (%s)",
                          _identity(item), e)
            return "error-skip"

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        outcomes = list(pool.map(_work, items))
    return outcomes


def run_cycle(app, settings, first_pass: bool = False, lock: threading.Lock = None,
              should_stop: Callable[[], bool] = None) -> None:
    """Run ONE acquisition cycle on the app's single retained connection (app.state.db).

    Steps: (1) under the writer lock, batched detect_gaps (D-15); (2) ensure_shares (D-10 self-heal,
    never blocks acquisition); (3) under the writer lock, select_eligible(grace+backoff+dormant) for
    the WHOLE eligible set; (4) DRAIN it to exhaustion — dispatch in stop-aware batches of
    MAX_CONCURRENT (bounded parallelism is the flood control, the per-item search window paces slskd),
    apply_result per outcome. The drain continues until the backlog is worked or `should_stop()` is
    set (a clean shutdown interrupts between batches, ~one batch latency).

    The connection + the shared writer lock are read off `app.state` (the lock is main.py's
    _detect_lock, shared so a manual /detect and a cycle can never collide). Adapters/clients are
    built lazily and CLOSED in finally (CR-02). slskd clients likewise."""
    if should_stop is None:
        should_stop = lambda: False  # noqa: E731 — default: never interrupt (direct/test callers)
    conn = getattr(app.state, "db", None)
    if conn is None:
        log.warning("scheduler cycle: ledger connection not ready; skipping")
        return
    if lock is None:
        lock = getattr(app.state, "detect_lock", None) or threading.Lock()

    # 1. Batched detection (D-15) — serialize the whole pass on the writer lock (it shares the one conn
    #    with the manual /detect trigger). build_adapters owns httpx clients; close them in finally.
    adapters, clients = build_adapters()
    try:
        by_app = {a.app: a for a in adapters}
        with lock:
            counts = detect_gaps(adapters, conn)
        log.info("scheduler cycle: detected %s", counts)

        # 2. Shares self-heal (D-10) — surfaces a zero-share leech risk; never blocks acquisition.
        slskd = None
        slskd_clients: List[Any] = []
        try:
            from core.acquire import build_acquire_clients
            slskd, slskd_clients = build_acquire_clients(settings)
            try:
                ensure_shares(slskd, app.state)
            except INFRA_EXC:
                log.info("scheduler cycle: infra fault on ensure_shares; continuing")

            # 3. Eligibility select (grace + backoff + dormant) — serialize the read on the lock.
            now = _now_dt()
            grace_cutoff = _iso(now - _dt.timedelta(seconds=settings.acq_grace_seconds))
            dormant_cutoff = _iso(now)  # permanently-unavailable rows whose next_attempt_at <= now
            now_iso = _iso(now)
            with lock:
                eligible_rows = repo.select_eligible(
                    conn, grace_cutoff, now_iso, dormant_cutoff, DRAIN_ROOM
                )
            if not eligible_rows:
                log.info("scheduler cycle: no eligible items this pass")
                return
            eligible = [_gapitem_from_row(r) for r in eligible_rows]
            log.info("scheduler cycle: draining %d eligible item(s) (cap %d)",
                     len(eligible), settings.max_concurrent)

            # 4. DRAIN the whole eligible set to exhaustion in stop-aware batches of MAX_CONCURRENT.
            #    dispatch bounds each batch's parallelism to the cap (the flood control), and the
            #    per-item search-collection window paces slskd. should_stop() is checked between
            #    batches so a clean shutdown interrupts a long drain within ~one batch. 5. apply_result
            #    per outcome (all writes serialized on the lock via LockedConn / the explicit lock).
            batch_size = max(int(settings.max_concurrent), 1)
            done = 0
            for start in range(0, len(eligible), batch_size):
                if should_stop():
                    log.info("scheduler cycle: stop requested -> drain interrupted at %d/%d",
                             done, len(eligible))
                    break
                batch = eligible[start:start + batch_size]
                outcomes = dispatch(batch, by_app, slskd, conn, lock, settings)
                for item, outcome in zip(batch, outcomes):
                    apply_result(conn, lock, item, outcome, settings)
                done += len(batch)
        finally:
            for c in slskd_clients:
                c.close()
    finally:
        for c in clients:
            c.close()


def _gapitem_from_row(row) -> GapItem:
    """Map a neutral ledger row to a GapItem (reads only neutral ledger columns — no *arr keys)."""
    return GapItem(
        arr_app=row["arr_app"],
        arr_id=row["arr_id"],
        kind=row["kind"],
        gap_type=row["gap_type"],          # REQUIRED field — stored on every upsert (repo.upsert), read back here
        artist_or_author=row["artist_or_author"],
        title=row["title"],
        foreign_id=row["foreign_id"],
        quality_profile_id=row["quality_profile_id"],
    )


class Scheduler:
    """The Phase-5 daemon: a single daemon thread that runs a boot cycle once, then loops on the poll
    interval via an interruptible stop-event (no busy-wait). REL-01: it runs continuously; Pitfall 5:
    a cycle exception is logged and the loop CONTINUES (the daemon never dies).

    ACQ_ENABLED is the kill-switch, re-read EACH cycle via Settings.from_env() (A4) so toggling the
    env + restarting is not required to pause — a live env change is observed on the next tick. The
    interval + grace/backoff/concurrency tunables are read from the same fresh Settings each cycle, so
    an ops change to MAX_CONCURRENT / poll interval takes effect without a code change.

    The shared writer `lock` (main.py's _detect_lock) is injected so a manual /detect and a cycle can
    never write the single connection concurrently (D-16). Tests inject a tiny interval + a fast
    stop_event so the loop never waits hours."""

    def __init__(self, app, settings, lock: threading.Lock):
        self._app = app
        self._settings = settings  # the initial snapshot (interval seed); ACQ_ENABLED re-read per cycle
        self._lock = lock
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="curator-scheduler", daemon=True)

    def start(self) -> None:
        log.info("scheduler: starting daemon (interval=%ss)", self._settings.acq_poll_interval_seconds)
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Signal the loop to stop and join the thread (clean, interruptible shutdown)."""
        log.info("scheduler: stopping")
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Boot cycle once immediately, then loop on the interval until stop_event is set.
        first = True
        while True:
            self._tick(first_pass=first)
            first = False
            interval = self._current_settings().acq_poll_interval_seconds
            # stop_event.wait returns True when the event is set (clean shutdown) — interruptible sleep.
            if self._stop_event.wait(interval):
                break
        log.info("scheduler: loop exited cleanly")

    def _current_settings(self):
        """Re-read Settings from the environment each cycle (A4 kill-switch + live-tunable re-read).
        Falls back to the injected settings if from_env is unavailable (e.g. a test stub object)."""
        from_env = getattr(type(self._settings), "from_env", None)
        if callable(from_env):
            try:
                return from_env()
            except Exception as e:  # a bad env should not kill the loop — keep the last-known settings
                log.warning("scheduler: settings re-read failed (%s); using last-known", e)
        return self._settings

    def _tick(self, first_pass: bool) -> None:
        """Run one guarded cycle: re-read the kill-switch, then run_cycle. ANY exception is logged and
        swallowed so the loop continues (Pitfall 5 — the daemon never dies on a cycle fault)."""
        try:
            settings = self._current_settings()
            if not settings.acq_enabled:
                log.info("scheduler: ACQ_ENABLED is false -> skipping cycle (kill-switch)")
                return
            run_cycle(self._app, settings, first_pass=first_pass, lock=self._lock,
                      should_stop=self._stop_event.is_set)
        except Exception as e:  # noqa: BLE001 — a cycle exception must NEVER kill the daemon (REL-01)
            log.exception("scheduler: cycle raised (%s); loop continues", e)
