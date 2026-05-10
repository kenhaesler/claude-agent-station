"""HTTP surface for third-party provider API keys.

Bring-your-own-key panels for non-Anthropic LLM providers (OpenAI Codex,
Google Gemini). Deliberately separate from ``oauth.py`` (which is
Anthropic-specific) and ``github_app.py`` (GitHub).

Endpoints:
  GET    /api/provider-keys           — status for every supported provider
  PUT    /api/provider-keys/{name}    — store a raw API key (never echoed back)
  DELETE /api/provider-keys/{name}    — remove the stored key
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.schemas import ProviderKeyStatus, ProviderKeysOut
from app.services import provider_keys as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/provider-keys", tags=["provider-keys"])


def _ensure_supported(provider: str) -> None:
    if provider not in svc.SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. Supported: {', '.join(svc.SUPPORTED_PROVIDERS)}",
        )


class ProviderKeyUpdate(BaseModel):
    """Body for ``PUT /api/provider-keys/{provider}``.

    The raw key is accepted on the way in but never serialised back —
    GET responses only carry the masked form.
    """

    key: str

    @field_validator("key")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("key must not be empty")
        return v


@router.get("", response_model=ProviderKeysOut)
async def get_keys() -> ProviderKeysOut:
    """Return the configured/masked status for every supported provider."""
    return ProviderKeysOut(
        openai=ProviderKeyStatus(**svc.get_status("openai")),
        gemini=ProviderKeyStatus(**svc.get_status("gemini")),
    )


@router.put("/{provider}", response_model=ProviderKeyStatus)
async def set_key(provider: str, body: ProviderKeyUpdate) -> ProviderKeyStatus:
    """Persist ``body.key`` for ``provider``; return the new public status."""
    _ensure_supported(provider)
    status = svc.write_key(provider, body.key)
    logger.info("provider_keys: write provider=%s len=%d", provider, len(body.key))
    return ProviderKeyStatus(**status)


@router.delete("/{provider}", response_model=ProviderKeyStatus)
async def clear_key(provider: str) -> ProviderKeyStatus:
    """Remove the stored key for ``provider``. Idempotent."""
    _ensure_supported(provider)
    status = svc.delete_key(provider)
    logger.info("provider_keys: delete provider=%s", provider)
    return ProviderKeyStatus(**status)
