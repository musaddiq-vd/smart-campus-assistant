"""Admin notices: a small JSON store plus helpers that make each notice searchable.

Storage: ``data/notices.json`` (override with env ``SCA_NOTICES_FILE``)::

    {"notices": [{"id": "N-2026-0001", "title": ..., "category": "exams", "branches": ["ALL"],
                  "body": ..., "published_at": "2026-11-03", "author": ...}]}

Each notice becomes one or more chunks (doc_type "notices", source "notices.json") whose
ids are their chunk_ids, so indexing is idempotent and a notice can be removed from the
vector store by id.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from smart_campus_assistant.config import BRANCHES, COLLEGE_KEY, PROJECT_ROOT

logger = logging.getLogger(__name__)

CATEGORIES: dict[str, str] = {
    "exams": "Exams",
    "fees": "Fees",
    "hostel": "Hostel",
    "library": "Library",
    "general": "General",
}
ALL_BRANCHES = "ALL"
SOURCE = "notices.json"
DOC_TYPE = "notices"

TITLE_MIN, TITLE_MAX = 5, 120
BODY_MIN, BODY_MAX = 10, 4000

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
# Upper bound on chunks per notice (a 4000-char body gives about 8); unindex deletes all of these ids.
MAX_CHUNKS = 16

_lock = threading.RLock()


class NoticeError(ValueError):
    """Invalid notice input (message is safe to show in the UI)."""


# ---------- storage ----------

def notices_file() -> Path:
    """Path of the notices JSON file (read from the environment on every call)."""
    return Path(os.getenv("SCA_NOTICES_FILE", str(PROJECT_ROOT / "data" / "notices.json")))


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; treating it as empty", path)
        return []
    items = data.get("notices", []) if isinstance(data, dict) else []
    return [n for n in items if isinstance(n, dict) and n.get("id")]


def _save(path: Path, notices: list[dict]) -> None:
    """Atomic write: temp file in the same folder, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"notices": notices}, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _sort_key(notice: dict) -> tuple[str, int]:
    return (str(notice.get("published_at") or ""), _seq(notice.get("id")))


def _seq(notice_id) -> int:
    match = re.search(r"(\d+)$", str(notice_id or ""))
    return int(match.group(1)) if match else 0


def list_notices(branch: str | None = None, limit: int | None = None) -> list[dict]:
    """Notices newest first. With `branch`, keep notices for "ALL" plus that branch."""
    with _lock:
        notices = _load(notices_file())
    if branch:
        code = str(branch).upper()
        notices = [n for n in notices if {ALL_BRANCHES, code} & {str(b).upper() for b in n.get("branches") or []}]
    notices.sort(key=_sort_key, reverse=True)
    return notices[:limit] if limit is not None else notices


def get_notice(notice_id: str) -> dict | None:
    with _lock:
        return next((n for n in _load(notices_file()) if n.get("id") == notice_id), None)


# ---------- validation ----------

_TAG_RE = re.compile(r"<[^>]*>")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_input(text, multiline: bool = False) -> str:
    """Strip HTML-ish tags and control characters; collapse spaces (keep newlines if multiline)."""
    text = html.unescape(str(text or ""))
    text = _TAG_RE.sub("", text).replace("<", "").replace(">", "")
    text = _CTRL_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_branches(branches) -> list[str]:
    """Upper-case, de-duplicate, validate. "ALL" (alone or mixed in) means every branch."""
    if isinstance(branches, str):
        branches = [branches]
    codes = [str(b).strip().upper() for b in (branches or []) if str(b).strip()]
    if not codes:
        raise NoticeError("Pick at least one branch (or ALL).")
    unknown = [c for c in codes if c != ALL_BRANCHES and c not in BRANCHES]
    if unknown:
        raise NoticeError(f"Unknown branch: {', '.join(unknown)}.")
    if ALL_BRANCHES in codes or set(codes) >= set(BRANCHES):
        return [ALL_BRANCHES]
    return [b for b in BRANCHES if b in codes]


def validate(title, body, category, branches) -> dict:
    """Return cleaned fields or raise NoticeError with a user-friendly message."""
    title = clean_input(title)
    body = clean_input(body, multiline=True)
    category = str(category or "").strip().lower()
    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        raise NoticeError(f"Title must be {TITLE_MIN}-{TITLE_MAX} characters (got {len(title)}).")
    if not BODY_MIN <= len(body) <= BODY_MAX:
        raise NoticeError(f"Notice text must be {BODY_MIN}-{BODY_MAX} characters (got {len(body)}).")
    if category not in CATEGORIES:
        raise NoticeError(f"Unknown category: {category or '(empty)'}.")
    return {"title": title, "body": body, "category": category, "branches": normalize_branches(branches)}


