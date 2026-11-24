"""Vector store access, hybrid search with an optional college filter, and prompt/citation helpers.

``search`` (SEARCH_MODE=hybrid, the default) fuses S3 Vectors similarity with BM25 keyword
search over the same chunks (reciprocal rank fusion), then applies the cheap deterministic
rerank in ``ranking.py``. SEARCH_MODE=vector keeps plain similarity search.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore, VectorStore

from smart_campus_assistant import models, ranking
from smart_campus_assistant.config import COLLEGE_ALIASES, COLLEGES, get_settings
from smart_campus_assistant.state import Citation

logger = logging.getLogger(__name__)

# Stored in S3 Vectors metadata but not filterable (saves the 2 KB filterable budget).
# Only applied when an index is created: an older index keeps "section" filterable, which fits the budget.
PAGE_CONTENT_KEY = "_page_content"
NON_FILTERABLE_KEYS = [PAGE_CONTENT_KEY, "source", "chunk_id", "section"]

SNIPPET_CHARS = 200


@lru_cache
def get_vector_store() -> VectorStore:
    """Return the configured vector store (cached; clear with reset_vector_store_cache)."""
    settings = get_settings()
    if settings.vector_backend == "memory":
        return InMemoryVectorStore(models.get_embeddings())

    from langchain_aws.vectorstores import AmazonS3Vectors

    return AmazonS3Vectors(
        vector_bucket_name=settings.vector_bucket_name,
        index_name=settings.vector_index_name,
        distance_metric="cosine",
        # Only applied when the index is created; college/doc_type/page stay filterable.
        non_filterable_metadata_keys=NON_FILTERABLE_KEYS,
        page_content_metadata_key=PAGE_CONTENT_KEY,
        create_index_if_not_exist=True,
        embedding=models.get_embeddings(),
        region_name=settings.aws_region,
    )


def reset_vector_store_cache() -> None:
    """Drop the cached store (tests use this after changing settings or embeddings)."""
    get_vector_store.cache_clear()


# Hinglish filler words that add noise to the query embedding.
_FILLER_WORDS = {"me", "mein", "ka", "ki", "ke", "kya", "hai", "hain", "kitni", "kitna", "kitne", "batao", "bataiye", "kaise", "aur"}


def clean_query(query: str, college: str | None = None) -> str:
    """Remove noise from the query before embedding.

    The college name only pulls the embedding towards generic pages (e.g. the
    "about" intro), so drop it. With a filter only that college's aliases are
    dropped; with one indexed college (the usual case) its aliases always are.
    """
    text = f" {query.lower()} "
    keys = [college] if college else (list(COLLEGES) if len(COLLEGES) == 1 else [])
    for key in keys:
        for alias in sorted(COLLEGE_ALIASES.get(key, ()), key=len, reverse=True):
            text = text.replace(alias, " ")
    words = [w for w in re.findall(r"[\w%./-]+", text) if w not in _FILLER_WORDS]
    cleaned = " ".join(words).strip()
    return cleaned or query


def vector_search(query: str, k: int, college: str | None = None) -> list[Document]:
    """Similarity search over the vector store; `college` is an optional college key such as "xyz"."""
    store = get_vector_store()
    query = clean_query(query, college)
    if not college:
        return store.similarity_search(query, k=k)
    if isinstance(store, InMemoryVectorStore):
        # The in-memory store takes a predicate over Documents.
        return store.similarity_search(
            query, k=k, filter=lambda doc: doc.metadata.get("college") == college
        )
    # S3 Vectors uses a Mongo-style filter on filterable metadata keys.
    return store.similarity_search(query, k=k, filter={"college": {"$eq": college}})


def keyword_search(query: str, k: int, college: str | None = None) -> list[Document]:
    """BM25 over the local copies of the indexed chunks (docs, uploads, notices), best first."""
    from smart_campus_assistant import local_search

    return [doc for doc, _ in local_search.keyword_search(query, k, college)]


def hybrid_search(query: str, k: int, college: str | None = None) -> list[Document]:
    """Vector + BM25 candidates, fused by reciprocal rank, then reranked; top k.

    Either side may fail on its own (no local documents, a vector store hiccup): the other side
    still answers. Only when the vector store fails and BM25 finds nothing does the error surface.
    """
    n = ranking.candidate_count(k)
    try:
        keyword_docs = keyword_search(query, n, college)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Keyword search failed, using vector search only: %s", exc)
        keyword_docs = []
    try:
        vector_docs = vector_search(query, n, college)
    except Exception as exc:
        if not keyword_docs:
            raise
        logger.warning("Vector search failed, using keyword search only: %s", exc)
        vector_docs = []
    # Keyword results first: for a chunk in both lists the fresh local copy is kept.
    fused = ranking.reciprocal_rank_fusion([keyword_docs, vector_docs])
    return ranking.rerank(query, fused, k)


def search(query: str, k: int | None = None, college: str | None = None) -> list[Document]:
    """Best chunks for `query` (hybrid or plain vector search, see SEARCH_MODE); `college` filters by key."""
    settings = get_settings()
    k = k or settings.retriever_k
    if settings.search_mode == "vector":
        return vector_search(query, k, college)
    return hybrid_search(query, k, college)


def _label(doc: Document) -> str:
    meta = doc.metadata
    college = COLLEGES.get(meta.get("college", ""), meta.get("college", "unknown"))
    page = meta.get("page")
    page_part = f" p.{page}" if page is not None else ""
    return f"{college} | {meta.get('doc_type', 'other')} | {meta.get('source', 'unknown')}{page_part}"


def format_docs(docs: list[Document]) -> str:
    """Numbered context block for prompts: "[1] (XYZ College of Engineering | fees | fee_structure.md p.2)\\n<text>"."""
    if not docs:
        return "No relevant documents found."
    return "\n\n".join(
        f"[{i}] ({_label(doc)})\n{doc.page_content.strip()}" for i, doc in enumerate(docs, 1)
    )


def _snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut + "..."


def _body(doc: Document, section: str | None) -> str:
    """Chunk text without its heading line, which the citation already shows as the section."""
    first, sep, rest = doc.page_content.partition("\n")
    return rest if sep and section and first.strip() == section.strip() and rest.strip() else doc.page_content


def to_citation(doc: Document) -> Citation:
    """Turn a retrieved chunk into a Citation: document, section number, heading path and a short snippet."""
    meta = doc.metadata
    page = meta.get("page")
    section = ranking.section_of(doc)
    return Citation(
        source=meta.get("source", "unknown"),
        college=meta.get("college", "unknown"),
        page=int(page) if page is not None else None,
        snippet=_snippet(_body(doc, section)),
        section=section,
    )
