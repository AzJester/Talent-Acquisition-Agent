"""Thin wrapper over the Anthropic SDK (Claude API or Amazon Bedrock).

Everything the app sends to a model goes through `LLMClient.complete`, so tests can swap in
a fake and the audit log sees every call."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

import anthropic

from taa.config import Settings


class LLMError(Exception):
    """A model call failed or returned something unusable."""


class LLMRefusal(LLMError):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str
    stop_reason: str | None
    request_id: str | None = None
    usage: dict = field(default_factory=dict)

    def json(self) -> dict:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Model returned invalid JSON: {e}") from e


class LLMClient(Protocol):
    provider: str

    def complete(self, *, model: str, system: list[dict], user: str, effort: str,
                 max_tokens: int = 16000, json_schema: dict | None = None) -> LLMResponse: ...


class ClaudeClient:
    """Claude via the first-party API (with server-side refusal fallbacks) or Bedrock."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.llm_provider
        if self.provider == "bedrock":
            if not settings.bedrock_region:
                raise LLMError("TAA_BEDROCK_REGION (or AWS_REGION) is required for the Bedrock provider")
            self._client = anthropic.AnthropicBedrockMantle(aws_region=settings.bedrock_region, max_retries=4)
        elif self.provider == "anthropic":
            self._client = anthropic.Anthropic(max_retries=4)
        else:
            raise LLMError(f"LLM provider '{self.provider}' is not enabled")

    def _model_id(self, model: str) -> str:
        if self.provider == "bedrock" and not model.startswith("anthropic."):
            return f"anthropic.{model}"
        return model

    def complete(self, *, model: str, system: list[dict], user: str, effort: str,
                 max_tokens: int = 16000, json_schema: dict | None = None) -> LLMResponse:
        output_config: dict = {"effort": effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        kwargs = dict(
            model=self._model_id(model),
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config=output_config,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        try:
            if self.provider == "anthropic" and self.settings.fallbacks:
                # On a policy decline the API re-runs the request on Anthropic's recommended fallback model
                resp = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self._client.messages.create(**kwargs)
        except anthropic.BadRequestError as e:
            raise LLMError(f"Bad request: {e.message}") from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMError(f"Model access denied: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited after retries") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Connection error: {e}") from e

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMRefusal(f"Model declined the request (category: {category})")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Model output hit max_tokens before finishing")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        u = resp.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        return LLMResponse(text=text, model=resp.model, stop_reason=resp.stop_reason,
                           request_id=getattr(resp, "_request_id", None), usage=usage)


def make_client(settings: Settings) -> LLMClient | None:
    if not settings.llm_enabled:
        return None
    return ClaudeClient(settings)
