"""Keyword search (BM25) over the college documents, uploaded documents and admin notices.

Demo mode has no AWS, so it cannot use the S3 Vectors index: ``search`` (BM25 + the shared
rerank in ``ranking.py``) is its whole retriever. Live hybrid search uses ``keyword_search``
as its second ranking next to vector similarity. Both read the very same chunks a full ingest
embeds (``ingest.all_chunks()``: ``data/college/*.md``, ``data/uploads/`` and
``data/notices.json``), so offline answers quote the real documents and a new notice or upload
is searchable as soon as it is saved.

The index is rebuilt automatically when a document, an upload or the notices file changes
(checked by file name, size and modification time on every search, which costs a few ``stat`` calls).
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document

DEFAULT_K = 4
BM25_K1 = 1.5
BM25_B = 0.75
HEADING_BOOST = 1.5  # a query word in the chunk's heading path counts this much extra

# English and Hinglish words that carry no topic.
STOPWORDS = frozenset(
    "a an the of at in on is are was were be been for and or to from by with what which who whom when where why "
    "how much many does do did can could should would will shall may i me my mine we our you your it its this that "
    "these those there here any some about as if than then so not no yes please tell give show explain know want "
    "need get have has had per "
    "kya hai hain ka ki ke ko se mein me par pe aur ya kitna kitni kitne kaise kab kahan kaun kaunsa kaunsi kaunse "
    "mera meri mere mujhe mujhko hum hamara hamari batao bataiye bata do dijiye chahiye hota hoti hote hoga hogi "
    "tha thi the raha rahi rahe liye wala wali wale bhi nahi nahin toh to "
    "xyz college engineering".split()
)

# Hinglish / everyday words -> the word the documents use.
SYNONYMS = {
    "kitab": "book", "kitaab": "book", "kitabe": "book", "kitabein": "book",
    "pariksha": "exam", "exams": "exam", "examination": "exam", "examinations": "exam", "paper": "exam",
    "papers": "exam",
    "shulk": "fee", "fees": "fee", "charges": "fee", "charge": "fee", "cost": "fee", "kharcha": "fee",
    "jurmana": "fine", "penalty": "fine",
    "chhatravas": "hostel", "hostels": "hostel",
    "upasthiti": "attendance", "hazri": "attendance", "haazri": "attendance",
    "chhatravritti": "scholarship", "wazifa": "scholarship",
    "naukri": "placement", "job": "placement", "jobs": "placement", "package": "placement",
    "packages": "placement", "salary": "placement",
    "timing": "timings", "subjects": "course", "subject": "course", "courses": "course",
    "syllabus": "curriculum", "cs": "cse", "mech": "mechanical",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_lock = threading.Lock()
_index: _Index | None = None


def _stem(word: str) -> str:
    """Very light stemming: plural endings only (enough for fee/fees, book/books, rule/rules)."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lower-case topic words of ``text`` (stopwords dropped, synonyms mapped, plurals stemmed)."""
    out = []
    for raw in _TOKEN_RE.findall(str(text).lower()):
        if raw in STOPWORDS:
            continue
        word = SYNONYMS.get(raw) or _stem(raw)
        out.append(SYNONYMS.get(word, word))
    return out


class _Index:
    """BM25 statistics for one snapshot of the chunks."""

    def __init__(self, chunks: list[Document], signature: tuple):
        self.chunks = chunks
        self.signature = signature
        self.term_counts: list[Counter[str]] = []
        self.heading_terms: list[set[str]] = []
        self.lengths: list[int] = []
        doc_freq: Counter[str] = Counter()
        for chunk in chunks:
            heading, _, _ = chunk.page_content.partition("\n")
            terms = tokenize(chunk.page_content)
            counts = Counter(terms)
            self.term_counts.append(counts)
            self.heading_terms.append(set(tokenize(heading)))
            self.lengths.append(len(terms))
            doc_freq.update(counts.keys())
        n = len(chunks)
        self.avg_length = (sum(self.lengths) / n) if n else 0.0
        self.idf = {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()}

    def score(self, query_terms: list[str], i: int) -> float:
        counts, length = self.term_counts[i], self.lengths[i]
        norm = BM25_K1 * (1 - BM25_B + BM25_B * length / (self.avg_length or 1.0))
        total = 0.0
        for term in dict.fromkeys(query_terms):
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = self.idf.get(term, 0.0)
            total += idf * tf * (BM25_K1 + 1) / (tf + norm)
            if term in self.heading_terms[i]:
                total += idf * HEADING_BOOST
        return total


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def _signature() -> tuple:
    """Changes whenever a college document, an upload or the notices file is added, removed or edited.

    The file list comes from ``ingest.source_files()``, so the index always covers the folders
    ingest reads (tests that point ingest at a temp folder get a matching index).
    """
    from smart_campus_assistant import ingest, notices

    files = tuple((str(p), _mtime(p), _size(p)) for p in ingest.source_files())
    notices_path = notices.notices_file()
    return (str(ingest.get_settings().data_dir), files, str(notices_path), _mtime(notices_path), _size(notices_path))


def _get_index() -> _Index:
    global _index
    signature = _signature()
    with _lock:
        if _index is None or _index.signature != signature:
            from smart_campus_assistant import ingest

            _index = _Index(ingest.all_chunks(), signature)
        return _index


def reset_cache() -> None:
    """Forget the index (tests use this after pointing settings at another folder)."""
    global _index
    with _lock:
        _index = None


def chunk_count() -> int:
    """Number of indexed chunks (0 when the documents folder is missing or empty)."""
    return len(_get_index().chunks)


def keyword_search(query: str, limit: int, college: str | None = None) -> list[tuple[Document, float]]:
    """Raw BM25 ranking: [(chunk, score)] best first, at most ``limit``; chunks sharing no topic word are left out.

    The "(CSE ...)" branch hint is not searched for: as query words it would drown the real
    question. The rerank uses it instead.
    """
    from smart_campus_assistant.ranking import split_scope

    index = _get_index()
    terms = tokenize(split_scope(query)[0])
    if not terms or limit <= 0:
        return []
    scored = []
    for i, chunk in enumerate(index.chunks):
        if college and chunk.metadata.get("college") != college:
            continue
        score = index.score(terms, i)
        if score > 0:
            scored.append((score, i))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [(index.chunks[i], score) for score, i in scored[:limit]]


def search(query: str, k: int | None = None, college: str | None = None) -> list[Document]:
    """Best-matching chunks for ``query``, best first: BM25 candidates, then the shared rerank.

    ``college`` is accepted for parity with ``retriever.search`` and filters on metadata.
    """
    from smart_campus_assistant import ranking

    k = k or DEFAULT_K
    return ranking.rerank(query, keyword_search(query, ranking.candidate_count(k), college), k)
