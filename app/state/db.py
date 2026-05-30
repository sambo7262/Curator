# Curator state ledger — SQLite-WAL connection + idempotent versioned migrations.
# This is the single persistence boundary: connect() yields a WAL-mode connection tuned
# for the single-writer detection path, and run_migrations() reconciles the schema from
# the ordered MIGRATIONS list, gated on PRAGMA user_version so it is a no-op on an
# already-current DB (STATE-01, criterion 1 — a recreated container self-heals on boot).
#
# Security: the ONLY f-string-into-SQL permitted in this layer is `PRAGMA user_version = {i}`,
# where `i` is a loop-controlled integer (never user input). All data queries elsewhere
# (state/repo.py) use `?` placeholders. [T-02-03]
import sqlite3
from pathlib import Path
from typing import List, Tuple

# Migration 0001 ships as a sibling .sql file read relative to THIS module, so the DDL is
# editable as SQL and the same file is the source of truth for the schema.
_SCHEMA_0001 = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# Ordered list of (version-label, sql). The 1-based index IS the user_version a migration
# bumps to; append new tuples here for later phases — never reorder or mutate shipped ones.
MIGRATIONS: List[Tuple[str, str]] = [
    ("0001", _SCHEMA_0001),
]


def connect(db_path: str) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection tuned for the single-writer detection path.

    autocommit (isolation_level=None) + check_same_thread=False so the one writer
    connection can be used from FastAPI's threadpool; WAL gives concurrent readers.
    Row factory yields sqlite3.Row so callers read columns by name.
    """
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")      # concurrent readers + one writer
    conn.execute("PRAGMA synchronous=NORMAL;")    # safe with WAL; good durability/throughput
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")     # wait, don't instantly error, on contention
    conn.row_factory = sqlite3.Row
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any migration whose 1-based index exceeds the stored PRAGMA user_version.

    Idempotent: re-running on an already-current DB applies nothing and leaves
    user_version unchanged (STATE-01 self-healing schema reconcile).
    """
    have = conn.execute("PRAGMA user_version;").fetchone()[0]
    for i, (_, sql) in enumerate(MIGRATIONS, start=1):
        if i > have:
            conn.executescript(sql)
            # The ONLY permitted f-string-into-SQL: `i` is a loop-controlled int, never user input.
            conn.execute(f"PRAGMA user_version = {i};")
