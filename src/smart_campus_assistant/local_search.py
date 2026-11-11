"""Offline keyword search (BM25) over the real college documents and admin notices.

Demo mode has no AWS, so it cannot use the S3 Vectors index. This module gives it a
small lexical index over the very same chunks a full ingest would embed
(``ingest.all_chunks()``: ``data/college/*.md`` plus ``data/notices.json``), so offline
answers quote the real documents and a notice is searchable as soon as it is saved.

The index is rebuilt automatically when a document or the notices file changes
(checked by file modification time on every search, which costs a few ``stat`` calls).
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document

from smart_campus_assistant.config import BRANCHES, get_settings

DEFAULT_K = 4
BM25_K1 = 1.5
BM25_B = 0.75
HEADING_BOOST = 1.5  # a query word in the chunk's heading path counts this much extra
SCOPE_BOOST = 1.25  # a chunk that names the student's own branch ranks this much higher

# "question (CSE Computer Science and Engineering)": the branch hint personal.branch_scoped_query adds.
_SCOPE_RE = re.compile(r"\s*\(([A-Z]{2,3}) [^()]*\)\s*$")
_BRANCH_CODE_RE = re.compile(r"\b(" + "|".join(BRANCHES) + r")\b")

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
        self.branches: list[frozenset[str]] = []  # branch codes named in each chunk (case-sensitive)
        doc_freq: Counter[str] = Counter()
        for chunk in chunks:
            heading, _, _ = chunk.page_content.partition("\n")
            terms = tokenize(chunk.page_content)
            counts = Counter(terms)
            self.term_counts.append(counts)
            self.heading_terms.append(set(tokenize(heading)))
            self.lengths.append(len(terms))
            self.branches.append(frozenset(_BRANCH_CODE_RE.findall(chunk.page_content)))
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


def _signature() -> tuple:
    """Changes whenever a college document or the notices file is added, removed or edited."""
    from smart_campus_assistant import notices

    data_dir = Path(get_settings().data_dir)
    docs = tuple((p.name, _mtime(p)) for p in sorted(data_dir.glob("*.md")))
    notices_path = notices.notices_file()
    return (str(data_dir), docs, str(notices_path), _mtime(notices_path))


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


def search(query: str, k: int | None = None, college: str | None = None) -> list[Document]:
    """Best-matching chunks for ``query``, best first. Chunks that share no topic word are left out.

    ``college`` is accepted for parity with ``retriever.search`` and filters on metadata.
    """
    index = _get_index()
    # The branch hint only breaks ties; as query words it would drown the real question.
    scope = None
    hint = _SCOPE_RE.search(query)
    if hint and hint.group(1) in BRANCHES:
        scope, query = hint.group(1), query[: hint.start()]
    terms = tokenize(query)
    if not terms:
        return []
    scored = []
    for i, chunk in enumerate(index.chunks):
        if college and chunk.metadata.get("college") != college:
            continue
        score = index.score(terms, i)
        if score > 0:
            if scope and scope in index.branches[i]:
                score *= SCOPE_BOOST
            scored.append((score, i))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [index.chunks[i] for _, i in scored[: k or DEFAULT_K]]
