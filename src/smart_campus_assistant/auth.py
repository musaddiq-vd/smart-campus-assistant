"""Login accounts: password hashing, the users file, authentication and throttling.

Passwords are stored only as PBKDF2-HMAC-SHA256 hashes in the users file
(``settings.users_file``, default data/users.json). The one exception is the
git-ignored demo credentials CSV written by ``seed-demo`` for local demos.

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
import os
import secrets
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Literal, TypedDict

from smart_campus_assistant.config import get_settings

ALGORITHM = "pbkdf2_sha256"
# Tests lower this to keep runs fast; stored hashes carry their own count.
ITERATIONS = 600_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8
ROLES = ("student", "admin")
ADMIN_EMAIL = "admin@xyz.edu.example"
ADMIN_NAME = "XYZ Admin Office"
CSV_COLUMNS = ("role", "email", "name", "student_id", "password")


class User(TypedDict):
    """Public view of an account (never contains the hash)."""

    email: str
    role: Literal["student", "admin"]
    name: str
    student_id: str | None


# ---------------------------------------------------------------- hashing


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    """Return ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`` with a fresh salt."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${_b64(salt)}${_b64(digest)}"


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


# ---------------------------------------------------------------- users file


def normalize_email(email: object) -> str:
    return str(email or "").strip().lower()


def _users_path(path: str | Path | None) -> Path:
    return Path(path) if path else get_settings().users_file


def load_users(path: str | Path | None = None) -> dict[str, dict]:
    """Return {email_lower: record}; {} if the file is missing."""
    path = _users_path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    users: dict[str, dict] = {}
    for row in data.get("users", []):
        if isinstance(row, dict) and row.get("email"):
            users[normalize_email(row["email"])] = dict(row)
    return users


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


def save_users(records: dict[str, dict], path: str | Path | None = None) -> None:
    """Write all records (sorted by email) atomically."""
    rows = [
        {
            "email": normalize_email(r["email"]),
            "role": r["role"],
            "name": r.get("name", ""),
            "student_id": r.get("student_id"),
            "password_hash": r["password_hash"],
        }
        for _, r in sorted(records.items())
    ]
    _atomic_write(_users_path(path), json.dumps({"users": rows}, indent=2) + "\n")


def _public(record: dict) -> User:
    return User(
        email=normalize_email(record["email"]),
        role=record["role"],
        name=record.get("name", ""),
        student_id=record.get("student_id"),
    )


# ---------------------------------------------------------------- login


def authenticate(
    email: str, password: str, role: str | None = None, path: str | Path | None = None
) -> User | None:
    """Return the public User if email + password (and role, if given) match, else None."""
    record = load_users(path).get(normalize_email(email))
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
) -> User:
    """Add one account; raises ValueError on bad input or a duplicate email."""
    email = normalize_email(email)
    if "@" not in email:
        raise ValueError("Invalid email.")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    if role == "student" and not student_id:
        raise ValueError("Student accounts need a student_id.")
    users = load_users(path)
    if email in users:
        raise ValueError(f"User already exists: {email}")
    record = {
        "email": email,
        "role": role,
        "name": name.strip(),
        "student_id": student_id.strip().upper() if role == "student" else None,
        "password_hash": hash_password(password),
    }
    users[email] = record
    save_users(users, path)
    return _public(record)


def set_password(email: str, new_password: str, path: str | Path | None = None) -> None:
    """Replace the password hash of an existing account (KeyError if unknown)."""
    email = normalize_email(email)
    users = load_users(path)
    if email not in users:
        raise KeyError(f"No such user: {email}")
    users[email]["password_hash"] = hash_password(new_password)
    save_users(users, path)
    revoke_user_sessions(email, users_path=path)  # a new password signs out every device


# ---------------------------------------------------------------- sessions (stay signed in)

SESSION_TTL_SECONDS = 12 * 3600
_TOKEN_RE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
_sessions_lock = threading.Lock()


def _sessions_path(path: str | Path | None = None, users_path: str | Path | None = None) -> Path:
    """Sessions live next to the users file (git-ignored data/sessions.json)."""
    if path:
        return Path(path)
    return _users_path(users_path).with_name("sessions.json")


def _token_hash(token: str) -> str:
    # Only the hash is stored, so a leaked sessions file cannot be replayed.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_token(token: object) -> bool:
    return isinstance(token, str) and 32 <= len(token) <= 128 and set(token) <= _TOKEN_RE_CHARS


