import json

import httpx
import pytest

from companion.config import Settings
from companion.providers import Provider, ProviderError, ProviderRegistry, complete_with_retry


def mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_compatible_request_and_usage():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 7, "completion_tokens": 2}})

    p = Provider("openai", "OpenAI", "openai_compatible", "https://api.openai.com/v1", "sk-test", http=mock(handler))
    c = await p.complete("gpt-x", [{"role": "user", "content": "hello"}], system="be brief", max_tokens=10)
    assert (c.text, c.input_tokens, c.output_tokens) == ("hi", 7, 2)
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be brief"}


async def test_gemini_maps_roles_and_usage():
    def handler(req: httpx.Request):
        body = json.loads(req.content)
        assert req.headers["x-goog-api-key"] == "g-key"
        assert body["contents"][1]["role"] == "model"
        assert body["systemInstruction"]["parts"][0]["text"] == "sys"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                                         "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1}})

    p = Provider("gemini", "Gemini", "gemini", "https://g/v1beta", "g-key", http=mock(handler))
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    c = await p.complete("gemini-x", msgs, system="sys")
    assert (c.text, c.input_tokens, c.output_tokens) == ("ok", 3, 1)


async def test_anthropic_uses_sdk_and_reads_usage():
    import httpx2

    def handler(req):
        assert req.url.path == "/v1/messages"
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert "ak" in (headers.get("x-api-key", "") + headers.get("authorization", "")), headers.get("authorization")
        body = json.loads(req.content)
        assert body["system"] == "sys" and "temperature" not in body
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": "4"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 1}})

    p = Provider("anthropic", "Anthropic API", "anthropic", "https://api.anthropic.com", "ak",
                 anthropic_http=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    c = await p.complete("claude-haiku-5-5", [{"role": "user", "content": "2+2"}], system="sys")
    assert (c.text, c.input_tokens, c.output_tokens) == ("4", 12, 1)


async def test_retry_on_5xx_then_success():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    p = Provider("x", "X", "openai_compatible", "http://x", "k", http=mock(handler))
    c = await complete_with_retry(p, "m", [{"role": "user", "content": "q"}])
    assert c.text == "ok" and calls["n"] == 2


async def test_4xx_is_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    p = Provider("x", "X", "openai_compatible", "http://x", "k", http=mock(handler))
    with pytest.raises(ProviderError):
        await complete_with_retry(p, "m", [{"role": "user", "content": "q"}])
    assert calls["n"] == 1


def test_registry_never_exposes_keys_and_prices(tmp_path):
    s = Settings(home=tmp_path, openai_api_key="sk-secret-value", _env_file=None)
    reg = ProviderRegistry(s)
    assert "sk-secret-value" not in json.dumps(reg.describe())
    by = {p["name"]: p for p in reg.describe()}
    assert by["openai"]["configured"] and not by["anthropic"]["configured"] and by["ollama"]["configured"]
    assert reg.cost(reg.get("anthropic"), "claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.6)
    assert reg.cost(reg.get("openai"), "unknown-model", 10, 10) is None
    assert reg.cost(reg.get("ollama"), "llama3", 10, 10) == 0.0


def test_unprefixed_vendor_env_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-be-used")
    reg = ProviderRegistry(Settings(home=tmp_path, _env_file=None))
    assert reg.get("anthropic").configured is False
