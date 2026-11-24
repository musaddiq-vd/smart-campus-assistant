"""Load the college markdown docs (plus admin uploads and notices), split them into chunks, and index them.

Heading-level chunking: every file is first cut at its headings (``#`` to ``###``) into sections,
so one rule ("Fee Structure 2026-27 > Late fee") stays together in one chunk. The section number is
the chunk's ``page``; its heading path is the first line of the chunk text (so the chunk keeps its
context when retrieved) and ``metadata["section"]`` (so citations can name it). Only a section longer
than CHUNK_SIZE is split further, preferring sub-headings, blank lines, lines and then sentences,
with CHUNK_OVERLAP characters of overlap; a table cut this way repeats its header row in every piece.

The vector store (this module), offline BM25 (local_search.py) and hybrid search all use these
same chunks, so the two retrievers can be fused by chunk id.

CLI: ``python -m smart_campus_assistant.ingest [--dry-run] [--reset] [--prune]``
"""

from __future__ import annotations

import argparse
import logging
import re
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from smart_campus_assistant.config import COLLEGE_KEY, get_settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1500  # hard cap per chunk, heading line included; a section that fits stays whole
PIECE_SIZE = 900  # target size of the pieces of a longer section
CHUNK_OVERLAP = 150
BATCH_SIZE = 50
MIN_SECTION_CHARS = 20
HEADING_LEVELS = 3  # "#" to "###" start a section; deeper headings stay inside their section
HEADING_MAX_CHARS = 120  # one heading title
PATH_MAX_CHARS = 300  # the whole "A > B > C" path

# Where a long section may be cut, best first. Deeper headings come first so sub-rules stay whole.
SEPARATORS = ["\n#### ", "\n##### ", "\n###### ", "\n\n", "\n", ". ", " ", ""]

# File stem -> short doc_type stored in metadata (filterable in S3 Vectors).
DOC_TYPES: dict[str, str] = {
    "fee_structure": "fees",
    "exam_notifications": "exams",
    "academic_rules": "rules",
    "hostel_rules": "hostel",
    "library_rules": "library",
    "branches_and_programmes": "branches",
    "scholarships": "scholarships",
    "placements_2026": "placements",
    "about_and_admissions": "admissions",
    "important_dates_and_contacts": "calendar",
    "notices": "notices",  # admin notices from data/notices.json (see notices.py)
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_RULE_RE = re.compile(r"^\|?[\s:|-]+\|?$")


def doc_type_for(filename: str) -> str:
    """Map a file name (or stem) such as "fee_structure.md" to its doc_type; unknown -> "other"."""
    return DOC_TYPES.get(Path(filename).stem.lower(), "other")


def clean_text(text: str) -> str:
    """Collapse runs of spaces/tabs and blank lines; keep single newlines and paragraph breaks."""
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _heading_title(raw: str) -> str:
    title = " ".join(raw.replace("*", "").replace("`", "").split())
    return title[:HEADING_MAX_CHARS].rstrip()


def split_sections(text: str, fallback: str, plain: bool = False) -> list[tuple[str, str]]:
    """[(heading path, cleaned body)] in file order, empty sections included.

    Text before the first heading (and all of a ``plain`` text file) gets ``fallback`` as its path.
    Lines inside code fences are never headings.
    """
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []  # (level, title) of the headings above the current line
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        path = " > ".join(title for _, title in stack if title) or fallback
        sections.append((path[:PATH_MAX_CHARS].rstrip(), clean_text("\n".join(lines))))
        lines.clear()

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if (plain or in_fence) else _HEADING_RE.match(line)
        if match and len(match.group(1)) <= HEADING_LEVELS:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _heading_title(match.group(2))))
        else:
            lines.append(line)
    flush()
    return sections


def text_documents(text: str, *, source: str, doc_type: str, fallback: str, plain: bool = False) -> list[Document]:
    """One Document per non-empty section of one file; `page` is the 1-based section number.

    The heading path is kept in metadata["section"] and prepended to chunks by split_documents.
    """
    docs: list[Document] = []
    for section, body in split_sections(text, fallback, plain=plain):
        if len(body) < MIN_SECTION_CHARS:
            continue
        docs.append(
            Document(
                page_content=body,
                metadata={
                    "college": COLLEGE_KEY,
                    "doc_type": doc_type,
                    "source": source,
                    "page": len(docs) + 1,
                    "section": section,
                },
            )
        )
    return docs


