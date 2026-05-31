"""Phase-4 staging-lifecycle + path-safety coverage (ACQ-02, IMPORT-01, IMPORT-05; T-04-04/05).

core/staging.py is the pure-filesystem half of Phase 4: it computes per-item staging paths inside
the shared /data tree and runs the purge/quarantine/TTL lifecycle — every destructive op gated by
a path-traversal + shallow-root guard so a malicious peer filename (../../etc/x) or a mis-set
quarantine root (/, /data, /data/media) can NEVER move or rmtree anything outside the configured
root (the #1 catastrophic-failure threat in the Security Domain).

All filesystem-isolated via pytest's built-in tmp_path; the hardlink samefile test proves the
IMPORT-01 path-identity guarantee (a Move within one filesystem is an atomic hardlink).
"""
import os
import time

import pytest

from core import staging


# --- staging_path: pure computation, never creates ---------------------------------------------

def test_staging_path_computes_under_root_and_does_not_create(tmp_path):
    """staging_path(root, batch_id) returns root/{batch_id} as a Path and does NOT mkdir it."""
    p = staging.staging_path(str(tmp_path), "item-42")
    assert p == tmp_path / "item-42"
    assert not p.exists()                      # creation is slskd's job via batchId routing


# --- assert_under_root: the traversal + shallow-root guard --------------------------------------

def test_assert_under_root_allows_path_strictly_under_root(tmp_path):
    """A path genuinely under root returns normally (no exception)."""
    staging.assert_under_root(tmp_path / "sub" / "file.flac", tmp_path)


def test_assert_under_root_rejects_dotdot_escape(tmp_path):
    """A ../ escape out of root raises ValueError (resolve() before the parents check)."""
    root = tmp_path / "staging"
    root.mkdir()
    with pytest.raises(ValueError):
        staging.assert_under_root(root / ".." / "escape", root)


def test_assert_under_root_rejects_symlink_escape(tmp_path):
    """A symlink whose target is outside root must be refused (resolve() follows the link)."""
    root = tmp_path / "staging"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "sneaky"
    os.symlink(outside, link)                  # link inside root -> dir outside root
    with pytest.raises(ValueError):
        staging.assert_under_root(link / "loot", root)


def test_assert_under_root_rejects_root_equal_to_target(tmp_path):
    """The target must be STRICTLY under root — root itself is not 'under' root (no self-purge)."""
    root = tmp_path / "staging"
    root.mkdir()
    with pytest.raises(ValueError):
        staging.assert_under_root(root, root)


@pytest.mark.parametrize("dangerous", ["/", "/data", "/data/media"])
def test_assert_under_root_refuses_shallow_dangerous_root(tmp_path, dangerous):
    """A shallow/dangerous ROOT (/, /data, /data/media) is refused outright (T-04-05)."""
    # Even a 'valid' child under such a root must raise, because the ROOT itself is forbidden.
    with pytest.raises(ValueError):
        staging.assert_under_root(os.path.join(dangerous, "anything"), dangerous)


# --- purge_staging: gated rmtree ----------------------------------------------------------------

def test_purge_staging_removes_dir_strictly_under_root(tmp_path):
    """purge_staging rmtrees a dir strictly under root (D-05 verified-import cleanup)."""
    root = tmp_path
    staging_dir = root / "item-7"
    (staging_dir / "nested").mkdir(parents=True)
    (staging_dir / "nested" / "a.flac").write_text("x")
    staging.purge_staging(staging_dir, root)
    assert not staging_dir.exists()


def test_purge_staging_refuses_target_outside_root_and_removes_nothing(tmp_path):
    """A target outside root raises and removes nothing (the escape never deletes)."""
    root = tmp_path / "staging"
    root.mkdir()
    victim = tmp_path / "library"               # sibling of root, NOT under it
    victim.mkdir()
    (victim / "precious.flac").write_text("keep me")
    with pytest.raises(ValueError):
        staging.purge_staging(victim, root)
    assert victim.exists()                      # nothing was removed
    assert (victim / "precious.flac").exists()


