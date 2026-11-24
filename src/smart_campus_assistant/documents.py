"""Documents admins upload so the assistant can answer from them (extra rules, circulars, policies).

Storage: ``data/uploads/`` (override with env ``SCA_UPLOADS_DIR``; git-ignored). Each upload is kept
as cleaned text: ``.md`` and ``.txt`` files as uploaded, a PDF as Markdown with one "## Page N"
section per page (so citations can name the page). Uploads are chunked exactly like the college
documents (``ingest.text_documents`` + ``ingest.split_documents``) with doc_type "uploads", and
their chunk ids are ``<file name>:s<section>:c<piece>``, so they can be added to and removed from
the vector store by id the way notices are.

The original ``data/college`` documents are never touched here: uploads cannot replace or delete them.
"""

from __future__ import annotations

import html
import importlib.util
import io
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.documents import Document

from smart_campus_assistant.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DOC_TYPE = "uploads"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB per file
MAX_TEXT_CHARS = 200_000  # about 250 chunks: keeps indexing quick and cheap
MAX_DOCUMENTS = 50
NAME_MAX_CHARS = 60
TEXT_SUFFIXES = (".md", ".txt")
PDF_SUFFIX = ".pdf"
INDEX_BATCH = 50

_lock = threading.RLock()


class DocumentError(ValueError):
    """Invalid upload (the message is safe to show in the UI)."""


# ---------- paths and names ----------

def uploads_dir() -> Path:
    """Folder of uploaded documents (read from the environment on every call, so tests can move it)."""
    return Path(os.getenv("SCA_UPLOADS_DIR", str(PROJECT_ROOT / "data" / "uploads")))


def pdf_supported() -> bool:
    """PDF upload needs the optional ``pypdf`` package."""
    return importlib.util.find_spec("pypdf") is not None


def allowed_types() -> list[str]:
    """File extensions the upload form accepts (without the dot)."""
    return [s.lstrip(".") for s in TEXT_SUFFIXES] + (["pdf"] if pdf_supported() else [])


def upload_files() -> list[Path]:
    """Stored uploads, sorted by name (missing folder -> [])."""
    folder = uploads_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES)


def stored_name(filename: str) -> str:
    """Safe file name for an upload: "Hostel Rules (2027).PDF" -> "hostel_rules_2027.md".

    Only lower-case letters, digits, "_" and "-" survive, so a name can never leave the uploads folder.
    """
    raw = Path(str(filename or "").replace("\\", "/")).name
    suffix = Path(raw).suffix.lower()
    stem = re.sub(r"[^a-z0-9_-]+", "_", Path(raw).stem.lower()).strip("_-")
    stem = re.sub(r"_{2,}", "_", stem)[:NAME_MAX_CHARS].strip("_-")
    if not stem:
        raise DocumentError("The file name has no letters or digits. Rename the file and try again.")
    return f"{stem}{'.md' if suffix == PDF_SUFFIX else suffix}"


def title_for(name: str) -> str:
    """Readable title from a stored name: "hostel_rules_2027.md" -> "Hostel rules 2027"."""
    words = re.sub(r"[_-]+", " ", Path(name).stem).strip()
    return words[:1].upper() + words[1:] if words else "Uploaded document"


def _college_stems() -> set[str]:
    """Stems of the built-in college documents (an upload may not take one of these names)."""
    from smart_campus_assistant import ingest

    return {p.stem.lower() for p in Path(ingest.get_settings().data_dir).glob("*.md")}


