"""Headless backend (path A): requests answered by `claude -p` processes,
here a stand-in CLI that mimics the real one's output and records how it was run."""

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "examples" / "agents"))
import trip_agent  # noqa: E402

FAKE = str(Path(__file__).parent / "fake_claude.py")


@pytest.fixture
def headless(gw, tmp_path, monkeypatch):
    log = tmp_path / "claude-calls"
    log.mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")       # set inside Desktop sessions; must not reach the CLI
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    backend = gw.app.state.headless
    backend.command_prefix = [sys.executable, FAKE]
    return log


def calls(log: Path) -> list[dict]:
    return sorted((json.loads(p.read_text()) for p in log.glob("call-*.json")), key=lambda c: c["start"])


async def test_openai_agent_tool_loop_on_headless(gw, headless):
    token = gw.headers["Authorization"].removeprefix("Bearer ")
    client = openai.AsyncOpenAI(base_url="http://127.0.0.1/v1", api_key=token, max_retries=0,
                                http_client=httpx.AsyncClient(transport=gw._transport))
    messages = [{"role": "system", "content": "You are a travel assistant."},
                {"role": "user", "content": "How far is Paris from Berlin in miles?"}]
    names = []
    for _ in range(4):
        r = await client.chat.completions.create(model="claude-headless", messages=messages, tools=trip_agent.TOOLS)
        msg = r.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            break
        for c in msg.tool_calls:
            names.append(c.function.name)
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "content": trip_agent.run_tool(c.function.name, json.loads(c.function.arguments))})
    assert names == ["road_distance_km", "calculate"] and msg.content.startswith("Paris to Berlin is 878 km")
    assert r.usage.prompt_tokens == 115 and r.usage.prompt_tokens_details.cached_tokens == 100 and r.usage.completion_tokens == 7

    first = calls(headless)[0]
    a = first["args"]
    assert a[a.index("--tools") + 1] == "" and "--strict-mcp-config" in a and a[a.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in a and a[a.index("--output-format") + 1] == "json"
    assert "You are a travel assistant." in a[a.index("--system-prompt") + 1]
    assert json.loads(a[a.index("--json-schema") + 1])["properties"]["tool_calls"]["items"]["properties"]["name"]["enum"] == ["road_distance_km", "calculate"]
    assert first["env_simple"] is None and first["env_api_key"] is None and first["env_companion"] == []
    assert Path(first["cwd"]).name == "headless"
    usage = (await gw.get("/api/usage")).json()["by_model"][0]
    assert usage["provider"] == "claude-headless" and usage["calls"] == 3 and usage["cost_usd"] == 0.0


async def test_requests_run_in_parallel_with_independent_processes(gw, headless, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "1.0")
    body = {"model": "claude-headless", "messages": [{"role": "user", "content": "ping"}]}
    started = time.perf_counter()
    rs = await asyncio.gather(*(gw.post("/v1/chat/completions", json=body) for _ in range(3)))
    elapsed = time.perf_counter() - started
    assert all(r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "pong" for r in rs)
    cs = calls(headless)
    assert len(cs) == 3 and elapsed < 2.8  # three 1 s calls overlapped (concurrency 3)
    assert max(c["start"] for c in cs) < min(c["end"] for c in cs)


async def test_anthropic_format_and_model_selection(gw, headless):
    r = await gw.post("/v1/messages", json={"model": "claude-headless/sonnet", "max_tokens": 100,
                                            "messages": [{"role": "user", "content": "ping"}]})
    data = r.json()
    assert data["content"] == [{"type": "text", "text": "pong"}] and data["stop_reason"] == "end_turn"
    assert data["usage"] == {"input_tokens": 15, "output_tokens": 7, "cache_read_input_tokens": 100}
    a = calls(headless)[0]["args"]
    assert a[a.index("--model") + 1] == "sonnet"


async def test_not_logged_in_gives_setup_hint(gw, headless, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "not_logged_in")
    r = await gw.post("/v1/chat/completions", json={"model": "claude-headless", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 503 and "claude setup-token" in r.json()["detail"]


async def test_invalid_reply_is_retried_then_rejected(gw, headless, monkeypatch):
    body = {"model": "claude-headless", "tools": trip_agent.TOOLS, "messages": [{"role": "user", "content": "ping"}]}
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "bad_once")
    r = await gw.post("/v1/chat/completions", json=body)
    assert r.status_code == 200 and len(calls(headless)) == 2
    assert "previous reply was rejected" in calls(headless)[1]["prompt"]
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "bad")
    r = await gw.post("/v1/chat/completions", json=body)
    assert r.status_code == 502 and "unusable reply twice" in r.json()["detail"]


async def test_reply_parsed_from_result_when_no_structured_output(gw, headless, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "no_structured")
    r = await gw.post("/v1/chat/completions", json={"model": "claude-headless", "messages": [{"role": "user", "content": "x"}]})
    assert r.json()["choices"][0]["message"]["content"] == "pong"


async def test_routing_by_model_and_default_backend(gw, headless):
    pending = asyncio.create_task(gw.post("/v1/chat/completions", json={"model": "claude-session",
                                                                        "messages": [{"role": "user", "content": "x"}]}))
    for _ in range(50):
        if (await gw.get("/api/bridge/pending")).json()["unclaimed"]:
            break
        await asyncio.sleep(0.05)
    assert calls(headless) == []  # claude-session never reaches the CLI
    req = (await gw.get("/api/bridge/next", params={"wait": 1})).json()["request"]
    await gw.post(f"/api/bridge/{req['request_id']}/answer", json={"content": "from the chat"})
    assert (await pending).json()["choices"][0]["message"]["content"] == "from the chat"

    gw.core.settings.bridge_default_backend = "headless"
    r = await gw.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]})
    assert r.json()["choices"][0]["message"]["content"] == "pong" and len(calls(headless)) == 1
    backends = (await gw.get("/api/bridge/backends")).json()
    assert backends["default_backend"] == "headless" and backends["headless"]["available"]
