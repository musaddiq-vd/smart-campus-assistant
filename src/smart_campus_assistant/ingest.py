"""Load the college markdown docs, split them into chunks with metadata, and index them.

Each markdown file is split by headings into sections (section number = "page"),
then each section into ~800-char chunks. Every chunk starts with its heading path
(e.g. "Fee Structure 2026-27 > Late fee") so it keeps its context when retrieved.

CLI: ``python -m smart_campus_assistant.ingest [--dry-run] [--reset]``
"""

from __future__ import annotations

import argparse
import logging
import re
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from smart_campus_assistant.config import COLLEGE_KEY, get_settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
BATCH_SIZE = 50
MIN_SECTION_CHARS = 20

HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]

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


def doc_type_for(filename: str) -> str:
    """Map a file name (or stem) such as "fee_structure.md" to its doc_type; unknown -> "other"."""
    return DOC_TYPES.get(Path(filename).stem.lower(), "other")


def clean_text(text: str) -> str:
    """Collapse runs of spaces/tabs and blank lines; keep single newlines."""
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def heading_path(metadata: dict, fallback: str) -> str:
    """"H1 > H2 > H3" from MarkdownHeaderTextSplitter metadata."""
    parts = [metadata[key] for _, key in HEADERS if metadata.get(key)]
    return " > ".join(parts) if parts else fallback


def load_documents(data_dir: Path | None = None) -> list[Document]:
    """One Document per markdown section; `page` is the 1-based section number in its file.

    The heading path is kept in metadata["section"] and prepended to chunks by split_documents.
    """
    data_dir = Path(data_dir or get_settings().data_dir)
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADERS, strip_headers=True)
    docs: list[Document] = []
    files = sorted(data_dir.glob("*.md"))
    if not files:
        logger.warning("No markdown files found in %s", data_dir)
    for path in files:
        source = path.name
        doc_type = doc_type_for(source)
        if doc_type == "other":
            logger.info("%s: no doc_type mapping, using 'other'", source)
        text = path.read_text(encoding="utf-8")
        section_no = 0
        for section in splitter.split_text(text):
            body = clean_text(section.page_content)
            if len(body) < MIN_SECTION_CHARS:
                continue
            section_no += 1
            docs.append(
                Document(
                    page_content=body,
                    metadata={
                        "college": COLLEGE_KEY,
                        "doc_type": doc_type,
                        "source": source,
                        "page": section_no,
                        "section": heading_path(section.metadata, path.stem),
                    },
                )
            )
        if section_no == 0:
            logger.warning("Skipping %s: no text sections", source)
    return docs


def split_documents(docs: list[Document]) -> list[Document]:
    """Split sections into chunks of at most CHUNK_SIZE chars, each prefixed with its heading path."""
    chunks: list[Document] = []
    for doc in docs:
        meta = doc.metadata
        prefix = f"{meta.get('section') or Path(meta['source']).stem}\n"
        body_size = max(CHUNK_SIZE - len(prefix), 200)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=body_size, chunk_overlap=min(CHUNK_OVERLAP, body_size // 4)
        )
        n = 0
        for piece in splitter.split_text(doc.page_content):
            piece = clean_text(piece)
            if not piece:
                continue
            text = (prefix + piece)[:CHUNK_SIZE]
            chunk_id = f"{meta['source']}:s{meta['page']}:c{n}"
            n += 1
            chunks.append(
                Document(
                    id=chunk_id,
                    page_content=text,
                    metadata={
                        "college": meta["college"],
                        "doc_type": meta["doc_type"],
                        "source": meta["source"],
                        "page": int(meta["page"]),
                        "chunk_id": chunk_id,
                    },
                )
            )
    return chunks


def notice_chunks() -> list[Document]:
    """Chunks for the admin notices (empty if the notices file is missing or unreadable)."""
    try:
        from smart_campus_assistant import notices

        return notices.all_notice_documents()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Skipping notices: %s", exc)
        return []


def all_chunks() -> list[Document]:
    """Markdown doc chunks plus the admin notices, as indexed by a full ingest."""
    return split_documents(load_documents()) + notice_chunks()


def _print_file_summary(chunks: list[Document]) -> None:
    for source, count in sorted(Counter(c.metadata["source"] for c in chunks).items()):
        print(f"  {source}: {count} chunks")


def ingest(reset: bool = False) -> int:
    """Load, split and add all chunks (docs + admin notices) to the vector store. Returns the chunk count.

    Ids are chunk_ids, so re-running overwrites instead of duplicating.
    `reset` deletes the existing chunks with those ids first.
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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.dry_run:
        dry_run()
    else:
        ingest(reset=args.reset)


if __name__ == "__main__":
    main()
