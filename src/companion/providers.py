"""Model providers the gateway calls with its own, separately configured keys.

None of these touch Claude Desktop. The special "session" target is not a
provider at all: it means the Claude session connected over MCP writes the
outputs itself and submits them back through a tool.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
import httpx

from companion.config import Settings

SESSION_TARGET = "session"

# USD per million tokens (input, output). Anthropic rates are list prices for
# the first-party API; anything not listed reports cost as unknown rather
# than guessing. Add or override entries with COMPANION_PRICING_FILE.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    stop_reason: str | None = None


@dataclass
class Provider:
    name: str
    label: str
    kind: str  # "anthropic" | "openai_compatible" | "gemini"
    base_url: str
    api_key: str | None = None
    paid: bool = True
    needs_key: bool = True
    http: httpx.AsyncClient | None = field(default=None, repr=False)
    # The anthropic SDK ships its own fork of httpx, so it takes a separate client.
    anthropic_http: Any = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or not self.needs_key

    def _client(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5))
        return self.http

    def _anthropic(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(api_key=self.api_key, base_url=self.base_url, http_client=self.anthropic_http, max_retries=2)

    async def list_models(self) -> list[str]:
        if not self.configured:
            raise ProviderError(f"{self.label} is not configured")
        try:
            if self.kind == "anthropic":
                return [m.id async for m in self._anthropic().models.list(limit=100)]
            if self.kind == "gemini":
                r = await self._client().get(f"{self.base_url}/models", headers={"x-goog-api-key": self.api_key or ""})
                _raise_for(r)
                return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", [])]
            r = await self._client().get(f"{self.base_url}/models", headers=self._bearer())
            _raise_for(r)
            return sorted(m["id"] for m in r.json().get("data", []))
        except anthropic.APIError as e:
            raise ProviderError(f"{self.label}: {e}") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.label} unreachable: {e}", retryable=True) from e

    async def complete(self, model: str, messages: list[dict[str, str]], system: str = "", max_tokens: int = 1024) -> Completion:
        if not self.configured:
            raise ProviderError(f"{self.label} is not configured")
        started = time.perf_counter()
        try:
            if self.kind == "anthropic":
                return await self._complete_anthropic(model, messages, system, max_tokens, started)
            if self.kind == "gemini":
                return await self._complete_gemini(model, messages, system, max_tokens, started)
            return await self._complete_openai(model, messages, system, max_tokens, started)
        except anthropic.RateLimitError as e:
            raise ProviderError(f"{self.label} rate limited: {e}", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"{self.label} error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"{self.label} unreachable: {e}", retryable=True) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.label} unreachable: {e}", retryable=True) from e

    def _bearer(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def _complete_anthropic(self, model, messages, system, max_tokens, started) -> Completion:
        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        msg = await self._anthropic().messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason == "refusal":
            text = text or "[refused]"
        return Completion(text, msg.usage.input_tokens, msg.usage.output_tokens, _ms(started), msg.stop_reason)

    async def _complete_openai(self, model, messages, system, max_tokens, started) -> Completion:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        r = await self._client().post(
            f"{self.base_url}/chat/completions",
            headers=self._bearer(),
            json={"model": model, "messages": msgs, "max_tokens": max_tokens},
        )
        _raise_for(r)
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return Completion(
            choice["message"].get("content") or "",
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            _ms(started),
            choice.get("finish_reason"),
        )

    async def _complete_gemini(self, model, messages, system, max_tokens, started) -> Completion:
        body: dict[str, Any] = {
            "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        r = await self._client().post(
            f"{self.base_url}/models/{model}:generateContent", headers={"x-goog-api-key": self.api_key or ""}, json=body
        )
        _raise_for(r)
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        usage = data.get("usageMetadata") or {}
        return Completion(text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), _ms(started), cand.get("finishReason"))


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _raise_for(r: httpx.Response) -> None:
    if r.status_code >= 400:
        detail = r.text[:300]
        raise ProviderError(f"HTTP {r.status_code}: {detail}", retryable=r.status_code == 429 or r.status_code >= 500)


async def complete_with_retry(provider: Provider, *args, attempts: int = 3, **kwargs) -> Completion:
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return await provider.complete(*args, **kwargs)
        except ProviderError as e:
            if not e.retryable or attempt == attempts:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


class ProviderRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pricing = dict(DEFAULT_PRICING)
        if settings.pricing_file and Path(settings.pricing_file).exists():
            for model, (inp, out) in json.loads(Path(settings.pricing_file).read_text()).items():
                self.pricing[model] = (float(inp), float(out))
        key = lambda s: s.get_secret_value() if s else None  # noqa: E731
        providers = [
            Provider("anthropic", "Anthropic API", "anthropic", "https://api.anthropic.com", key(settings.anthropic_api_key)),
            Provider("openai", "OpenAI", "openai_compatible", "https://api.openai.com/v1", key(settings.openai_api_key)),
            Provider("gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta", key(settings.gemini_api_key)),
            Provider("openrouter", "OpenRouter", "openai_compatible", "https://openrouter.ai/api/v1", key(settings.openrouter_api_key)),
            Provider("ollama", "Ollama (local)", "openai_compatible", settings.ollama_url.rstrip("/") + "/v1", paid=False, needs_key=False),
        ]
        if settings.custom_base_url:
            providers.append(
                Provider("custom", "Custom OpenAI-compatible", "openai_compatible", settings.custom_base_url.rstrip("/"),
                         key(settings.custom_api_key), needs_key=False)
            )
        self.providers: dict[str, Provider] = {p.name: p for p in providers}

    def register(self, provider: Provider) -> None:
        self.providers[provider.name] = provider

    def get(self, name: str) -> Provider:
        if name not in self.providers:
            raise ProviderError(f"unknown provider '{name}' (known: {', '.join(self.providers)})")
        return self.providers[name]

    def cost(self, provider: Provider, model: str, input_tokens: int, output_tokens: int) -> float | None:
        if not provider.paid:
            return 0.0
        price = self.pricing.get(model)
        if price is None:
            return None
        return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 6)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": p.name, "label": p.label, "kind": p.kind, "configured": p.configured, "paid": p.paid,
             "base_url": p.base_url}
            for p in self.providers.values()
        ]

    async def probe(self, name: str, timeout: float = 5.0) -> dict[str, Any]:
        p = self.get(name)
        if not p.configured:
            return {"name": name, "reachable": False, "error": "not configured", "models": []}
        started = time.perf_counter()
        try:
            models = await asyncio.wait_for(p.list_models(), timeout)
            return {"name": name, "reachable": True, "latency_ms": _ms(started), "model_count": len(models), "models": models[:50]}
        except (ProviderError, asyncio.TimeoutError) as e:
            return {"name": name, "reachable": False, "error": str(e) or "timed out", "models": []}
