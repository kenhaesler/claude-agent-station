"""Anthropic SDK client for coordinator-level LLM calls.

Replaces subprocess.run(["claude", ...]) with direct API calls.
Benefits: no subprocess overhead, structured output, proper error handling,
token accounting via response.usage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _resolve_api_key() -> str | None:
    """Resolve API key from env var or Claude CLI OAuth credentials.

    Priority:
    1. ANTHROPIC_API_KEY env var (standard)
    2. Claude CLI OAuth access token from ~/.claude/.credentials.json
    """
    import os

    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key

    # Try Claude CLI OAuth credentials
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    try:
        with open(creds_path) as f:
            creds = json.load(f)
        token = creds.get("claudeAiOauth", {}).get("accessToken")
        if token:
            logger.debug("Using OAuth access token from Claude CLI credentials")
            return token
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    return None


def get_client() -> anthropic.Anthropic:
    """Get or create the singleton Anthropic client.

    Auto-discovers API key from env var or Claude CLI OAuth credentials.
    """
    global _client
    if _client is None:
        api_key = _resolve_api_key()
        if api_key:
            _client = anthropic.Anthropic(api_key=api_key)
        else:
            _client = anthropic.Anthropic()  # Let SDK raise if no key found
    return _client


@dataclass
class LLMResponse:
    """Response from a coordinator LLM call with token accounting."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


def call_llm(
    prompt: str,
    model: str = "claude-haiku-4-5-20251001",
    system: str = "",
    max_tokens: int = 2048,
) -> LLMResponse:
    """Single-turn LLM call via Anthropic SDK.

    Returns LLMResponse with text and token counts.
    Raises anthropic.APIError on failure (caller should handle).
    """
    client = get_client()
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    text = response.content[0].text if response.content else ""

    return LLMResponse(
        text=text,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=model,
    )


def call_llm_json(
    prompt: str,
    model: str = "claude-haiku-4-5-20251001",
    system: str = "",
    max_tokens: int = 2048,
) -> tuple[dict, LLMResponse]:
    """Single-turn LLM call that extracts JSON from the response.

    Returns (parsed_dict, raw_response). Raises ValueError if JSON parsing fails.
    """
    resp = call_llm(prompt, model=model, system=system, max_tokens=max_tokens)
    text = resp.text.strip()

    # Try to extract JSON from potential markdown wrapping
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > 0:
        data = json.loads(text[start:end])
        return data, resp

    raise ValueError(f"No JSON object found in LLM response: {text[:200]}")
