"""MCP contract tests: spawn the real stdio server as a subprocess (the way
the Code tab does) against a real gateway on a local port."""

import json
import os
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from companion.gateway import create_app
from companion.providers import ProviderRegistry
from tests.conftest import MATH, FakeProvider

EXPECTED_TOOLS = {
    "list_available_models", "inspect_providers", "run_evaluation", "create_evaluation_dataset",
    "compare_model_results", "execute_agent_workflow", "inspect_agent_trace", "get_execution_status",
    "retrieve_evaluation_report", "inspect_system_health", "search_experiments", "create_experiment",
    "inspect_usage", "list_datasets", "cancel_execution", "list_session_tasks", "submit_session_task",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_gateway(settings):
    settings.port = free_port()
    reg = ProviderRegistry(settings)
    reg.register(FakeProvider())
    server = uvicorn.Server(uvicorn.Config(create_app(settings, reg), host="127.0.0.1", port=settings.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{settings.port}/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield settings
    server.should_exit = True
    thread.join(10)


def stdio_params(settings):
    env = {**os.environ, "COMPANION_HOME": str(settings.home), "COMPANION_PORT": str(settings.port), "COMPANION_AUTOSTART": "false"}
    return StdioServerParameters(command=sys.executable, args=["-m", "companion.mcp_server"], env=env)


def data(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


async def test_tools_schemas_and_session_eval_round_trip(live_gateway):
    progress = []

    async def on_progress(p, total, msg):
        progress.append((p, msg))

    async with Client(stdio_params(live_gateway), read_timeout_seconds=60) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert EXPECTED_TOOLS <= set(tools)
        for t in tools.values():
            assert t.input_schema["type"] == "object" and t.description
        assert tools["inspect_usage"].annotations.read_only_hint is True
        assert tools["run_evaluation"].input_schema["properties"]["wait_seconds"]["maximum"] == 55

        health = data(await client.call_tool("inspect_system_health", {}))
        assert health["database"]["ok"] is True

        created = data(await client.call_tool("create_evaluation_dataset", {"name": "math", "items": MATH}))
        assert created["version"] == 1

        started = data(await client.call_tool(
            "run_evaluation", {"dataset": "math", "provider": "session", "scorers": ["exact_match"], "wait_seconds": 5},
            progress_callback=on_progress))
        assert started["status"] == "waiting"

        tasks = data(await client.call_tool("list_session_tasks", {"execution_id": started["execution_id"]}))["tasks"]
        outputs = [{"item_id": it["item_id"], "output": str(2 * int(it["item_id"][1:]))} for it in tasks[0]["payload"]["items"]]
        sub = data(await client.call_tool("submit_session_task", {"task_id": tasks[0]["task_id"], "response": {"outputs": outputs}}))
        assert sub["execution_status"] == "succeeded"

        rep = data(await client.call_tool("retrieve_evaluation_report", {"run_id": started["run_id"]}))
        assert rep["summary"]["scores"]["exact_match"]["mean"] == 1.0

        health = data(await client.call_tool("inspect_system_health", {}))
        assert health["mcp_clients"] and health["mcp_clients"][0]["tool_calls"] >= 5
    assert progress, "expected progress notifications while waiting"


async def test_invalid_input_and_gateway_errors_are_tool_errors(live_gateway):
    async with Client(stdio_params(live_gateway), read_timeout_seconds=60) as client:
        bad = await client.call_tool("run_evaluation", {"dataset": "x", "provider": "session", "scorers": ["nope"]})
        assert bad.is_error
        missing = await client.call_tool("retrieve_evaluation_report", {"run_id": "doesnotexist"})
        assert missing.is_error and "not found" in missing.content[0].text
        approval = await client.call_tool("submit_session_task", {"task_id": "nope", "response": {}})
        assert approval.is_error


async def test_gateway_down_gives_clear_error(settings):
    settings.port = free_port()
    async with Client(stdio_params(settings), read_timeout_seconds=30) as client:
        r = await client.call_tool("inspect_system_health", {})
        assert r.is_error and "gateway not reachable" in r.content[0].text
