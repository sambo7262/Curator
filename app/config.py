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
    """

    lidarr_url: str = os.getenv("LIDARR_URL", "http://lidarr:8686")
    lidarr_api_key: Optional[str] = os.getenv("LIDARR_API_KEY")
    readarr_url: str = os.getenv("READARR_URL", "http://readarr:8787")
    readarr_api_key: Optional[str] = os.getenv("READARR_API_KEY")
    db_path: str = os.getenv("DB_PATH", "/db/curator.sqlite")


# Module-level singleton — mirrors how main.py defines `app` / `DATA` after imports.
settings = Settings()
