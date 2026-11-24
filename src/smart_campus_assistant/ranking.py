"""Hybrid retrieval helpers: reciprocal rank fusion and a cheap, deterministic rerank.

Live search fuses two rankings of the same chunks: S3 Vectors similarity (good at paraphrases)
and BM25 (good at exact words, numbers and codes such as "CS301"). Reciprocal rank fusion only
uses ranks, so the two very different score scales never need to be calibrated; a chunk both
retrievers like rises to the top.

The rerank then adjusts that order with signals that cost no model call:

- coverage: the share of the question's topic words found in the chunk, and in its heading path
  (a chunk that matches every word beats one that repeats a single word);
- branch: a chunk naming the asked-about branch (or the signed-in student's branch hint) ranks
  higher, one that names only other branches lower;
- near-duplicates: a chunk whose words are almost all in a better chunk is dropped, so the
  k slots go to different facts (stale copies, overlapping pieces).

Demo mode runs BM25 + this same rerank, fully offline.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from langchain_core.documents import Document

from smart_campus_assistant.config import BRANCHES

RRF_K = 60  # the usual constant: dampens the gap between rank 1 and rank 2
MIN_CANDIDATES = 12
CANDIDATES_PER_RESULT = 4
MAX_CANDIDATES = 30

COVERAGE_WEIGHT = 0.5  # all topic words in the chunk -> score x1.5
HEADING_WEIGHT = 0.25  # all topic words in the heading path -> a further +0.25
BRANCH_MATCH = 1.2
BRANCH_MISMATCH = 0.8
# Drop a chunk when this share of its raw words (stopwords and branch codes included, so the
# ME and CE intros stay apart) is already in a better chunk: stale copies and repeated text.
DUPLICATE_SHARE = 0.95
SECTION_MAX_CHARS = 300

_WORD_RE = re.compile(r"[a-z0-9]+")

# "question (CSE Computer Science and Engineering)": the branch hint personal.branch_scoped_query adds.
_SCOPE_RE = re.compile(r"\s*\(([A-Z]{2,3}) [^()]*\)\s*$")
# Branch codes as the documents write them (case-sensitive: "me" is also a Hinglish word).
_BRANCH_CODE_RE = re.compile(r"\b(" + "|".join(sorted(BRANCHES, key=len, reverse=True)) + r")\b")
# Lower-case ways a question names a branch.
BRANCH_WORDS: dict[str, tuple[str, ...]] = {
    "CSE": ("cse", "cs", "computer science"),
    "ECE": ("ece", "electronics"),
    "ME": ("mechanical", "mech"),
    "CE": ("civil",),
}


def candidate_count(k: int) -> int:
    """How many results each retriever returns before fusion and rerank."""
    return min(max(k * CANDIDATES_PER_RESULT, MIN_CANDIDATES), MAX_CANDIDATES)


def doc_key(doc: Document) -> str:
    """Identity of a chunk across retrievers: its chunk_id (both stores use it as the id)."""
    key = doc.metadata.get("chunk_id") or doc.id
    if key:
        return str(key)
    return "text:" + hashlib.sha1(doc.page_content.encode("utf-8")).hexdigest()


def reciprocal_rank_fusion(rankings: Sequence[Sequence[Document]], rrf_k: int = RRF_K) -> list[tuple[Document, float]]:
    """[(doc, fused score)] best first; score = sum over rankings of 1 / (rrf_k + rank).

    The first ranking that contains a chunk supplies its Document; ties keep first-seen order.
    """
    scores: dict[str, float] = {}
    docs: dict[str, Document] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, doc in enumerate(ranking, start=1):
            key = doc_key(doc)
            if key in seen:  # one list counts a chunk once
                continue
            seen.add(key)
            docs.setdefault(key, doc)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
    order = sorted(scores, key=lambda key: -scores[key])
    return [(docs[key], scores[key]) for key in order]


def split_scope(query: str) -> tuple[str, str | None]:
    """("question", "CSE") for a query ending in the "(CSE Computer Science and Engineering)" hint."""
    hint = _SCOPE_RE.search(query or "")
    if hint and hint.group(1) in BRANCHES:
        return query[: hint.start()], hint.group(1)
    return query or "", None


def query_branches(text: str) -> set[str]:
    """Branch codes a question names: upper-case codes ("ME") or words ("cse", "civil")."""
    found = set(_BRANCH_CODE_RE.findall(text or ""))
    lowered = (text or "").lower()
    for code, words in BRANCH_WORDS.items():
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words):
            found.add(code)
    return found


def chunk_branches(doc: Document) -> set[str]:
    """Branch codes written in a chunk."""
    return set(_BRANCH_CODE_RE.findall(doc.page_content))


def section_of(doc: Document) -> str | None:
    """Heading path of a chunk: metadata["section"], else the first line of a multi-line chunk."""
    section = doc.metadata.get("section")
    if section:
        return str(section)[:SECTION_MAX_CHARS]
    first, sep, _ = doc.page_content.partition("\n")
    first = first.strip()
    return first[:SECTION_MAX_CHARS] if sep and first else None


def _branch_factor(wanted: set[str], named: set[str]) -> float:
    if not wanted or not named:
        return 1.0
    return BRANCH_MATCH if wanted & named else BRANCH_MISMATCH


def raw_words(text: str) -> set[str]:
    """Every lower-case word and number of ``text`` (no stopwords dropped, no stemming)."""
    return set(_WORD_RE.findall(text.lower()))


def is_near_duplicate(words: set[str], better: set[str]) -> bool:
    """True when almost every raw word of a chunk is already in a better-ranked chunk."""
    return bool(words) and len(words & better) >= DUPLICATE_SHARE * len(words)


def rerank(query: str, candidates: Sequence[tuple[Document, float]], k: int) -> list[Document]:
    """Top ``k`` of ``candidates`` ([(doc, first-stage score)], any scale) after the rerank above."""
    from smart_campus_assistant.local_search import tokenize  # local_search imports this module lazily

    if not candidates or k <= 0:
        return []
    text, scope = split_scope(query)
    terms = set(tokenize(text))
    wanted = query_branches(text) | ({scope} if scope else set())
    top = max(score for _, score in candidates) or 1.0
    rows = []
    for position, (doc, score) in enumerate(candidates):
        factor = 1.0
        if terms:
            factor += COVERAGE_WEIGHT * len(terms & set(tokenize(doc.page_content))) / len(terms)
            factor += HEADING_WEIGHT * len(terms & set(tokenize(section_of(doc) or ""))) / len(terms)
        factor *= _branch_factor(wanted, chunk_branches(doc))
        rows.append((max(score, 0.0) / top * factor, position, doc))
    rows.sort(key=lambda row: (-row[0], row[1]))
    kept: list[Document] = []
    kept_words: list[set[str]] = []
    for _, _, doc in rows:
        words = raw_words(doc.page_content)
        if any(is_near_duplicate(words, better) for better in kept_words):
            continue
        kept.append(doc)
        kept_words.append(words)
        if len(kept) == k:
            break
    return kept
