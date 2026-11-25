"""Session model bridge: real OpenAI and Anthropic SDK clients talk to a live
gateway while a stand-in for the Claude session serves the requests through
the same endpoints the MCP tools use."""

import asyncio
import json
import sys
from pathlib import Path

import anthropic
import httpx
import openai
import pytest

from tests.test_mcp_contract import live_gateway  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).parent.parent / "examples" / "agents"))
import anthropic_sdk_agent  # noqa: E402
import trip_agent  # noqa: E402


def stand_in_claude(req: dict) -> dict:
    """Plays the model for the example agents, deciding from the transcript."""
    msgs = req["messages"]
    names = {t["name"] for t in req["tools"]}
    done = [m for m in msgs if m["role"] == "tool"]
    if "road_distance_km" in names:
        if not done:
            return {"tool_calls": [{"name": "road_distance_km", "arguments": {"a": "Paris", "b": "Berlin"}}]}
        if len(done) == 1:
            km = json.loads(done[0]["content"])["km"]
            return {"content": "Converting.", "tool_calls": [{"name": "calculate", "arguments": {"expression": f"{km} * 0.621371"}}]}
        miles = json.loads(done[1]["content"])["result"]
        return {"content": f"Paris to Berlin is 878 km by road, about {miles} miles."}
    if not done:
        return {"tool_calls": [{"name": "get_stock", "arguments": {"sku": "A-100"}},
                               {"name": "get_stock", "arguments": {"sku": "B-200"}}]}
    return {"content": "A-100 has 12 units; B-200 is out of stock."}


async def serve(base: str, token: str, stop: asyncio.Event, seen: list):
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}", "X-Companion-Actor": "mcp"},
                                 timeout=30) as c:
        while not stop.is_set():
            r = (await c.get("/api/bridge/next", params={"wait": 1})).json()
            if r["request"]:
                seen.append(r["request"])
                ans = await c.post(f"/api/bridge/{r['request']['request_id']}/answer", json=stand_in_claude(r["request"]))
                assert ans.status_code == 200, ans.text


async def with_server(settings, work):
    base, token = f"http://127.0.0.1:{settings.port}", settings.gateway_token()
    stop, seen = asyncio.Event(), []
    server = asyncio.create_task(serve(base, token, stop, seen))
    try:
        return await asyncio.to_thread(work, base, token), seen
    finally:
        stop.set()
        await server


async def test_openai_sdk_agent_runs_on_session_bridge(live_gateway):  # noqa: F811
    def work(base, token):
        client = openai.OpenAI(base_url=f"{base}/v1", api_key=token, default_headers={"X-Agent-Name": "trip_agent"})
        return trip_agent.run("How far is Paris from Berlin, in miles?", client, "claude-session")

    result, seen = await with_server(live_gateway, work)
    assert result["answer"].startswith("Paris to Berlin is 878 km") and result["steps"] == 3
    assert [c["name"] for c in result["tool_calls"]] == ["road_distance_km", "calculate"]
    assert result["tool_calls"][1]["result"]["result"] == pytest.approx(545.564, abs=0.01)
    assert seen[0]["agent"] == "trip_agent" and seen[0]["system"].startswith("You are a travel assistant")
    assert seen[2]["messages"][1]["tool_calls"][0]["name"] == "road_distance_km"  # history normalised for Claude


async def test_anthropic_sdk_agent_with_parallel_tool_calls(live_gateway):  # noqa: F811
    def work(base, token):
        client = anthropic.Anthropic(base_url=base, api_key=token, max_retries=0)
        return anthropic_sdk_agent.run("Do we have A-100 and B-200?", client, "claude-session")

    result, seen = await with_server(live_gateway, work)
    assert result["answer"] == "A-100 has 12 units; B-200 is out of stock."
    assert [c["args"]["sku"] for c in result["tool_calls"]] == ["A-100", "B-200"]
    assert [m["role"] for m in seen[1]["messages"]] == ["user", "assistant", "tool", "tool"]


