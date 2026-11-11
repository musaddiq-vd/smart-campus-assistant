"""Login accounts: password hashing, SQLite storage, authentication, throttling, sessions,
self-registration, account claiming and password reset.

Accounts, sessions and reset codes live in the SQLite database ``settings.db_file``
(default data/app.db, env ``SCA_DB_FILE``, git-ignored; see ``db.py``). Passwords are
stored only as PBKDF2-HMAC-SHA256 hashes, session tokens and reset codes only as hashes.
The one plaintext exception is the git-ignored demo credentials CSV written by
``seed-demo`` for local demos.

Every function takes an optional ``path`` (the database file) so tests can use a
temporary database. For backwards compatibility the session functions also accept
``users_path``; both mean the database file now.

CLI: ``python -m smart_campus_assistant.auth --help``
"""

from __future__ import annotations

import argparse
import base64
import csv
import getpass
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Callable, Literal, TypedDict

from smart_campus_assistant import db, mailer, student_data
from smart_campus_assistant.config import BRANCHES, get_settings

log = logging.getLogger(__name__)

ALGORITHM = "pbkdf2_sha256"
# Tests lower this to keep runs fast; stored hashes carry their own count.
ITERATIONS = 600_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
ROLES = ("student", "admin")
ADMIN_EMAIL = "admin@xyz.edu.example"
ADMIN_NAME = "XYZ Admin Office"
CSV_COLUMNS = ("role", "email", "name", "student_id", "password")

PASSWORD_RULES = (
    f"At least {MIN_PASSWORD_LENGTH} characters",
    "At least one letter",
    "At least one digit",
)
STUDENT_CATEGORIES = ("General", "OBC-NCL", "EWS", "SC", "ST")
NAME_MIN, NAME_MAX = 2, 60
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CLAIM_MISMATCH = "Student ID and college email do not match our records"


class User(TypedDict):
    """Public view of an account (never contains the hash)."""

    email: str
    role: Literal["student", "admin"]
    name: str
    student_id: str | None
    must_change_password: bool


# ---------------------------------------------------------------- hashing


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${_b64(salt)}${_b64(digest)}"


def hash_password(password: str) -> str:
    """Return ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`` with a fresh salt.

    Only the minimum length is checked here; use ``validate_new_password`` for the full rules.
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return _hash_secret(password)


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against an encoded hash; False on bad input."""
    try:
        algorithm, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        rounds = int(iterations)
        if rounds < 1:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        if not salt or not expected:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


# Dummy hash per iteration count, used so unknown emails cost the same time.
_DUMMY_HASHES: dict[int, str] = {}


def _dummy_hash() -> str:
    if ITERATIONS not in _DUMMY_HASHES:
        _DUMMY_HASHES[ITERATIONS] = hash_password(secrets.token_urlsafe(16))
    return _DUMMY_HASHES[ITERATIONS]


def validate_new_password(password: object) -> list[str]:
    """Human-readable problems with a new password ([] means it is acceptable)."""
    if not isinstance(password, str):
        return [f"Password must be at least {MIN_PASSWORD_LENGTH} characters."]
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if not re.search(r"[^\W\d_]", password):
        problems.append("Password must contain at least one letter.")
    if not re.search(r"\d", password):
        problems.append("Password must contain at least one digit.")
    return problems


def _require_strong(password: object) -> None:
    problems = validate_new_password(password)
    if problems:
        raise ValueError(" ".join(problems))


# ---------------------------------------------------------------- account storage (SQLite)


def normalize_email(email: object) -> str:
    return str(email or "").strip().lower()


def valid_email(email: object) -> bool:
    email = normalize_email(email)
    return len(email) <= 254 and _EMAIL_RE.match(email) is not None


def _record(row: sqlite3.Row) -> dict:
    return {
        "email": row["email"],
        "role": row["role"],
        "name": row["name"],
        "student_id": row["student_id"],
        "password_hash": row["password_hash"],
        "must_change_password": bool(row["must_change_password"]),
    }


def _public(record: dict) -> User:
    return User(
        email=normalize_email(record["email"]),
        role=record["role"],
        name=record.get("name", ""),
        student_id=record.get("student_id"),
        must_change_password=bool(record.get("must_change_password", False)),
    )


def _get_record(email: str, path: str | Path | None = None) -> dict | None:
    with db.reading(path) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
    return _record(row) if row else None


