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
# Migration 0002 (Phase 4): widen the items.status CHECK enum + add the staged_files table.
_SCHEMA_0002 = (Path(__file__).parent / "migration_0002.sql").read_text(encoding="utf-8")
# Migration 0003 (Phase 5): add attempt_count/next_attempt_at/last_checked_at + the
# 'permanently-unavailable' status (backoff + dormant re-check, D-07/D-08/D-09).
_SCHEMA_0003 = (Path(__file__).parent / "migration_0003.sql").read_text(encoding="utf-8")

# Ordered list of (version-label, sql). The 1-based index IS the user_version a migration
# bumps to; append new tuples here for later phases — never reorder or mutate shipped ones.
MIGRATIONS: List[Tuple[str, str]] = [
    ("0001", _SCHEMA_0001),
    ("0002", _SCHEMA_0002),   # Phase 4: acquisition lifecycle states + staged_files table
    ("0003", _SCHEMA_0003),   # Phase 5: backoff/attempt/last-checked cols + permanently-unavailable
]


def connect(db_path: str) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection tuned for the single-writer detection path.

    autocommit (isolation_level=None) + check_same_thread=False so the one writer
    connection can be used from FastAPI's threadpool; WAL gives concurrent readers.
    Row factory yields sqlite3.Row so callers read columns by name.
    """
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")      # concurrent readers + one writer
    # synchronous=FULL (not NORMAL) for the writer: with WAL, NORMAL can LOSE the last committed
    # transaction(s) on an OS crash / power loss (DB stays consistent, but a just-written
    # status='imported' could vanish) — and a lost 'imported' would re-trigger acquisition,
    # breaking the load-bearing "no redundant downloads" guarantee. This is a low-volume homelab
    # gap-filler, so the extra fsync-per-commit cost is irrelevant; correctness wins (WR-06).
    conn.execute("PRAGMA synchronous=FULL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")     # wait, don't instantly error, on contention
    conn.row_factory = sqlite3.Row
    return conn


def _split_statements(sql: str) -> List[str]:
    """Split a migration .sql into individual statements on top-level `;`.

    Strips `--` line comments FIRST (a prose comment may itself contain a `;`, which would
    otherwise split mid-comment), then splits the remaining DDL on `;`. Sufficient for the
    Curator migration files, which use no string literals embedding a `;` — so this lets us
    run each statement inside ONE explicit transaction instead of executescript (which
    auto-commits and would defeat the wrapping BEGIN).
    """
    no_comments = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        no_comments.append(line)
    stripped = "\n".join(no_comments)
    return [stmt.strip() for stmt in stripped.split(";") if stmt.strip()]


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any migration whose 1-based index exceeds the stored PRAGMA user_version.

    Each migration's DDL AND its user_version bump are committed together in a single
    explicit transaction (WR-02): if the process dies mid-migration, SQLite rolls the
    whole thing back, so a partially-applied schema can never coexist with a stale
    user_version (which would re-run a non-idempotent future migration). We split the
    .sql and execute statements individually rather than executescript() because the
    latter issues an implicit COMMIT that would break the wrapping transaction.

    Idempotent: re-running on an already-current DB applies nothing and leaves
    user_version unchanged (STATE-01 self-healing schema reconcile).
    """
    have = conn.execute("PRAGMA user_version;").fetchone()[0]
    for i, (_, sql) in enumerate(MIGRATIONS, start=1):
        if i > have:
            conn.execute("BEGIN;")
            try:
                for stmt in _split_statements(sql):
                    conn.execute(stmt)
                # The ONLY permitted f-string-into-SQL: `i` is a loop-controlled int, never user input.
                conn.execute(f"PRAGMA user_version = {i};")
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