async def test_streaming_both_formats(live_gateway):  # noqa: F811
    def work(base, token):
        oa = openai.OpenAI(base_url=f"{base}/v1", api_key=token)
        chunks = list(oa.chat.completions.create(model="claude-session", stream=True, tools=trip_agent.TOOLS,
                                                 messages=[{"role": "user", "content": "distance?"}]))
        call = chunks[1].choices[0].delta.tool_calls[0]
        an = anthropic.Anthropic(base_url=base, api_key=token, max_retries=0)
        with an.messages.stream(model="claude-session", max_tokens=100, tools=anthropic_sdk_agent.TOOLS,
                                messages=[{"role": "user", "content": "stock?"}]) as s:
            final = s.get_final_message()
        return call, final

    (call, final), _ = await with_server(live_gateway, work)
    assert call.function.name == "road_distance_km" and json.loads(call.function.arguments)["a"] == "Paris"
    assert final.stop_reason == "tool_use" and [b.input["sku"] for b in final.content] == ["A-100", "B-200"]


async def test_bridge_rejects_bad_answers_and_wrong_key(live_gateway):  # noqa: F811
    base, token = f"http://127.0.0.1:{live_gateway.port}", live_gateway.gateway_token()
    bad = openai.OpenAI(base_url=f"{base}/v1", api_key="sk-wrong", max_retries=0)
    with pytest.raises(openai.AuthenticationError):
        bad.models.list()

    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30) as c:
        pending = asyncio.create_task(c.post("/v1/chat/completions", json={
            "model": "claude-session", "tools": trip_agent.TOOLS, "messages": [{"role": "user", "content": "hi"}]}))
        req = None
        while req is None:
            req = (await c.get("/api/bridge/next", params={"wait": 2})).json()["request"]
        rid = req["request_id"]
        r = await c.post(f"/api/bridge/{rid}/answer", json={"tool_calls": [{"name": "rm_rf", "arguments": {}}]})
        assert r.status_code == 422 and "not offered" in r.text
        assert (await c.post(f"/api/bridge/{rid}/answer", json={})).status_code == 422
        assert (await c.post(f"/api/bridge/{rid}/answer", json={"content": "hello"})).status_code == 200
        assert (await c.post(f"/api/bridge/{rid}/answer", json={"content": "again"})).status_code == 422
        resp = (await pending).json()
        assert resp["choices"][0]["message"]["content"] == "hello" and resp["choices"][0]["finish_reason"] == "stop"
        usage = (await c.get("/api/usage")).json()
        assert usage["by_model"][0]["provider"] == "claude-session" and usage["by_model"][0]["cost_usd"] == 0.0


async def test_new_request_is_not_expired_before_its_waiter_exists(gw):
    """Regression: a poll landing between the task commit and the waiter
    registration used to expire a live request as 'caller disconnected'."""
    from datetime import timedelta

    from companion.models import SessionTask, utcnow

    ex = await gw.core.create_execution("model_request", {}, "api")
    task = await gw.core.open_task(ex.id, "llm", {"source": "bridge", "messages": [], "tools": []})
    assert await gw.bridge.next_request(0) is None
    async with gw.core.sessions() as s:
        assert (await s.get(SessionTask, task.id)).status == "open"
        (await s.get(SessionTask, task.id)).created_at = utcnow() - timedelta(seconds=30)
        await s.commit()
    assert await gw.bridge.next_request(0) is None
    async with gw.core.sessions() as s:
        assert (await s.get(SessionTask, task.id)).status == "expired"


async def test_timed_out_request_is_expired(live_gateway):  # noqa: F811
    from companion.config import Settings  # noqa: F401

    base, token = f"http://127.0.0.1:{live_gateway.port}", live_gateway.gateway_token()
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=2) as c:
        with pytest.raises(httpx.ReadTimeout):
            await c.post("/v1/chat/completions", json={"model": "claude-session", "messages": [{"role": "user", "content": "x"}]})
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30) as c:
        await asyncio.sleep(3)  # disconnects are checked once a second
        assert (await c.get("/api/bridge/next", params={"wait": 0})).json()["request"] is None