def load_users(path: str | Path | None = None) -> dict[str, dict]:
    """Compatibility helper: {email_lower: record incl. password_hash} from the database."""
    with db.reading(path) as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY email").fetchall()
    return {row["email"]: _record(row) for row in rows}


def count_users(path: str | Path | None = None) -> int:
    """Number of accounts (one COUNT query; no password hashes leave the database)."""
    with db.reading(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def account_email_for_student(student_id: object, path: str | Path | None = None) -> str | None:
    """Sign-in email of the account that belongs to a student ID, or None if they have no account."""
    sid = student_data.normalize_id(student_id)
    if not sid:
        return None
    with db.reading(path) as conn:
        row = conn.execute("SELECT email FROM users WHERE student_id = ? AND role = 'student'", (sid,)).fetchone()
    return row["email"] if row else None


def save_users(records: dict[str, dict], path: str | Path | None = None) -> None:
    """Compatibility helper: make the users table equal to ``records`` (upsert; delete the rest).

    Deleting an account also deletes its sessions and reset codes.
    """
    with db.transaction(path) as conn:
        keep = []
        for _, r in sorted(records.items()):
            email = normalize_email(r["email"])
            keep.append(email)
            conn.execute(
                "INSERT INTO users (email, role, name, student_id, password_hash, must_change_password, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(email) DO UPDATE SET role = excluded.role, name = excluded.name,"
                " student_id = excluded.student_id, password_hash = excluded.password_hash,"
                " must_change_password = excluded.must_change_password",
                (email, r["role"], r.get("name", ""), r.get("student_id"), r["password_hash"],
                 int(bool(r.get("must_change_password", False))), db.now_iso()),
            )
        marks = ",".join("?" * len(keep))
        conn.execute(f"DELETE FROM users WHERE email NOT IN ({marks})" if keep else "DELETE FROM users", keep)


def _insert_user(conn: sqlite3.Connection, email: str, role: str, name: str, student_id: str | None,
                 password_hash: str, must_change: bool = False) -> None:
    conn.execute(
        "INSERT INTO users (email, role, name, student_id, password_hash, must_change_password, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (email, role, name, student_id, password_hash, int(must_change), db.now_iso()),
    )


# ---------------------------------------------------------------- login


def authenticate(
    email: str, password: str, role: str | None = None, path: str | Path | None = None
) -> User | None:
    """Return the public User if email + password (and role, if given) match, else None."""
    record = _get_record(email, path)
    encoded = record.get("password_hash", "") if record else ""
    # Always hash once, even for unknown emails, so timing does not reveal accounts.
    ok = verify_password(password or "", encoded if record else _dummy_hash())
    if not record or not ok:
        return None
    if role is not None and record.get("role") != role:
        return None
    return _public(record)


class LoginThrottle:
    """In-memory lockout: ``max_failures`` failures within ``window`` seconds lock for ``lockout`` seconds."""

    def __init__(
        self,
        max_failures: int = 5,
        window: float = 600.0,
        lockout: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self.clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}

    @staticmethod
    def _key(key: str) -> str:
        return normalize_email(key)

    def check(self, key: str) -> float:
        """Seconds left in lockout (0.0 if a login attempt is allowed)."""
        left = self._locked_until.get(self._key(key), 0.0) - self.clock()
        return max(0.0, left)

    def record_failure(self, key: str) -> None:
        key, now = self._key(key), self.clock()
        fails = self._failures.setdefault(key, deque())
        fails.append(now)
        while fails and now - fails[0] > self.window:
            fails.popleft()
        if len(fails) >= self.max_failures:
            self._locked_until[key] = now + self.lockout
            fails.clear()

    def record_success(self, key: str) -> None:
        key = self._key(key)
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


# ---------------------------------------------------------------- account admin


def create_user(
    email: str,
    password: str,
    role: str,
    name: str,
    student_id: str | None = None,
    path: str | Path | None = None,
    must_change_password: bool = False,
) -> User:
    """Add one account; raises ValueError on bad input, a weak password or a duplicate."""
    email = normalize_email(email)
    if not valid_email(email):
        raise ValueError("Invalid email.")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    if role == "student" and not student_id:
        raise ValueError("Student accounts need a student_id.")
    _require_strong(password)
    sid = student_data.normalize_id(student_id) if role == "student" else None
    record = {"email": email, "role": role, "name": str(name or "").strip(), "student_id": sid,
              "must_change_password": must_change_password}
    password_hash = hash_password(password)
    with db.transaction(path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError(f"User already exists: {email}")
        if sid and conn.execute("SELECT 1 FROM users WHERE student_id = ?", (sid,)).fetchone():
            raise ValueError(f"An account already exists for student {sid}")
        _insert_user(conn, email, role, record["name"], sid, password_hash, must_change_password)
    return _public(record)


def set_password(email: str, new_password: str, path: str | Path | None = None) -> None:
    """Replace the password of an existing account (KeyError if unknown, ValueError if weak).

    Clears ``must_change_password``, signs out every device and drops pending reset codes.
    """
    email = normalize_email(email)
    _require_strong(new_password)
    password_hash = hash_password(new_password)
    with db.transaction(path) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE email = ?", (password_hash, email)
        )
        if cur.rowcount == 0:
            raise KeyError(f"No such user: {email}")
        conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
        conn.execute("DELETE FROM password_resets WHERE email = ?", (email,))


def admin_reset_password(email: str, by_admin_email: str, path: str | Path | None = None) -> str:
    """Admin sets a temporary password for another account and returns it (show it once).

    The account must change it at next sign-in (``must_change_password``); all its sessions
    end. Raises PermissionError if ``by_admin_email`` is not an admin, ValueError for the
    admin's own account (use the normal password change), KeyError for an unknown account.
    """
    target, admin = normalize_email(email), normalize_email(by_admin_email)
    admin_record = _get_record(admin, path)
    if not admin_record or admin_record["role"] != "admin":
        raise PermissionError("Only an admin can reset passwords.")
    if target == admin:
        raise ValueError("Use the Account page to change your own password.")
    temp = generate_password()
    password_hash = hash_password(temp)
    with db.transaction(path) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE email = ?", (password_hash, target)
        )
        if cur.rowcount == 0:
            raise KeyError(f"No such user: {target}")
        conn.execute("DELETE FROM sessions WHERE email = ?", (target,))
        conn.execute("DELETE FROM password_resets WHERE email = ?", (target,))
    return temp


