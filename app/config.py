# Curator — one declarative place for runtime configuration (env → typed Settings).
# Consolidates the scattered os.getenv reads (PITFALLS #4) into a single frozen singleton.
# Security: *arr API keys are read from env ONLY — never logged, never baked into the image,
# never written to a fixture or committed file (.env is gitignored, Phase 1). [T-02-01]
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Settings:
    """Typed, immutable env surface for the *arr URLs/keys + the SQLite ledger path.

    Env var names match docker-compose.yml verbatim; DB_PATH is the only Phase-2 addition.
    The DB lives on its OWN /db mount (NOT under the shared /data tree) — see compose. [T-02-02]

    The defaults here are STATIC fallbacks; the env is read in from_env() at construction time
    (NOT baked in as field defaults at import time), so tests can monkeypatch.setenv then rebuild
    via Settings.from_env(), and any importer that loads `config` before the environment is fully
    populated no longer silently freezes stale defaults (WR-01).
    """

    lidarr_url: str = "http://lidarr:8686"
    lidarr_api_key: Optional[str] = None
    readarr_url: str = "http://readarr:8787"
    readarr_api_key: Optional[str] = None
    db_path: str = "/db/curator.sqlite"

    # Phase-3 matcher/gate tunables (MATCH-02, QUAL-03). Owner-tunable WITHOUT a rebuild —
    # defaults MUST equal the MatchConfig defaults (RESEARCH 207-215) so no-env behavior is
    # identical to the hard-coded scorer. gate.py reads these to build its MatchConfig.
    match_strong_thresh: float = 0.15      # accept iff best distance <= this (RESEARCH 323)
    match_rec_gap_thresh: float = 0.10     # decline-as-ambiguous unless 2nd-best is this far behind
    match_w_artist: float = 3.0            # per-sub-distance weights (RESEARCH 207-215)
    match_w_album: float = 3.0
    match_w_track_count: float = 4.0
    match_w_track_titles: float = 4.0
    fakeflac_min_kbps: int = 400           # bytes/sec FLAC authenticity floor (RESEARCH 356)

    # Phase-4 acquisition tunables (SP-4). All env-overridable via from_env(); no rebuild needed.
    # NOTE: NO Plex fields — revised D-04 (2026-05-31) drops the Curator->Plex call entirely;
    # IMPORT-04 is satisfied by the owner's existing "scan on new media" auto-scan (external
    # precondition), so no PLEX_URL/PLEX_TOKEN secret enters the stack. [T-04-02 secrets stay Optional]
    slskd_url: str = "http://localhost:5030"          # default; prod uses NAS IP via DEPLOY.md
    slskd_api_key: Optional[str] = None               # read from env only, never logged/baked
    acq_search_window_seconds: float = 12.0           # D-07 fixed collection window (Claude's discretion)
    acq_stall_seconds: float = 600.0                  # D-01 no-progress stall threshold (~10 min)
    acq_poll_seconds: float = 5.0                     # transfer poll interval
    staging_root: str = "/data/downloads/soulseek"    # MUST match slskd directories.downloads (D-12)
    quarantine_root: str = "/data/downloads/soulseek/.quarantine"
    quarantine_ttl_seconds: float = 604800.0          # D-06 quarantine TTL (~7 days)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a Settings by reading the environment NOW (not at import time).

        Each call snapshots the current env, so the captured values track the process
        environment at construction rather than at module-import — the WR-01 fix.

        The Phase-3 tunables are cast to float/int here; a non-numeric operator value
        fails fast with a clear ValueError at startup (T-03-12, accepted fail-fast) rather
        than silently mis-gating. Env names are the documented RESEARCH §3 strings.
        """
        return cls(
            lidarr_url=os.getenv("LIDARR_URL", "http://lidarr:8686"),
            lidarr_api_key=os.getenv("LIDARR_API_KEY"),
            readarr_url=os.getenv("READARR_URL", "http://readarr:8787"),
            readarr_api_key=os.getenv("READARR_API_KEY"),
            db_path=os.getenv("DB_PATH", "/db/curator.sqlite"),
            match_strong_thresh=float(os.getenv("MATCH_STRONG_THRESH", "0.15")),
            match_rec_gap_thresh=float(os.getenv("MATCH_REC_GAP_THRESH", "0.10")),
            match_w_artist=float(os.getenv("MATCH_W_ARTIST", "3.0")),
            match_w_album=float(os.getenv("MATCH_W_ALBUM", "3.0")),
            match_w_track_count=float(os.getenv("MATCH_W_TRACK_COUNT", "4.0")),
            match_w_track_titles=float(os.getenv("MATCH_W_TRACK_TITLES", "4.0")),
            fakeflac_min_kbps=int(os.getenv("FAKEFLAC_MIN_KBPS", "400")),
            # Phase-4 acquisition tunables — numerics cast float() so a bad operator value fails
            # fast at startup (Phase-3 precedent); keys/tokens stay Optional (never baked/logged).
            slskd_url=os.getenv("SLSKD_URL", "http://localhost:5030"),
            slskd_api_key=os.getenv("SLSKD_API_KEY"),
            acq_search_window_seconds=float(os.getenv("ACQ_SEARCH_WINDOW_SECONDS", "12.0")),
            acq_stall_seconds=float(os.getenv("ACQ_STALL_SECONDS", "600.0")),
            acq_poll_seconds=float(os.getenv("ACQ_POLL_SECONDS", "5.0")),
            staging_root=os.getenv("STAGING_ROOT", "/data/downloads/soulseek"),
            quarantine_root=os.getenv(
                "QUARANTINE_ROOT", "/data/downloads/soulseek/.quarantine"
            ),
            quarantine_ttl_seconds=float(os.getenv("QUARANTINE_TTL_SECONDS", "604800.0")),
        )


# Module-level singleton — mirrors how main.py defines `app` / `DATA` after imports.
# Built from the env at import time via from_env(); tests rebuild with Settings.from_env()
# after monkeypatching the environment.
settings = Settings.from_env()
