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

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a Settings by reading the environment NOW (not at import time).

        Each call snapshots the current env, so the captured values track the process
        environment at construction rather than at module-import — the WR-01 fix.
        """
        return cls(
            lidarr_url=os.getenv("LIDARR_URL", "http://lidarr:8686"),
            lidarr_api_key=os.getenv("LIDARR_API_KEY"),
            readarr_url=os.getenv("READARR_URL", "http://readarr:8787"),
            readarr_api_key=os.getenv("READARR_API_KEY"),
            db_path=os.getenv("DB_PATH", "/db/curator.sqlite"),
        )


# Module-level singleton — mirrors how main.py defines `app` / `DATA` after imports.
# Built from the env at import time via from_env(); tests rebuild with Settings.from_env()
# after monkeypatching the environment.
settings = Settings.from_env()