def _size_label(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{max(1, round(size / 1024))} KB"


# ---------- reading and cleaning ----------

_TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^<>\n]*>", flags=re.DOTALL)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_document_text(text: str) -> str:
    """Drop HTML tags/comments and control characters; normalise newlines and spaces; keep Markdown.

    Only real tags (``<b>``, ``</script>``) are removed, so "CGPA < 6" and "> quote" survive.
    """
    text = html.unescape(str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = _TAG_RE.sub("", text)
    text = _CTRL_RE.sub("", text).replace("\t", " ").replace(" ", " ")
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r" *\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(data: bytes) -> str:
    """Text of a .md/.txt upload; UTF-8 (with or without BOM), UTF-16 with a BOM, else Windows-1252."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise DocumentError("This file could not be read as text.") from exc
    if b"\x00" in data:
        raise DocumentError("This does not look like a text file. Upload a .md or .txt file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _pdf_markdown(data: bytes, title: str) -> str:
    """Markdown of a PDF: "# <title>" then one "## Page N" section per page that has text."""
    if not pdf_supported():
        raise DocumentError("PDF upload needs the pypdf package. Upload a .md or .txt file instead.")
    if not data.startswith(b"%PDF"):
        raise DocumentError("This file is not a valid PDF.")
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("This PDF is password-protected. Upload an unprotected copy.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001  (pypdf raises many kinds of errors on broken files)
        raise DocumentError("This PDF could not be read.") from exc
    parts = [f"# {title}"]
    for number, text in enumerate(pages, start=1):
        # A line of PDF text must never turn into a Markdown heading.
        body = re.sub(r"(?m)^\s*#+\s*", "", text).strip()
        if body:
            parts.append(f"## Page {number}\n\n{body}")
    return "\n\n".join(parts)


def read_upload(filename: str, data: bytes) -> tuple[str, str]:
    """Validate one upload and return (stored name, cleaned text). Raises DocumentError."""
    raw_name = Path(str(filename or "").replace("\\", "/")).name
    suffix = Path(raw_name).suffix.lower()
    allowed = [f".{t}" for t in allowed_types()]
    if suffix not in allowed:
        shown = re.sub(r"[^a-z0-9.]", "", suffix)[:12] or "no extension"  # the UI renders Markdown
        raise DocumentError(f"Only {', '.join(allowed)} files can be uploaded (got {shown}).")
    data = bytes(data or b"")
    if not data:
        raise DocumentError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError(f"The file is too large ({_size_label(len(data))}). The limit is 2 MB.")
    name = stored_name(raw_name)
    text = clean_document_text(_pdf_markdown(data, title_for(name)) if suffix == PDF_SUFFIX else _decode(data))
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentError(f"The text is too long ({len(text):,} characters; the limit is {MAX_TEXT_CHARS:,}). "
                            "Split it into smaller files.")
    if not _text_documents(text, name):  # headings alone, or nothing but whitespace
        raise DocumentError("No readable text was found in this file (scanned PDFs are not supported).")
    return name, text


# ---------- chunks ----------

def _text_documents(text: str, name: str) -> list[Document]:
    """Sections of an upload, chunked exactly like the college documents."""
    from smart_campus_assistant import ingest

    return ingest.text_documents(
        text,
        source=name,
        doc_type=DOC_TYPE,
        fallback=title_for(name),
        plain=Path(name).suffix.lower() == ".txt",  # a "#" line in a text file is not a heading
    )


def _file_documents(path: Path) -> list[Document]:
    return _text_documents(path.read_text(encoding="utf-8"), path.name)


def document_chunks(name: str) -> list[Document]:
    """Chunks of one stored upload ([] when it does not exist)."""
    from smart_campus_assistant import ingest

    path = _existing_path(name)
    return ingest.split_documents(_file_documents(path)) if path else []


def all_upload_chunks() -> list[Document]:
    """Chunks of every stored upload; an unreadable file is skipped, never fatal."""
    from smart_campus_assistant import ingest

    chunks: list[Document] = []
    for path in upload_files():
        try:
            chunks += ingest.split_documents(_file_documents(path))
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Skipping uploaded document %s: %s", path.name, exc)
    return chunks


# ---------- list / save / delete ----------

def _existing_path(name: str) -> Path | None:
    """Path of an upload by its exact stored name; anything else (paths, college docs) -> None."""
    return next((p for p in upload_files() if p.name == str(name or "")), None)


def _info(path: Path) -> dict:
    from smart_campus_assistant import ingest

    stat = path.stat()
    try:
        sections = _file_documents(path)
        chunks = len(ingest.split_documents(sections))
    except (OSError, UnicodeDecodeError):
        sections, chunks = [], 0
    return {
        "name": path.name,
        "title": title_for(path.name),
        "kind": "Markdown" if path.suffix.lower() == ".md" else "Text",
        "size": stat.st_size,
        "size_label": _size_label(stat.st_size),
        # Stored in UTC like every other time in the app; pages show it in the college's time zone.
        "uploaded_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="minutes"),
        "sections": len(sections),
        "chunks": chunks,
    }


def list_documents() -> list[dict]:
    """Stored uploads, newest first, with size, section and chunk counts."""
    with _lock:
        infos = [_info(p) for p in upload_files()]
    return sorted(infos, key=lambda d: (d["uploaded_at"], d["name"]), reverse=True)


def college_documents() -> list[dict]:
    """The built-in college documents (read-only here) with their section and chunk counts."""
    from smart_campus_assistant import ingest

    chunks = ingest.split_documents(ingest.load_documents())
    counts: dict[str, list[int]] = {}
    for chunk in chunks:
        row = counts.setdefault(chunk.metadata["source"], [0, 0])
        row[0] = max(row[0], int(chunk.metadata["page"]))
        row[1] += 1
    return [{"name": name, "title": title_for(name), "sections": s, "chunks": c}
            for name, (s, c) in sorted(counts.items())]


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_document(filename: str, data: bytes) -> dict:
    """Validate and store one upload; returns its info. Raises DocumentError on bad input or a name clash."""
    name, text = read_upload(filename, data)
    if Path(name).stem in _college_stems():
        raise DocumentError(f'"{name}" has the same name as a built-in college document. Rename the file.')
    with _lock:
        existing = upload_files()
        if any(p.name == name for p in existing):
            raise DocumentError(f'"{name}" is already uploaded. Delete it first to replace it.')
        if len(existing) >= MAX_DOCUMENTS:
            raise DocumentError(f"There are already {MAX_DOCUMENTS} uploaded documents. Delete one first.")
        path = uploads_dir() / name
        _write_atomic(path, text)
        return _info(path)


def delete_document(name: str) -> bool:
    """Remove one stored upload. False when no upload has that exact name (college docs never match)."""
    with _lock:
        path = _existing_path(name)
        if path is None:
            return False
        path.unlink()
    return True


# ---------- search index ----------

def refresh_local_index() -> None:
    """Make offline (BM25) search pick up the change now instead of on its next file check."""
    try:
        from smart_campus_assistant import local_search

        local_search.reset_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not refresh the offline search index: %s", exc)


def index_document(name: str) -> tuple[bool, str]:
    """Add one upload's chunks to the vector store (ids = chunk_ids, so it is idempotent). Never raises."""
    try:
        from smart_campus_assistant import retriever

        chunks = document_chunks(name)
        if not chunks:
            return False, "The document has no text to index."
        store = retriever.get_vector_store()
        for i in range(0, len(chunks), INDEX_BATCH):
            batch = chunks[i : i + INDEX_BATCH]
            store.add_documents(batch, ids=[c.id for c in batch])
        return True, f"Indexed for search ({len(chunks)} chunk{'s' if len(chunks) != 1 else ''})."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Indexing uploaded document %s failed: %s", name, exc)
        return False, f"Keyword search has it, but adding it to the vector index failed: {type(exc).__name__}."


def unindex_document(name: str, chunk_ids: list[str]) -> tuple[bool, str]:
    """Delete an upload's chunk ids from the vector store. Never raises."""
    if not chunk_ids:
        # Never call delete with empty ids: AmazonS3Vectors.delete(ids=None) drops the whole index.
        return True, "Removed from search."
    try:
        from smart_campus_assistant import retriever

        store = retriever.get_vector_store()
        for i in range(0, len(chunk_ids), INDEX_BATCH):
            store.delete(ids=chunk_ids[i : i + INDEX_BATCH])
        return True, "Removed from search."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unindexing uploaded document %s failed: %s", name, exc)
        return False, f"Deleted, but removing it from the vector index failed: {type(exc).__name__}."


def publish_document(filename: str, data: bytes, live: bool = True) -> tuple[dict, bool, str]:
    """save_document + make it searchable. Raises DocumentError only for invalid input.

    ``live`` adds the chunks to the vector store as well; in demo mode offline search is all there is.
    """
    info = save_document(filename, data)
    refresh_local_index()
    if not live:
        return info, True, "Searchable now."
    indexed, message = index_document(info["name"])
    return info, indexed, message


def remove_document(name: str, live: bool = True) -> tuple[bool, bool, str]:
    """delete_document + remove it from search -> (deleted, unindexed, message)."""
    try:
        chunk_ids = [c.id for c in document_chunks(name) if c.id]  # read before the file is gone
    except (OSError, UnicodeDecodeError):
        chunk_ids = []
    if not delete_document(name):
        return False, False, f'"{name}" was not found among the uploaded documents.'
    refresh_local_index()
    if not live:
        return True, True, "Removed from search."
    unindexed, message = unindex_document(name, chunk_ids)
    return True, unindexed, message
