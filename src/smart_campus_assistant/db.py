"""SQLite storage for accounts, sessions, password reset codes and self-registered students.

The database file is ``settings.db_file`` (default data/app.db, env ``SCA_DB_FILE``,
git-ignored). Every public function takes an optional ``path`` so tests can use a
temporary database.

Connections are short-lived (open, use, close) so Streamlit's threads never share
one. Writes are serialised with a module lock and run inside ``BEGIN IMMEDIATE``.

On the first ``init_db`` of an empty database the legacy JSON files are imported
once: ``users.json`` (password hashes are kept as-is, so old passwords keep
working) and ``sessions.json`` (unexpired sessions only).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from smart_campus_assistant.config import get_settings

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    email                TEXT PRIMARY KEY CHECK (email = lower(email)),
    role                 TEXT NOT NULL CHECK (role IN ('student', 'admin')),
    name                 TEXT NOT NULL DEFAULT '',
    student_id           TEXT UNIQUE,
    password_hash        TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE ON UPDATE CASCADE,
    role       TEXT NOT NULL,
    expires    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_email ON sessions(email);
CREATE TABLE IF NOT EXISTS password_resets (
    email      TEXT PRIMARY KEY REFERENCES users(email) ON DELETE CASCADE ON UPDATE CASCADE,
    code_hash  TEXT NOT NULL,
    expires    REAL NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS students (
    student_id  TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

# One lock for all writes in this process (SQLite allows one writer at a time anyway).
write_lock = threading.RLock()
_initialised: set[str] = set()


def now_iso() -> str:
    """UTC timestamp like 2026-11-03T10:15:00+00:00."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path(path: str | Path | None = None) -> Path:
    """The database file: ``path`` if given, else ``settings.db_file``."""
    return Path(path) if path else get_settings().db_file


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection (WAL, foreign keys, Row factory); creates the schema on first use.

    The connection is in autocommit mode; use ``transaction()`` for writes. Close it when done.
    """
    p = db_path(path)
    if str(p.resolve()) not in _initialised or not p.exists():
        init_db(p)
    return _open(p)


@contextmanager
def reading(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Connection for reads; closed afterwards."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Locked write transaction (BEGIN IMMEDIATE); commits on success, rolls back on error."""
    with write_lock:
        conn = connect(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()


# ---------------------------------------------------------------- init + legacy import


def _legacy_files(p: Path) -> tuple[Path, Path]:
    """Legacy JSON files: the configured users file for the app database, else siblings of ``p``."""
    settings = get_settings()
    try:
        is_app_db = p.resolve() == Path(settings.db_file).resolve()
    except OSError:
        is_app_db = False
    users_json = Path(settings.users_file) if is_app_db else p.with_name("users.json")
    return users_json, users_json.with_name("sessions.json")


def _import_legacy(conn: sqlite3.Connection, users_json: Path, sessions_json: Path) -> tuple[int, int]:
    users = sessions = 0
    try:
        data = json.loads(users_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    rows = data.get("users", []) if isinstance(data, dict) else []
    stamp = now_iso()
    for row in rows:
        if not isinstance(row, dict) or not row.get("email") or not row.get("password_hash"):
            continue
        role = row.get("role")
        if role not in ("student", "admin"):
            continue
        sid = row.get("student_id") if role == "student" else None
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (email, role, name, student_id, password_hash, must_change_password,"
            " created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (str(row["email"]).strip().lower(), role, row.get("name", ""),
             str(sid).strip().upper() if sid else None, row["password_hash"], stamp),
        )
        users += cur.rowcount
    try:
        sdata = json.loads(sessions_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sdata = {}
    now = time.time()
    for token_hash, s in (sdata.get("sessions", {}) if isinstance(sdata, dict) else {}).items():
        if not isinstance(s, dict) or not isinstance(s.get("expires"), (int, float)) or s["expires"] <= now:
            continue
        email = str(s.get("email", "")).strip().lower()
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone() is None:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO sessions (token_hash, email, role, expires) VALUES (?, ?, ?, ?)",
            (token_hash, email, s.get("role", ""), float(s["expires"])),
        )
        sessions += cur.rowcount
    return users, sessions


def init_db(
    path: str | Path | None = None,
    users_json: str | Path | None = None,
    sessions_json: str | Path | None = None,
) -> dict:
    """Create missing tables (idempotent) and import legacy JSON once into an empty database.

    Returns {"path", "schema_version", "imported_users", "imported_sessions", "users"}.
    """
    p = db_path(path)
    default_users, default_sessions = _legacy_files(p)
    users_json = Path(users_json) if users_json else default_users
    sessions_json = Path(sessions_json) if sessions_json else default_sessions
    imported = (0, 0)
    with write_lock:
        conn = _open(p)
        try:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass  # some file systems do not support WAL; the default journal still works
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, now_iso()),
                )
                done = conn.execute("SELECT 1 FROM meta WHERE key = 'legacy_import_done'").fetchone()
                empty = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
                if not done and empty:
                    imported = _import_legacy(conn, users_json, sessions_json)
                if not done:
                    conn.execute(
                        "INSERT INTO meta (key, value) VALUES ('legacy_import_done', ?)", (now_iso(),)
                    )
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        _initialised.add(str(p.resolve()))
    return {
        "path": str(p),
        "schema_version": version,
        "imported_users": imported[0],
        "imported_sessions": imported[1],
        "users": total,
    }