# ---------------------------------------------------------------- self-registration and claiming


def register_new_student(
    name: str,
    email: str,
    password: str,
    branch: str,
    category: str = "General",
    hostel: bool = False,
    path: str | Path | None = None,
) -> User:
    """Admit a NEW first-year student: creates the student record and the account together.

    Raises ValueError with a message that is safe to show to the user.
    """
    name = " ".join(str(name or "").split())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise ValueError(f"Name must be {NAME_MIN} to {NAME_MAX} characters.")
    email = normalize_email(email)
    if not valid_email(email):
        raise ValueError("Enter a valid email address.")
    branch = str(branch or "").strip().upper()
    if branch not in BRANCHES:
        raise ValueError(f"Choose a valid branch ({', '.join(BRANCHES)}).")
    matches = [c for c in STUDENT_CATEGORIES if c.lower() == str(category or "").strip().lower()]
    if not matches:
        raise ValueError(f"Choose a valid category ({', '.join(STUDENT_CATEGORIES)}).")
    category = matches[0]
    _require_strong(password)
    if student_data.find_student_by_email(email, db_path=path):
        raise ValueError(
            "This email already belongs to a student record. Existing students should claim their account instead."
        )
    password_hash = hash_password(password)
    with db.transaction(path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone() or conn.execute(
            "SELECT 1 FROM students WHERE email = ?", (email,)
        ).fetchone():
            raise ValueError("An account with this email already exists. Sign in or reset your password.")
        record = student_data.create_student_record(
            name, email, branch, category, bool(hostel), db_path=path, conn=conn
        )
        student_data.save_student_record(conn, record)
        _insert_user(conn, email, "student", name, record["student_id"], password_hash)
    return _public({"email": email, "role": "student", "name": name, "student_id": record["student_id"]})


def claim_student_account(student_id: str, college_email: str, password: str, path: str | Path | None = None) -> User:
    """Create the account of an EXISTING student (in the college records) who has none yet.

    The student id and the college email must both match one record. Unknown ids and
    mismatches get the same generic error. The account email is the record's email.
    """
    _require_strong(password)
    sid = student_data.normalize_id(student_id)
    email = normalize_email(college_email)
    record = student_data.get_student(sid, db_path=path) if sid else None
    record_email = normalize_email((record or {}).get("email"))
    if not record or not email or not hmac.compare_digest(record_email.encode(), email.encode()):
        raise ValueError(CLAIM_MISMATCH)
    password_hash = hash_password(password)
    name = str(record.get("name", "")).strip()
    with db.transaction(path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = ? OR student_id = ?", (record_email, sid)).fetchone():
            raise ValueError("An account already exists for this student. Sign in, or use 'Forgot password'.")
        _insert_user(conn, record_email, "student", name, sid, password_hash)
    return _public({"email": record_email, "role": "student", "name": name, "student_id": sid})


# ---------------------------------------------------------------- password reset

RESET_CODE_TTL_SECONDS = 15 * 60
RESET_MAX_ATTEMPTS = 5
RESET_MIN_INTERVAL_SECONDS = 60


def request_password_reset(email: str, path: str | Path | None = None, now: float | None = None) -> dict:
    """Start a password reset; returns {"delivery": "email" | "screen" | "none", "code": str | None}.

    - SMTP configured (see mailer.py): the code is emailed and the result is ALWAYS
      {"delivery": "email", "code": None}, for known, unknown and rate-limited emails alike.
    - No SMTP (DEMO ONLY): a known account gets {"delivery": "screen", "code": "123456"} so the
      demo UI can show the code; unknown emails and rate-limited requests get
      {"delivery": "none", "code": None}. Never deploy screen delivery with real users.

    Only a hash of the 6-digit code is stored; it expires after 15 minutes, allows 5 attempts,
    a new request replaces the old code, and one request per email per 60 s is accepted.
    """
    now = time.time() if now is None else now
    email = normalize_email(email)
    use_email = mailer.email_configured()
    generic = {"delivery": "email" if use_email else "none", "code": None}
    if not valid_email(email):
        return generic
    code = f"{secrets.randbelow(10**6):06d}"
    code_hash = _hash_secret(code)  # hashed before the lookup so known and unknown emails cost the same
    with db.transaction(path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone() is None:
            return generic
        row = conn.execute("SELECT created_at FROM password_resets WHERE email = ?", (email,)).fetchone()
        if row and now - row["created_at"] < RESET_MIN_INTERVAL_SECONDS:
            return generic
        conn.execute(
            "INSERT OR REPLACE INTO password_resets (email, code_hash, expires, attempts, created_at)"
            " VALUES (?, ?, ?, 0, ?)",
            (email, code_hash, now + RESET_CODE_TTL_SECONDS, now),
        )
    if use_email:
        body = mailer.reset_email_body(code, RESET_CODE_TTL_SECONDS // 60)
        if not mailer.send_email(email, mailer.RESET_SUBJECT, body):
            log.warning("Password reset email could not be sent")
            with db.transaction(path) as conn:  # let the user retry at once
                conn.execute("DELETE FROM password_resets WHERE email = ? AND code_hash = ?", (email, code_hash))
        return generic
    return {"delivery": "screen", "code": code}


def confirm_password_reset(
    email: str, code: str, new_password: str, path: str | Path | None = None, now: float | None = None
) -> bool:
    """Set a new password if the reset code is right; False for a wrong/expired/used-up code.

    Raises ValueError (before using an attempt) when the new password is too weak. On success
    every session of the account ends and the code is deleted.
    """
    _require_strong(new_password)
    now = time.time() if now is None else now
    email = normalize_email(email)
    code = re.sub(r"\s+", "", str(code or ""))
    with db.transaction(path) as conn:
        row = conn.execute("SELECT * FROM password_resets WHERE email = ?", (email,)).fetchone()
        if row is None:
            return False
        if row["expires"] <= now:
            conn.execute("DELETE FROM password_resets WHERE email = ?", (email,))
            return False
        if row["attempts"] >= RESET_MAX_ATTEMPTS:
            return False
        conn.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE email = ?", (email,))
        code_hash = row["code_hash"]
    # verify_password compares in constant time; it runs outside the write lock (PBKDF2 is slow).
    if not (len(code) == 6 and code.isdigit() and verify_password(code, code_hash)):
        return False
    with db.transaction(path) as conn:
        cur = conn.execute("DELETE FROM password_resets WHERE email = ? AND code_hash = ?", (email, code_hash))
        if cur.rowcount == 0:
            return False  # replaced or used meanwhile
    try:
        set_password(email, new_password, path)
    except KeyError:
        return False
    return True


# ---------------------------------------------------------------- sessions (stay signed in)

SESSION_TTL_SECONDS = 12 * 3600
_TOKEN_RE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _token_hash(token: str) -> str:
    # Only the hash is stored, so a leaked database cannot be replayed.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_token(token: object) -> bool:
    return isinstance(token, str) and 32 <= len(token) <= 128 and set(token) <= _TOKEN_RE_CHARS


def create_session(
    user: User | dict,
    ttl_seconds: int = SESSION_TTL_SECONDS,
    path: str | Path | None = None,
    users_path: str | Path | None = None,
    now: float | None = None,
) -> str:
    """Start a session for a signed-in user and return its random token (store it in a cookie).

    Raises KeyError if the account does not exist.
    """
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    email = normalize_email(user["email"])
    with db.transaction(path or users_path) as conn:
        conn.execute("DELETE FROM sessions WHERE expires <= ?", (now,))  # prune expired entries
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone() is None:
            raise KeyError(f"No such user: {email}")
        conn.execute(
            "INSERT INTO sessions (token_hash, email, role, expires) VALUES (?, ?, ?, ?)",
            (_token_hash(token), email, user["role"], now + ttl_seconds),
        )
    return token


def get_session_user(
    token: object,
    path: str | Path | None = None,
    users_path: str | Path | None = None,
    now: float | None = None,
) -> User | None:
    """Return the User behind a valid, unexpired token, else None.

    The account is re-read, so a deleted account or a changed role ends the session.
    """
    if not _valid_token(token):
        return None
    now = time.time() if now is None else now
    with db.reading(path or users_path) as conn:
        row = conn.execute(
            "SELECT u.*, s.role AS session_role FROM sessions s JOIN users u ON u.email = s.email"
            " WHERE s.token_hash = ? AND s.expires > ?",
            (_token_hash(token), now),
        ).fetchone()
    if not row or row["role"] != row["session_role"]:
        return None
    return _public(_record(row))


def revoke_session(token: object, path: str | Path | None = None, users_path: str | Path | None = None) -> None:
    """End one session (log out). Unknown tokens are ignored."""
    if not _valid_token(token):
        return
    with db.transaction(path or users_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def revoke_user_sessions(email: str, path: str | Path | None = None, users_path: str | Path | None = None) -> int:
    """End every session of one account; returns how many were removed."""
    with db.transaction(path or users_path) as conn:
        removed = conn.execute("DELETE FROM sessions WHERE email = ?", (normalize_email(email),)).rowcount
    return removed


# ---------------------------------------------------------------- demo seeding

# No ambiguous letters (l, o, i) or digits (0, 1).
_CONSONANTS = "bcdfghjkmnprstvwz"
_VOWELS = "aeu"
_DIGITS = "23456789"


def generate_password() -> str:
    """Readable random password like ``kavu-desa-4827`` (14 chars, letters and digits)."""

    def chunk() -> str:
        return "".join(secrets.choice(_CONSONANTS) + secrets.choice(_VOWELS) for _ in range(2))

    digits = "".join(secrets.choice(_DIGITS) for _ in range(4))
    return f"{chunk()}-{chunk()}-{digits}"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def seed_demo_users(
    path: str | Path | None = None,
    credentials_csv: str | Path | None = None,
    students_file: str | Path | None = None,
    force: bool = False,
    shared_password: str | None = None,
    write_csv: bool = True,
) -> int:
    """Create one account per student in the students JSON plus the admin; return the number created.

    Writes the accounts to the database (hashes only) and, unless ``write_csv`` is False, a CSV
    with plaintext demo passwords (default: ``demo_credentials.csv`` next to the database).
    ``force`` overwrites the seeded accounts (their sessions end); other accounts, such as
    self-registered students, are kept. ``shared_password`` gives every demo account the same
    password (hosted demos); it is an operator setting, so only its minimum length is checked.
    """
    p = db.db_path(path)
    csv_path = Path(credentials_csv) if credentials_csv else p.parent / "demo_credentials.csv"
    students_path = Path(students_file) if students_file else get_settings().students_file
    if not force and (load_users(p) or (write_csv and csv_path.exists())):
        raise FileExistsError(f"{p} already has accounts or {csv_path} already exists; use force=True to overwrite.")
    if shared_password is not None:
        hash_password(shared_password)  # fail early if it is too short

    data = json.loads(students_path.read_text(encoding="utf-8"))
    rows = data.get("students", []) if isinstance(data, dict) else data
    accounts = [
        ("student", normalize_email(s["email"]), s.get("name", ""), student_data.normalize_id(s["student_id"]))
        for s in rows
        if isinstance(s, dict) and s.get("email") and s.get("student_id")
    ]
    accounts.append(("admin", ADMIN_EMAIL, ADMIN_NAME, None))

    seen: set[str] = set()
    prepared: list[tuple] = []
    creds: list[dict] = []
    for role, email, name, sid in accounts:
        if email in seen:
            raise ValueError(f"Duplicate email in students file: {email}")
        seen.add(email)
        password = shared_password if shared_password is not None else generate_password()
        prepared.append((email, role, name, sid, hash_password(password)))
        creds.append({"role": role, "email": email, "name": name, "student_id": sid or "", "password": password})

    with db.transaction(p) as conn:
        for email, role, name, sid, password_hash in prepared:
            if sid:  # a different account holding this student id would block the seed
                conn.execute("DELETE FROM users WHERE student_id = ? AND email <> ?", (sid, email))
            conn.execute(
                "INSERT INTO users (email, role, name, student_id, password_hash, must_change_password, created_at)"
                " VALUES (?, ?, ?, ?, ?, 0, ?)"
                " ON CONFLICT(email) DO UPDATE SET role = excluded.role, name = excluded.name,"
                " student_id = excluded.student_id, password_hash = excluded.password_hash,"
                " must_change_password = 0",
                (email, role, name, sid, password_hash, db.now_iso()),
            )
            conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
    if write_csv:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(creds)
        _atomic_write(csv_path, buf.getvalue())
    return len(prepared)


def ensure_hosted_demo_users(path: str | Path | None = None) -> int:
    """Hosted demo bootstrap: seed accounts once when SCA_DEMO_PASSWORD is set and none exist.

    Cloud hosts start without the git-ignored database. Every demo account then gets the
    shared demo password from the environment (e.g. Streamlit secrets); no plaintext CSV is
    written. Returns the number of accounts created (0 if skipped).
    """
    password = os.getenv("SCA_DEMO_PASSWORD") or ""
    if not password or count_users(path):
        return 0
    problems = validate_new_password(password)
    if problems:
        # The app swallows bootstrap errors, so leave a clear hint in the server log.
        log.warning(
            "SCA_DEMO_PASSWORD is not usable (%s); demo accounts were not created.", "; ".join(problems)
        )
        return 0
    return seed_demo_users(path=path, force=True, shared_password=password, write_csv=False)


# ---------------------------------------------------------------- CLI


def _ask_new_password() -> str:
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise ValueError("Passwords do not match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smart_campus_assistant.auth", description=__doc__.splitlines()[0])
    parser.add_argument("--db-file", help="SQLite database (default: settings.db_file / SCA_DB_FILE).")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create the database and import legacy users.json / sessions.json once.")

    seed = sub.add_parser("seed-demo", help="Create demo accounts for all students plus the admin.")
    seed.add_argument("--force", action="store_true", help="Overwrite seeded accounts and the credentials file.")

    add = sub.add_parser("add-user", help="Add one account (password asked twice).")
    add.add_argument("--email", required=True)
    add.add_argument("--role", required=True, choices=ROLES)
    add.add_argument("--name", required=True)
    add.add_argument("--student-id")

    setpw = sub.add_parser("set-password", help="Change an account's password (asked twice).")
    setpw.add_argument("--email", required=True)

    sub.add_parser("list-users", help="List accounts (email, role, student_id, must-change flag).")

    args = parser.parse_args(argv)
    path = Path(args.db_file) if args.db_file else get_settings().db_file

    try:
        if args.command == "init-db":
            info = db.init_db(path)
            print(f"Database {info['path']} (schema v{info['schema_version']}): {info['users']} accounts, "
                  f"imported {info['imported_users']} users and {info['imported_sessions']} sessions")
        elif args.command == "seed-demo":
            csv_path = path.parent / "demo_credentials.csv"
            count = seed_demo_users(path=path, credentials_csv=csv_path, force=args.force)
            print(f"Created {count} accounts in {path}")
            print(f"Demo credentials written to {csv_path} (git-ignored, local demos only)")
        elif args.command == "add-user":
            user = create_user(args.email, _ask_new_password(), args.role, args.name, args.student_id, path)
            print(f"Added {user['role']} {user['email']}")
        elif args.command == "set-password":
            set_password(args.email, _ask_new_password(), path)
            print(f"Password updated for {normalize_email(args.email)}")
        elif args.command == "list-users":
            for email, r in sorted(load_users(path).items()):
                flag = "\tmust-change" if r.get("must_change_password") else ""
                print(f"{email}\t{r.get('role')}\t{r.get('student_id') or '-'}{flag}")
    except (FileExistsError, KeyError, ValueError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"Error: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