def load_documents(data_dir: Path | None = None) -> list[Document]:
    """One Document per section of every top-level ``*.md`` file in the college data folder."""
    data_dir = Path(data_dir or get_settings().data_dir)
    docs: list[Document] = []
    files = sorted(data_dir.glob("*.md"))
    if not files:
        logger.warning("No markdown files found in %s", data_dir)
    for path in files:
        doc_type = doc_type_for(path.name)
        if doc_type == "other":
            logger.info("%s: no doc_type mapping, using 'other'", path.name)
        sections = text_documents(path.read_text(encoding="utf-8"), source=path.name, doc_type=doc_type,
                                  fallback=path.stem)
        if not sections:
            logger.warning("Skipping %s: no text sections", path.name)
        docs.extend(sections)
    return docs


def _table_header_before(body: str, offset: int) -> str | None:
    """Header row + rule row of the table that `offset` falls inside (None when it is not in a table)."""
    lines = body[:offset].split("\n")
    header = None
    for i in range(len(lines) - 1):
        if lines[i].startswith("|") and _TABLE_RULE_RE.match(lines[i + 1]) and "-" in lines[i + 1]:
            header = f"{lines[i]}\n{lines[i + 1]}"
        elif not lines[i].startswith("|"):
            header = None  # a non-table line ends the table above it
    if lines and lines[-1] and not lines[-1].startswith("|"):
        return None
    return header


def section_pieces(body: str, room: int) -> list[str]:
    """The body as one piece when it fits in `room` characters, else overlapping pieces of ~PIECE_SIZE."""
    body = body.strip()
    if len(body) <= room:
        return [body] if body else []
    size = min(PIECE_SIZE, room)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size, chunk_overlap=min(CHUNK_OVERLAP, size // 4), separators=SEPARATORS
    )
    pieces: list[str] = []
    start = 0
    for raw in splitter.split_text(body):
        piece = raw.strip()
        if not piece:
            continue
        found = body.find(piece, start)
        start = found + 1 if found >= 0 else start
        if pieces and piece.startswith("|") and found > 0:
            header = _table_header_before(body, found)
            if header and not piece.startswith(header) and len(header) + 1 + len(piece) <= room:
                piece = f"{header}\n{piece}"
        pieces.append(piece)
    return pieces


def split_documents(docs: list[Document]) -> list[Document]:
    """Turn sections into chunks of at most CHUNK_SIZE chars, each starting with its heading path."""
    chunks: list[Document] = []
    for doc in docs:
        meta = doc.metadata
        section = meta.get("section") or Path(meta["source"]).stem
        prefix = f"{section}\n"
        room = max(CHUNK_SIZE - len(prefix), 200)
        for n, piece in enumerate(section_pieces(clean_text(doc.page_content), room)):
            chunk_id = f"{meta['source']}:s{meta['page']}:c{n}"
            chunks.append(
                Document(
                    id=chunk_id,
                    page_content=(prefix + piece)[:CHUNK_SIZE],
                    metadata={
                        "college": meta["college"],
                        "doc_type": meta["doc_type"],
                        "source": meta["source"],
                        "page": int(meta["page"]),
                        "chunk_id": chunk_id,
                        "section": section,
                    },
                )
            )
    return chunks


def with_sections(docs: list[Document]) -> list[Document]:
    """Fill a missing metadata["section"] from the chunk's first line (notice chunks start with their title)."""
    for doc in docs:
        first, sep, _ = doc.page_content.partition("\n")
        if sep and not doc.metadata.get("section"):
            doc.metadata["section"] = first.strip()[:PATH_MAX_CHARS]
    return docs


def notice_chunks() -> list[Document]:
    """Chunks for the admin notices (empty if the notices file is missing or unreadable)."""
    try:
        from smart_campus_assistant import notices

        return with_sections(notices.all_notice_documents())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Skipping notices: %s", exc)
        return []


def upload_chunks() -> list[Document]:
    """Chunks for the documents admins uploaded (empty if the uploads folder is missing or unreadable)."""
    try:
        from smart_campus_assistant import documents

        return documents.all_upload_chunks()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Skipping uploaded documents: %s", exc)
        return []


