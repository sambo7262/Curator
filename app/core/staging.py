# Curator staging lifecycle — the pure-filesystem half of Phase 4 (ACQ-02, IMPORT-01, IMPORT-05).
#
# This is a CORE module: it sits behind the *arr-agnostic firewall and therefore speaks ONLY
# filesystem paths — no *arr/slskd wire vocabulary, no httpx, stdlib only (pathlib/shutil/os/time).
# Its single concern is per-item isolation inside the shared /data tree: compute a staging path,
# and run the purge (verified-import, D-05) / quarantine (terminal failure, D-06) / TTL-sweep
# (D-06) lifecycle.
#
# SECURITY (the load-bearing reason this module exists as a guarded unit — Security Domain V12):
# every destructive operation (rmtree/move) is gated by assert_under_root FIRST. Two threats:
#   T-04-04  a malicious peer names a file '../../etc/x' to escape the staging root -> resolve()
#            the path and require the root to be a strict parent before touching anything.
#   T-04-05  a mis-set quarantine_root of '/', '/data', or '/data/media' would let a purge wipe
#            the library -> refuse such shallow/dangerous roots outright, independent of the target.
# If assert_under_root raises, NOTHING is moved or removed — the guard runs before the side effect.
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Roots we refuse to operate under, no matter what. A purge/move/TTL-sweep targeting (or rooted at)
# any of these — or any root with too few path components — could destroy the clean library, so the
# guard rejects them before any rmtree/move can run (T-04-05).
_FORBIDDEN_ROOTS = {Path("/"), Path("/data"), Path("/data/media")}
# A root must have at least this many path parts beyond the filesystem anchor. Path("/data/media")
# resolves to parts ('/', 'data', 'media') -> 2 non-anchor parts; require >= 3 so the configured
# staging/quarantine roots are always a real per-app subtree (e.g. /data/downloads/soulseek), never
# a top-level mount that a mistake could turn into a library-wiping target.
_MIN_ROOT_PARTS = 3


def staging_path(downloads_root: str, batch_id: str) -> Path:
    """Compute the per-item staging dir: downloads_root/{batch_id}. NEVER creates it.

    Directory creation is slskd's job (it materializes the dir when routing the download via the
    batchId); Curator only needs the deterministic path to watch + later purge/quarantine.
    """
    return Path(downloads_root) / batch_id


def assert_under_root(path, root) -> None:
    """Path-traversal + shallow-root guard. Raises ValueError unless `path` is STRICTLY under a
    safe, sufficiently-deep `root`.

    Two independent checks, both must pass:
      1. `root` itself must be safe: its resolved form is not /, /data, or /data/media, and has at
         least _MIN_ROOT_PARTS components (a mis-set root can never be a top-level mount). (T-04-05)
      2. `path` resolved must have `root` (resolved) among its parents — i.e. path is strictly
         inside root. resolve() is applied FIRST so a '../' or symlink escape is normalized away
         before the parents test, defeating peer-supplied traversal. (T-04-04)

    Both `path` and `root` may be str or Path. resolve(strict=False) is used so a not-yet-created
    staging/quarantine target still resolves to an absolute, normalized path for the check.
    """
    root_resolved = Path(root).resolve()
    # Check 1: the root must not be a forbidden/shallow path.
    if root_resolved in _FORBIDDEN_ROOTS or len(root_resolved.parts) < (_MIN_ROOT_PARTS + 1):
        # parts of '/data/media' == ('/', 'data', 'media') -> len 3 == _MIN_ROOT_PARTS+1; reject.
        raise ValueError(
            f"refusing to operate under shallow/dangerous root: {root_resolved!s}"
        )
    # Check 2: the target must be strictly inside the (resolved) root.
    path_resolved = Path(path).resolve()
    if root_resolved not in path_resolved.parents:
        raise ValueError(
            f"path escapes root: {path_resolved!s} is not strictly under {root_resolved!s}"
        )


def purge_staging(staging_dir, root) -> None:
    """D-05: rm -rf the staging dir after a VERIFIED import. Guarded: assert_under_root FIRST.

    ignore_errors=True so a partially-removed/locked file can't leave the loop wedged — the dir is
    being discarded anyway. The guard guarantees we only ever rmtree strictly inside `root`.
    """
    assert_under_root(staging_dir, root)
    shutil.rmtree(staging_dir, ignore_errors=True)


def quarantine_staging(staging_dir, quarantine_root, label: str) -> Path:
    """D-06: move a failed staging dir into quarantine_root/{label}-{timestamp}; return the new path.

    The destination (not the source) is what could escape the quarantine root, so the guard runs on
    `dest`. A wall-clock int timestamp keeps the dest name unique-per-second and lets the TTL sweep
    read st_mtime later. shutil.move within /data is an atomic rename (same filesystem).
    """
    dest = Path(quarantine_root) / f"{label}-{int(time.time())}"
    assert_under_root(dest, quarantine_root)
    shutil.move(str(staging_dir), str(dest))
    return dest


def purge_expired_quarantine(quarantine_root, ttl_seconds: float) -> int:
    """D-06: rmtree quarantine subdirs whose st_mtime is older than now-ttl_seconds; return count.

    Wall-clock (time.time vs st_mtime) is correct here: st_mtime is itself wall-clock, and the TTL
    is a human-meaningful retention window (default 7 days), not an interval measurement. Each
    candidate subdir is assert_under_root-guarded before its rmtree, so even a corrupt listing can
    never delete outside the quarantine root. A missing quarantine root yields 0 (nothing to sweep).
    """
    qroot = Path(quarantine_root)
    # The guard's shallow-root check must apply to the sweep too: refuse to sweep /data/media etc.
    # by running assert_under_root on a notional child (which validates the root half of the guard).
    assert_under_root(qroot / "__ttl_probe__", qroot)

    if not qroot.is_dir():
        return 0

    cutoff = time.time() - ttl_seconds
    purged = 0
    for child in qroot.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            assert_under_root(child, qroot)
            shutil.rmtree(child, ignore_errors=True)
            purged += 1
    return purged
