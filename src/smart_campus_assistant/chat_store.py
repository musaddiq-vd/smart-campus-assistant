"""Per-user chat history on disk, so the "Ask" page survives a browser refresh.

One JSON file per user: ``data/chats/<sha256(lower-cased email)>.json`` (git-ignored).
The folder can be moved with the ``SCA_CHATS_DIR`` environment variable (used by tests).
Only what the UI needs to re-render a past turn is stored: no tokens, cost or timings.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

MAX_MESSAGES = 50
MAX_SNIPPET_CHARS = 600
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "chats"

_lock = threading.Lock()

# Fields kept per message (everything else is dropped).
_USER_FIELDS = ("role", "content", "created_at")
_ASSISTANT_FIELDS = ("role", "answer", "route", "route_reason", "citations", "steps", "no_record", "created_at")
_CITATION_FIELDS = ("source", "college", "page", "doc_type", "snippet")
_STEP_FIELDS = ("node", "n_docs", "n_plan", "guest")  # step names plus small counts for the flow strip


def chats_dir() -> Path:
    env = os.environ.get("SCA_CHATS_DIR")
    return Path(env) if env else DEFAULT_DIR


def _path(email: str) -> Path:
    key = str(email or "").strip().lower()
    if not key:
        raise ValueError("An email is required to store chat history.")
    return chats_dir() / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def _clean(message: object) -> dict | None:
    """Whitelist the fields of one message; None if it is not a valid user/assistant message."""
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if role == "user":
        if not isinstance(message.get("content"), str):
            return None
        out = {k: message[k] for k in _USER_FIELDS if k in message}
    elif role == "assistant":
        out = {k: message[k] for k in _ASSISTANT_FIELDS if k in message}
        out["answer"] = str(out.get("answer") or "")
        out["citations"] = [
            {k: c[k] for k in _CITATION_FIELDS if k in c}
            for c in (out.get("citations") or [])
            if isinstance(c, dict)
        ]
        for c in out["citations"]:
            if isinstance(c.get("snippet"), str):
                c["snippet"] = c["snippet"][:MAX_SNIPPET_CHARS]
        out["steps"] = [
            {k: s[k] for k in _STEP_FIELDS if k in s}
            for s in (out.get("steps") or [])
            if isinstance(s, dict) and s.get("node")
        ]
    else:
        return None
    out.setdefault("created_at", time.time())
    return out


def _read(path: Path) -> list[dict]:
    """Messages in the file; [] when it is missing or broken."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [m for m in (_clean(r) for r in rows) if m]


def _write(path: Path, messages: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            json.dump({"messages": messages}, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)  # atomic: readers never see a half-written file
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_history(email: str) -> list[dict]:
    """Last MAX_MESSAGES messages of this user, oldest first."""
    with _lock:
        return _read(_path(email))[-MAX_MESSAGES:]


def append_messages(email: str, messages: list[dict]) -> None:
    """Add messages to this user's history and keep only the last MAX_MESSAGES."""
    new = [m for m in (_clean(m) for m in messages or []) if m]
    if not new:
        return
    path = _path(email)
    with _lock:
        _write(path, (_read(path) + new)[-MAX_MESSAGES:])


def clear_history(email: str) -> None:
    """Delete this user's stored history."""
    path = _path(email)
    with _lock:
        path.unlink(missing_ok=True)
