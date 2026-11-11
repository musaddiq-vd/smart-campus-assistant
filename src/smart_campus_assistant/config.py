"""Central settings, loaded from environment variables and the project's .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# One fictional college. All data about it is synthetic.
COLLEGE_KEY = "xyz"
COLLEGE_NAME = "XYZ College of Engineering"

# Kept as a dict so retriever labels and filters work unchanged.
COLLEGES: dict[str, str] = {COLLEGE_KEY: COLLEGE_NAME}

# Lower-case ways people name the college in questions (dropped from search queries).
COLLEGE_ALIASES: dict[str, tuple[str, ...]] = {
    COLLEGE_KEY: ("xyz college of engineering", "xyz college", "xyz"),
}

# Branch code -> display name. Student IDs embed the code, e.g. XYZ24CSE001.
BRANCHES: dict[str, str] = {
    "CSE": "Computer Science and Engineering",
    "ECE": "Electronics and Communication Engineering",
    "ME": "Mechanical Engineering",
    "CE": "Civil Engineering",
}

# simple: one fact about the college; deep: comparison / multi-step;
# personal: the logged-in student's own data (fees, books, attendance, results).
ROUTES = ("simple", "deep", "personal")


@dataclass(frozen=True)
class Settings:
    aws_region: str
    vector_backend: str  # "s3" (Amazon S3 Vectors) or "memory" (local, for tests and demos)
    vector_bucket_name: str
    vector_index_name: str
    router_model_id: str
    answer_model_id: str
    embedding_model_id: str
    retriever_k: int
    max_research_loops: int
    research_workers: int  # sub-questions researched at the same time on the deep path (1 = sequential)
    data_dir: Path  # college documents (markdown)
    students_file: Path  # synthetic student records
    users_file: Path  # legacy JSON accounts (imported into db_file once)
    db_file: Path  # SQLite: accounts, sessions, reset codes, self-registered students
    results_dir: Path


@lru_cache
def get_settings() -> Settings:
    """Return settings read once from the environment."""
    return Settings(
        aws_region=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        vector_backend=os.getenv("VECTOR_BACKEND", "s3"),
        vector_bucket_name=os.getenv("VECTOR_BUCKET_NAME", "smart-campus-assistant-vectors"),
        vector_index_name=os.getenv("VECTOR_INDEX_NAME", "xyz-college-docs"),
        router_model_id=os.getenv("ROUTER_MODEL_ID", "us.amazon.nova-micro-v1:0"),
        answer_model_id=os.getenv("ANSWER_MODEL_ID", "us.amazon.nova-lite-v1:0"),
        embedding_model_id=os.getenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"),
        retriever_k=int(os.getenv("RETRIEVER_K", "5")),
        max_research_loops=int(os.getenv("MAX_RESEARCH_LOOPS", "2")),
        research_workers=int(os.getenv("RESEARCH_WORKERS", "4")),
        data_dir=PROJECT_ROOT / "data" / "college",
        students_file=PROJECT_ROOT / "data" / "students.json",
        users_file=Path(os.getenv("SCA_USERS_FILE", str(PROJECT_ROOT / "data" / "users.json"))),
        db_file=Path(os.getenv("SCA_DB_FILE", str(PROJECT_ROOT / "data" / "app.db"))),
        results_dir=PROJECT_ROOT / "eval" / "results",
    )
