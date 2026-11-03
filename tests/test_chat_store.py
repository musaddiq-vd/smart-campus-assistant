"""Tests for the per-user chat history store (temp folder via SCA_CHATS_DIR)."""

from __future__ import annotations

import hashlib
import json

import pytest

from smart_campus_assistant import chat_store

A = "Aarav@XYZ.edu.example"
B = "diya@xyz.edu.example"


@pytest.fixture(autouse=True)
def chats_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SCA_CHATS_DIR", str(tmp_path))
    return tmp_path


def turn(i: int) -> list[dict]:
    return [
        {"role": "user", "content": f"question {i}"},
        {"role": "assistant", "answer": f"answer {i}", "route": "simple", "route_reason": "fact",
         "citations": [{"source": "xyz/fees.md", "page": 1, "snippet": "x" * 2000, "score": 0.9}],
         "steps": [{"node": "router", "ms": 12}, {"node": "simple_answer", "n_docs": 5}],
         "usage": [{"input_tokens": 400}], "cost_usd": 0.1},
    ]


def test_round_trip_keeps_only_ui_fields(chats_dir):
    chat_store.append_messages(A, turn(1))
    history = chat_store.load_history(A.lower())  # email case does not matter
    assert [m["role"] for m in history] == ["user", "assistant"]
    user, answer = history
    assert user["content"] == "question 1" and "created_at" in user
    assert answer["answer"] == "answer 1" and answer["route"] == "simple" and answer["route_reason"] == "fact"
    assert answer["steps"] == [{"node": "router"}, {"node": "simple_answer", "n_docs": 5}]
    assert set(answer["citations"][0]) == {"source", "page", "snippet"}
    assert len(answer["citations"][0]["snippet"]) == chat_store.MAX_SNIPPET_CHARS
    assert "usage" not in answer and "cost_usd" not in answer
    name = hashlib.sha256(A.lower().encode()).hexdigest() + ".json"
    assert [p.name for p in chats_dir.iterdir()] == [name]
    assert "aarav" not in name.lower()


def test_cap_at_50_messages():
    for i in range(40):
        chat_store.append_messages(A, turn(i))
    history = chat_store.load_history(A)
    assert len(history) == chat_store.MAX_MESSAGES == 50
    assert history[-1]["answer"] == "answer 39" and history[0]["content"] == "question 15"


def test_users_are_isolated_and_clear():
    chat_store.append_messages(A, turn(1))
    chat_store.append_messages(B, turn(2))
    assert [m.get("content") for m in chat_store.load_history(A)][0] == "question 1"
    assert [m.get("content") for m in chat_store.load_history(B)][0] == "question 2"
    chat_store.clear_history(A)
    assert chat_store.load_history(A) == []
    assert len(chat_store.load_history(B)) == 2
    chat_store.clear_history(A)  # clearing twice is fine


def test_missing_and_broken_files(chats_dir):
    assert chat_store.load_history(A) == []
    path = chat_store._path(A)
    path.write_text("{not json", encoding="utf-8")
    assert chat_store.load_history(A) == []
    chat_store.append_messages(A, turn(1))  # a broken file is replaced, not fatal
    assert len(chat_store.load_history(A)) == 2
    path.write_text(json.dumps({"messages": [{"role": "system", "content": "x"}, "junk", {"role": "user"}]}), encoding="utf-8")
    assert chat_store.load_history(A) == []


def test_invalid_input():
    with pytest.raises(ValueError):
        chat_store.load_history("")
    chat_store.append_messages(A, [{"role": "tool", "content": "x"}])
    assert chat_store.load_history(A) == []
