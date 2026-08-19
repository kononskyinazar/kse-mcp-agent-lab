"""Model access. OpenRouter by default, injectable so tests never call out."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ModelUnavailable(RuntimeError):
    pass


def build_model(temperature: float = 0.0, **kwargs: Any):
    """Chat model backed by OpenRouter.

    Raises rather than falling back to a canned answer: a silent offline path
    would make the agent's output impossible to attribute.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ModelUnavailable(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and fill it in; "
            "the agent does not substitute a canned response."
        )
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ModelUnavailable("langchain-openai is not installed; pip install -e '.[agent]'") from exc

    return ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        api_key=api_key,
        base_url=os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
        temperature=temperature,
        **kwargs,
    )