def test_purge_staging_refuses_shallow_root(tmp_path):
    """purge_staging with root '/data' (shallow/dangerous) refuses — a mis-set root can't wipe."""
    with pytest.raises(ValueError):
        staging.purge_staging("/data/media/music/Artist", "/data")


# --- quarantine_staging: gated move -------------------------------------------------------------

def test_quarantine_staging_moves_dir_and_returns_new_path(tmp_path):
    """quarantine_staging moves the dir under quarantine_root/{label}-{ts}; source gone, dest present."""
    staging_dir = tmp_path / "staging" / "item-3"
    staging_dir.mkdir(parents=True)
    (staging_dir / "partial.flac").write_text("oops")
    qroot = tmp_path / "quarantine"
    qroot.mkdir()

    dest = staging.quarantine_staging(staging_dir, qroot, "lidarr-99")

    assert not staging_dir.exists()             # source moved away
    assert dest.exists()                        # dest present
    assert dest.parent == qroot
    assert dest.name.startswith("lidarr-99-")   # label-{timestamp}
    assert (dest / "partial.flac").read_text() == "oops"


def test_quarantine_staging_refuses_shallow_quarantine_root(tmp_path):
    """A shallow quarantine_root (/data) is refused — the move can't escape into the library."""
    staging_dir = tmp_path / "item"
    staging_dir.mkdir()
    with pytest.raises(ValueError):
        staging.quarantine_staging(staging_dir, "/data", "x")
    assert staging_dir.exists()                 # nothing moved


# --- purge_expired_quarantine: TTL selective purge ----------------------------------------------

def test_purge_expired_quarantine_removes_only_old_dirs(tmp_path):
    """Only quarantine subdirs older than ttl are purged; fresh ones survive; count returned."""
    qroot = tmp_path / "quarantine"
    qroot.mkdir()
    old1 = qroot / "lidarr-1-old"
    old2 = qroot / "lidarr-2-old"
    fresh = qroot / "lidarr-3-fresh"
    for d in (old1, old2, fresh):
        d.mkdir()
        (d / "f").write_text("x")
    # Age the two old dirs well beyond the TTL via st_mtime (wall-clock, what the impl compares).
    ancient = time.time() - 10_000
    os.utime(old1, (ancient, ancient))
    os.utime(old2, (ancient, ancient))

    purged = staging.purge_expired_quarantine(qroot, ttl_seconds=3600)

    assert purged == 2
    assert not old1.exists()
    assert not old2.exists()
    assert fresh.exists()                       # within TTL -> survives


def test_purge_expired_quarantine_empty_root_returns_zero(tmp_path):
    """An empty quarantine root purges nothing and returns 0 (no crash on no subdirs)."""
    qroot = tmp_path / "quarantine"
    qroot.mkdir()
    assert staging.purge_expired_quarantine(qroot, ttl_seconds=1) == 0


def test_purge_expired_quarantine_refuses_shallow_root():
    """A shallow quarantine_root (/data/media) is refused — TTL purge can't sweep the library."""
    with pytest.raises(ValueError):
        staging.purge_expired_quarantine("/data/media", ttl_seconds=1)


# --- IMPORT-01: hardlink path-identity proof ----------------------------------------------------

def test_hardlink_within_one_filesystem_is_samefile(tmp_path):
    """IMPORT-01: a file hardlinked from staging to dest satisfies os.path.samefile — proving a
    Move within ONE filesystem is an atomic hardlink (identical /data path, the #1 import-fix)."""
    staging_dir = tmp_path / "staging"
    dest_dir = tmp_path / "dest"
    staging_dir.mkdir()
    dest_dir.mkdir()
    src = staging_dir / "track.flac"
    src.write_bytes(b"FLACDATA")
    dst = dest_dir / "track.flac"
    os.link(src, dst)                           # hardlink (the *arr Manual-Import-Move primitive)
    assert os.path.samefile(src, dst)           # same inode -> atomic, zero-copy, identical bytes