def _load_sessions(path: Path, now: float) -> dict[str, dict]:
    """Return unexpired sessions; a missing or broken file counts as empty."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sessions = data.get("sessions", {}) if isinstance(data, dict) else {}
    return {
        h: s for h, s in sessions.items()
        if isinstance(s, dict) and isinstance(s.get("expires"), (int, float)) and s["expires"] > now
    }


def _save_sessions(path: Path, sessions: dict[str, dict]) -> None:
    _atomic_write(path, json.dumps({"sessions": sessions}, indent=2) + "\n")


def create_session(
    user: User | dict,
    ttl_seconds: int = SESSION_TTL_SECONDS,
    path: str | Path | None = None,
    users_path: str | Path | None = None,
    now: float | None = None,
) -> str:
    """Start a session for a signed-in user and return its random token (store it in a cookie)."""
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    spath = _sessions_path(path, users_path)
    with _sessions_lock:
        sessions = _load_sessions(spath, now)  # also prunes expired entries
        sessions[_token_hash(token)] = {
            "email": normalize_email(user["email"]),
            "role": user["role"],
            "expires": now + ttl_seconds,
        }
        _save_sessions(spath, sessions)
    return token


def get_session_user(
    token: object,
    path: str | Path | None = None,
    users_path: str | Path | None = None,
    now: float | None = None,
) -> User | None:
    """Return the User behind a valid, unexpired token, else None.

    The account is re-read from the users file, so a deleted account or a changed
    role ends the session.
    """
    if not _valid_token(token):
        return None
    now = time.time() if now is None else now
    session = _load_sessions(_sessions_path(path, users_path), now).get(_token_hash(token))
    if not session:
        return None
    record = load_users(users_path).get(session["email"])
    if not record or record.get("role") != session.get("role"):
        return None
    return _public(record)


def revoke_session(token: object, path: str | Path | None = None, users_path: str | Path | None = None) -> None:
    """End one session (log out). Unknown tokens are ignored."""
    if not _valid_token(token):
        return
    spath = _sessions_path(path, users_path)
    with _sessions_lock:
        sessions = _load_sessions(spath, time.time())
        if sessions.pop(_token_hash(token), None) is not None:
            _save_sessions(spath, sessions)


def revoke_user_sessions(email: str, path: str | Path | None = None, users_path: str | Path | None = None) -> int:
    """End every session of one account; returns how many were removed."""
    email = normalize_email(email)
    spath = _sessions_path(path, users_path)
    with _sessions_lock:
        sessions = _load_sessions(spath, time.time())
        keep = {h: s for h, s in sessions.items() if s.get("email") != email}
        removed = len(sessions) - len(keep)
        if removed or spath.exists():
            _save_sessions(spath, keep)
    return removed


# ---------------------------------------------------------------- demo seeding

# No ambiguous letters (l, o, i) or digits (0, 1).
_CONSONANTS = "bcdfghjkmnprstvwz"
_VOWELS = "aeu"
_DIGITS = "23456789"


def generate_password() -> str:
    """Readable random password like ``kavu-desa-4827`` (14 chars)."""

    def chunk() -> str:
        return "".join(secrets.choice(_CONSONANTS) + secrets.choice(_VOWELS) for _ in range(2))

    digits = "".join(secrets.choice(_DIGITS) for _ in range(4))
    return f"{chunk()}-{chunk()}-{digits}"


def seed_demo_users(
    path: str | Path | None = None,
    credentials_csv: str | Path | None = None,
    students_file: str | Path | None = None,
    force: bool = False,
    shared_password: str | None = None,
    write_csv: bool = True,
) -> int:
    """Create one account per student plus the admin; return the number created.

    Writes the users file (hashes only) and, unless ``write_csv`` is False, a CSV
    with plaintext demo passwords (default: ``demo_credentials.csv`` next to the
    users file). ``shared_password`` gives every demo account the same password
    (used by hosted demos) instead of a random one each.
    """
    path = _users_path(path)
    csv_path = Path(credentials_csv) if credentials_csv else path.parent / "demo_credentials.csv"
    students_path = Path(students_file) if students_file else get_settings().students_file
    if not force and (path.exists() or (write_csv and csv_path.exists())):
        raise FileExistsError(f"{path} or {csv_path} already exists; use force=True to overwrite.")
    if shared_password is not None:
        hash_password(shared_password)  # fail early if it is too short

    data = json.loads(students_path.read_text(encoding="utf-8"))
    rows = data.get("students", []) if isinstance(data, dict) else data

    accounts = [
        ("student", normalize_email(s["email"]), s.get("name", ""), str(s["student_id"]).upper())
        for s in rows
        if isinstance(s, dict) and s.get("email") and s.get("student_id")
    ]
    accounts.append(("admin", ADMIN_EMAIL, ADMIN_NAME, None))

    users: dict[str, dict] = {}
    creds: list[dict] = []
    for role, email, name, sid in accounts:
        if email in users:
            raise ValueError(f"Duplicate email in students file: {email}")
        password = shared_password if shared_password is not None else generate_password()
        users[email] = {
            "email": email,
            "role": role,
            "name": name,
            "student_id": sid,
            "password_hash": hash_password(password),
        }
        creds.append({"role": role, "email": email, "name": name, "student_id": sid or "", "password": password})

    save_users(users, path)
    if write_csv:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(creds)
        _atomic_write(csv_path, buf.getvalue())
    return len(users)


def ensure_hosted_demo_users(path: str | Path | None = None) -> int:
    """Hosted demo bootstrap: seed accounts once when SCA_DEMO_PASSWORD is set and none exist.

    Cloud hosts start without the git-ignored users file. Every demo account then
    gets the shared demo password from the environment (e.g. Streamlit secrets);
    no plaintext CSV is written. Returns the number of accounts created (0 if skipped).
    """
    password = os.getenv("SCA_DEMO_PASSWORD") or ""
    if not password or load_users(path):
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
    parser.add_argument("--users-file", help="Users JSON (default: settings.users_file / SCA_USERS_FILE).")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-demo", help="Create demo accounts for all students plus the admin.")
    seed.add_argument("--force", action="store_true", help="Overwrite existing users and credentials files.")

    add = sub.add_parser("add-user", help="Add one account (password asked twice).")
    add.add_argument("--email", required=True)
    add.add_argument("--role", required=True, choices=ROLES)
    add.add_argument("--name", required=True)
    add.add_argument("--student-id")

    setpw = sub.add_parser("set-password", help="Change an account's password (asked twice).")
    setpw.add_argument("--email", required=True)

    sub.add_parser("list-users", help="List accounts (email, role, student_id).")

    args = parser.parse_args(argv)
    path = Path(args.users_file) if args.users_file else get_settings().users_file

    try:
        if args.command == "seed-demo":
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
                print(f"{email}\t{r.get('role')}\t{r.get('student_id') or '-'}")
    except (FileExistsError, KeyError, ValueError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"Error: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