# ---------- add / delete ----------

def _next_id(notices: list[dict], today: date) -> str:
    seq = max((_seq(n.get("id")) for n in notices), default=0) + 1
    return f"N-{today.year}-{seq:04d}"


def add_notice(title, body, category, branches, author, today: date | None = None) -> dict:
    """Validate, store and return the new notice (raises NoticeError on bad input)."""
    fields = validate(title, body, category, branches)
    today = today or date.today()
    with _lock:
        path = notices_file()
        notices = _load(path)
        notice = {
            "id": _next_id(notices, today),
            **fields,
            "published_at": today.isoformat(),
            "author": clean_input(author) or "Admin",
        }
        notices.append(notice)
        _save(path, notices)
    return notice


def delete_notice(notice_id: str) -> bool:
    """Remove a notice from the file. Returns False if no notice has that id."""
    with _lock:
        path = notices_file()
        notices = _load(path)
        kept = [n for n in notices if n.get("id") != notice_id]
        if len(kept) == len(notices):
            return False
        _save(path, kept)
    return True


# ---------- documents / vector store ----------

def branches_label(branches) -> str:
    codes = list(branches or [])
    return "All branches" if not codes or ALL_BRANCHES in codes else ", ".join(codes)


def chunk_id(notice_id: str, n: int) -> str:
    return f"{SOURCE}:{notice_id}:c{n}"


def notice_documents(notices: list[dict]) -> list[Document]:
    """Chunks for the given notices; `page` is the notice's running number in the list."""
    docs: list[Document] = []
    for page, notice in enumerate(notices, start=1):
        nid = notice["id"]
        header = f"Notice {nid} ({notice.get('published_at', '')}) > {notice.get('title', '')}\n"
        meta_line = (
            f"Category: {CATEGORIES.get(notice.get('category'), 'General')}. "
            f"For: {branches_label(notice.get('branches'))}.\n"
        )
        prefix = header + meta_line
        body_size = max(CHUNK_SIZE - len(prefix), 200)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=body_size, chunk_overlap=min(CHUNK_OVERLAP, body_size // 4)
        )
        pieces = [p.strip() for p in splitter.split_text(str(notice.get("body") or "")) if p.strip()]
        for n, piece in enumerate(pieces[:MAX_CHUNKS]):
            cid = chunk_id(nid, n)
            docs.append(
                Document(
                    id=cid,
                    page_content=(prefix + piece)[:CHUNK_SIZE],
                    metadata={
                        "college": COLLEGE_KEY,
                        "doc_type": DOC_TYPE,
                        "source": SOURCE,
                        "page": page,
                        "chunk_id": cid,
                    },
                )
            )
    return docs


def all_notice_documents() -> list[Document]:
    """Chunks for every stored notice, oldest first (stable page numbers for a full ingest)."""
    return notice_documents(list(reversed(list_notices())))


def index_notice(notice: dict) -> tuple[bool, str]:
    """Add one notice's chunks to the vector store (ids = chunk_ids). Never raises."""
    try:
        from smart_campus_assistant import retriever

        docs = notice_documents([notice])
        if not docs:
            return False, "Notice has no text to index."
        store = retriever.get_vector_store()
        store.add_documents(docs, ids=[d.id for d in docs])
        return True, f"Indexed for search ({len(docs)} chunk{'s' if len(docs) != 1 else ''})."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Indexing notice %s failed: %s", notice.get("id"), exc)
        return False, f"Saved, but search indexing failed: {type(exc).__name__}."


def unindex_notice(notice_id: str) -> tuple[bool, str]:
    """Delete every possible chunk id of a notice from the vector store. Never raises."""
    ids = [chunk_id(notice_id, n) for n in range(MAX_CHUNKS)]
    try:
        from smart_campus_assistant import retriever

        # Never call delete with empty ids: AmazonS3Vectors.delete(ids=None) drops the whole index.
        retriever.get_vector_store().delete(ids=ids)
        return True, "Removed from search."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unindexing notice %s failed: %s", notice_id, exc)
        return False, f"Deleted, but removing it from search failed: {type(exc).__name__}."


def publish_notice(title, body, category, branches, author) -> tuple[dict, bool, str]:
    """add_notice + index_notice. Raises NoticeError only for invalid input."""
    notice = add_notice(title, body, category, branches, author)
    indexed, message = index_notice(notice)
    return notice, indexed, message


def remove_notice(notice_id: str) -> tuple[bool, bool, str]:
    """delete_notice + unindex_notice -> (deleted, unindexed, message)."""
    if not delete_notice(notice_id):
        return False, False, f"Notice {notice_id} was not found."
    unindexed, message = unindex_notice(notice_id)
    return True, unindexed, message
