"""Central settings, loaded from environment variables and the project's .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
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

def _is_placeholder(value: str | None) -> bool:
    return not value or value.strip().lower().startswith("your_")


def has_aws_credentials() -> bool:
    """Local check only (no network): env vars, the project .env, or an AWS profile on disk."""
    env = dict(os.environ)
    try:
        from dotenv import dotenv_values

        env = {**{k: v for k, v in dotenv_values(PROJECT_ROOT / ".env").items() if v}, **env}
    except Exception:  # noqa: BLE001
        pass
    if not _is_placeholder(env.get("AWS_ACCESS_KEY_ID")) and not _is_placeholder(env.get("AWS_SECRET_ACCESS_KEY")):
        return True
    if env.get("AWS_PROFILE"):
        return True
    aws_dir = Path(env.get("AWS_SHARED_CREDENTIALS_FILE", Path.home() / ".aws" / "credentials"))
    try:
        return aws_dir.is_file() and "aws_access_key_id" in aws_dir.read_text(errors="ignore").lower()
    except OSError:
        return False


def is_demo_mode() -> bool:
    """Demo (offline) mode: ``SCA_DEMO_MODE=1`` forces it, ``0`` turns it off, otherwise ON without AWS credentials.

    Demo mode also allows things that are only safe with synthetic data, such as showing a
    one-time code on screen when no email server is configured.
    """
    flag = os.getenv("SCA_DEMO_MODE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return not has_aws_credentials()


DEFAULT_TIMEZONE = "Asia/Kolkata"
_IST = timezone(timedelta(hours=5, minutes=30), "IST")


def local_timezone() -> tzinfo:
    """The college's time zone (``SCA_TIMEZONE``, default Asia/Kolkata) for dates and times shown to people.

    Timestamps are stored in UTC; this only decides how they are shown and which day they count
    for. Without the IANA time zone database (Windows without the ``tzdata`` package) India's
    fixed +05:30 is used, and any other unknown zone falls back to UTC.
    """
    name = os.getenv("SCA_TIMEZONE", "").strip() or DEFAULT_TIMEZONE
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001  (ZoneInfoNotFoundError, bad name)
        return _IST if name == DEFAULT_TIMEZONE else timezone.utc


def local_time(stamp: object) -> datetime | None:
    """A stored ISO timestamp in the local time zone (no zone in the text means UTC); None if it is not one."""
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(local_timezone())


def env_flag(name: str, default: bool = True) -> bool:
    """A yes/no environment setting: "0", "false", "off" or "no" mean False; empty means ``default``."""
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in ("0", "false", "off", "no")


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
    # "hybrid": vector + BM25 results fused (reciprocal rank fusion) and reranked; "vector": similarity only.
    search_mode: str
    max_research_loops: int
    research_workers: int  # sub-questions researched at the same time on the deep path (1 = sequential)
    data_dir: Path  # college documents (markdown)
    students_file: Path  # synthetic student records
    users_file: Path  # legacy JSON accounts (imported into db_file once)
    db_file: Path  # SQLite: accounts, sessions, reset codes, self-registered students
    results_dir: Path


SEARCH_MODES = ("hybrid", "vector")


def search_mode_from_env() -> str:
    """SEARCH_MODE, lower-cased; an unknown value falls back to "hybrid" instead of breaking search."""
    mode = os.getenv("SEARCH_MODE", "hybrid").strip().lower()
    return mode if mode in SEARCH_MODES else "hybrid"


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
        search_mode=search_mode_from_env(),
        max_research_loops=int(os.getenv("MAX_RESEARCH_LOOPS", "2")),
        research_workers=int(os.getenv("RESEARCH_WORKERS", "4")),
        data_dir=PROJECT_ROOT / "data" / "college",
        students_file=PROJECT_ROOT / "data" / "students.json",
        users_file=Path(os.getenv("SCA_USERS_FILE", str(PROJECT_ROOT / "data" / "users.json"))),
        db_file=Path(os.getenv("SCA_DB_FILE", str(PROJECT_ROOT / "data" / "app.db"))),
        results_dir=PROJECT_ROOT / "eval" / "results",
    )
