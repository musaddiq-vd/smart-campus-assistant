"""Factories for Bedrock chat models and embeddings.

Always call these through the module (``from smart_campus_assistant import models``;
``models.get_chat_model("router")``) so tests and fake mode can monkeypatch them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from smart_campus_assistant.config import get_settings

Role = Literal["router", "answer"]


def model_id_for(role: Role) -> str:
    settings = get_settings()
    return settings.router_model_id if role == "router" else settings.answer_model_id


@lru_cache
def get_chat_model(role: Role) -> BaseChatModel:
    """Nova Micro for routing (cheap), Nova Lite for answers."""
    settings = get_settings()
    return ChatBedrockConverse(
        model=model_id_for(role),
        region_name=settings.aws_region,
        temperature=0,
        max_tokens=300 if role == "router" else 1500,
    )


@lru_cache
def get_embeddings() -> Embeddings:
    settings = get_settings()
    return BedrockEmbeddings(model_id=settings.embedding_model_id, region_name=settings.aws_region)
