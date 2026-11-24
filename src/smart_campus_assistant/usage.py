"""Token usage and cost tracking for every LLM call."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from typing import Iterable, Iterator, Mapping

from langchain_core.messages import BaseMessage

from smart_campus_assistant.state import UsageRecord

# USD per 1,000 tokens (input, output), Bedrock on-demand, us-east-1.
# Check https://aws.amazon.com/bedrock/pricing/ before quoting numbers.
PRICING_PER_1K: dict[str, tuple[float, float]] = {
    "nova-micro": (0.000035, 0.00014),
    "nova-lite": (0.00006, 0.00024),
    "nova-pro": (0.0008, 0.0032),
    "titan-embed-text-v2": (0.00002, 0.0),
}


def price_for(model_id: str) -> tuple[float, float]:
    for key, price in PRICING_PER_1K.items():
        if key in str(model_id or ""):
            return price
    return (0.0, 0.0)


def _count(record: Mapping, key: str) -> float:
    """A non-negative number from a usage record; 0 when it is missing or not a number.

    Records come from the streamed graph updates, so a partial or odd record must count as
    zero instead of breaking the per-question monitoring.
    """
    try:
        value = float(record.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 and math.isfinite(value) else 0.0


def cost_usd(record: UsageRecord) -> float:
    price_in, price_out = price_for(record.get("model_id", ""))
    return _count(record, "input_tokens") / 1000 * price_in + _count(record, "output_tokens") / 1000 * price_out


def usage_from_message(node: str, model_id: str, message: BaseMessage, latency_ms: float) -> UsageRecord:
    """Build a usage record from an AIMessage's ``usage_metadata`` (zeros if missing)."""
    meta = getattr(message, "usage_metadata", None) or {}
    return UsageRecord(
        node=node,
        model_id=model_id,
        input_tokens=int(meta.get("input_tokens", 0)),
        output_tokens=int(meta.get("output_tokens", 0)),
        latency_ms=round(latency_ms, 1),
    )


@contextmanager
def timer() -> Iterator[dict]:
    """Measure elapsed milliseconds: ``with timer() as t: ...; t["ms"]``."""
    result = {"ms": 0.0}
    start = time.perf_counter()
    try:
        yield result
    finally:
        result["ms"] = (time.perf_counter() - start) * 1000


def summarize_usage(records: Iterable[UsageRecord]) -> dict:
    """Totals for one question: calls, tokens, cost and LLM latency (anything that is not a record is skipped)."""
    records = [r for r in (records or []) if isinstance(r, Mapping)]
    input_tokens = int(sum(_count(r, "input_tokens") for r in records))
    output_tokens = int(sum(_count(r, "output_tokens") for r in records))
    return {
        "llm_calls": len(records),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": round(sum(cost_usd(r) for r in records), 8),
        "llm_latency_ms": round(sum(_count(r, "latency_ms") for r in records), 1),
    }