def source_files() -> list[Path]:
    """Every file the chunks come from except the notices file: college docs, then uploads.

    local_search uses it (with modification times) to notice when its index is stale.
    """
    files = sorted(Path(get_settings().data_dir).glob("*.md"))
    try:
        from smart_campus_assistant import documents

        files += documents.upload_files()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list uploaded documents: %s", exc)
    return files


def all_chunks() -> list[Document]:
    """College doc chunks, uploaded documents and the admin notices, as indexed by a full ingest."""
    return split_documents(load_documents()) + upload_chunks() + notice_chunks()


def _print_file_summary(chunks: list[Document]) -> None:
    for source, count in sorted(Counter(c.metadata["source"] for c in chunks).items()):
        print(f"  {source}: {count} chunks")


def stored_ids(store) -> list[str] | None:
    """Every vector id in the store, or None when the store cannot list them.

    S3 Vectors is listed page by page through its boto3 client; the in-memory store is a dict.
    """
    inner = getattr(store, "store", None)
    if isinstance(inner, dict):
        return list(inner)
    client = getattr(store, "client", None)
    if client is None or not hasattr(client, "list_vectors"):
        return None
    ids: list[str] = []
    token = None
    while True:
        params = {"vectorBucketName": store.vector_bucket_name, "indexName": store.index_name, "maxResults": 500}
        if token:
            params["nextToken"] = token
        page = client.list_vectors(**params)
        ids += [v["key"] for v in page.get("vectors", [])]
        token = page.get("nextToken")
        if not token:
            return ids


def prune(store, keep: set[str]) -> int:
    """Delete vectors whose ids a full ingest no longer produces (e.g. pieces of an older chunking).

    Returns the number deleted. Never calls delete with no ids: that would drop a whole S3 index.
    """
    existing = stored_ids(store)
    if existing is None:
        print("This vector store cannot list its ids; nothing pruned.")
        return 0
    stale = [i for i in existing if i not in keep]
    for i in range(0, len(stale), BATCH_SIZE):
        store.delete(ids=stale[i : i + BATCH_SIZE])
    return len(stale)


def ingest(reset: bool = False, prune_stale: bool = False) -> int:
    """Load, split and add all chunks (docs, uploads, notices) to the vector store. Returns the chunk count.

    Ids are chunk_ids, so re-running overwrites instead of duplicating.
    `reset` deletes the existing chunks with those ids first; `prune_stale` then deletes every
    other vector in the index (left over from an older chunking or a deleted file).
    """
    from smart_campus_assistant import retriever

    chunks = all_chunks()
    store = retriever.get_vector_store()
    ids = [c.metadata["chunk_id"] for c in chunks]
    if reset and ids:
        for i in range(0, len(ids), BATCH_SIZE):
            store.delete(ids=ids[i : i + BATCH_SIZE])
    for i in range(0, len(chunks), BATCH_SIZE):
        store.add_documents(chunks[i : i + BATCH_SIZE], ids=ids[i : i + BATCH_SIZE])
    print(f"Indexed {len(chunks)} chunks:")
    _print_file_summary(chunks)
    if prune_stale and ids:
        print(f"Pruned {prune(store, set(ids))} stale vectors.")
    return len(chunks)


def dry_run() -> int:
    """Load and split only (no embeddings, no AWS); print counts."""
    chunks = all_chunks()
    by_type = Counter(c.metadata["doc_type"] for c in chunks)
    print(f"Dry run: {len(chunks)} chunks")
    for doc_type, count in sorted(by_type.items()):
        print(f"  {doc_type:<13} {count}")
    print("Per file:")
    _print_file_summary(chunks)
    return len(chunks)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest the college markdown docs into the vector store.")
    parser.add_argument("--dry-run", action="store_true", help="load and split only, no AWS")
    parser.add_argument("--reset", action="store_true", help="delete existing chunk ids first")
    parser.add_argument("--prune", action="store_true",
                        help="afterwards delete vectors no longer produced (e.g. after a chunking change)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.dry_run:
        dry_run()
    else:
        ingest(reset=args.reset, prune_stale=args.prune)


if __name__ == "__main__":
    main()
