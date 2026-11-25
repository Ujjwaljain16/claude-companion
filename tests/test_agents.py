"""Agent runner: real agent processes (Python, TypeScript, and an OpenAI-SDK
agent on the session bridge) run over datasets and get scored."""

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from tests.conftest import wait_for
from tests.test_bridge import stand_in_claude
from tests.test_mcp_contract import live_gateway  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"

CAPITALS = [
    {"id": "france", "input": "What is the capital of France?", "expected": "Paris"},
    {"id": "kenya", "input": "What is the capital of Kenya?", "expected": "Nairobi"},
    {"id": "canada", "input": "What is the capital of Canada?", "expected": "Ottawa"},
    {"id": "atlantis", "input": "What is the capital of Atlantis?", "expected": "Poseidonia"},
    {"id": "crash", "input": "crash please", "expected": "x"},
]


def write_registry(tmp_path: Path, monkeypatch, extra: str = "") -> None:
    py = sys.executable.replace("\\", "/")
    root = str(ROOT).replace("\\", "/")
    (tmp_path / "agents.toml").write_text(f"""
[agents.rules]
command = ["{py}", "examples/agents/rule_agent.py"]
cwd = "{root}"
timeout_s = 20
description = "deterministic"

[agents.trip]
command = ["{py}", "examples/agents/trip_agent_runner.py"]
cwd = "{root}"
timeout_s = 60
{extra}""", encoding="utf-8")
    monkeypatch.setenv("COMPANION_AGENTS_FILE", str(tmp_path / "agents.toml"))


async def run_and_wait(gw, body, states=("succeeded", "failed")):
    r = await gw.post("/api/agents/runs", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    for run in out["runs"]:
        ex = await wait_for(gw, run["execution_id"], states=states, timeout=90)
        assert ex["status"] == "succeeded", ex
    return out


async def test_rule_agent_metrics_regression_failures_and_exports(gw, tmp_path, monkeypatch):
    write_registry(tmp_path, monkeypatch)
    listed = (await gw.get("/api/agents")).json()
    assert {a["name"] for a in listed["agents"]} == {"rules", "trip"}
    await gw.post("/api/datasets", json={"name": "capitals", "items": CAPITALS})

    out = await run_and_wait(gw, {"agent": "rules", "dataset": "capitals",
                                  "scorers": [{"name": "exact_match"}, {"name": "tool_success"}],
                                  "variants": [{"label": "v1"}, {"label": "v2-buggy", "config": {"buggy": True}}],
                                  "parallel": 3})
    v1, v2 = (r["run_id"] for r in out["runs"])
    rep = (await gw.get(f"/api/evaluations/{v1}")).json()
    a = rep["summary"]["agent"]
    assert a["completion_rate"] == 0.8 and a["errors"] == 1  # crash item
    assert rep["summary"]["scores"]["exact_match"]["mean"] == 0.6  # france, kenya, canada
    assert a["failure_recovery_rate"] == 0.5  # kenya recovered after a 503; atlantis did not
    kenya = next(i for i in rep["items"] if i["item_id"] == "kenya")
    assert [c["name"] for c in kenya["trajectory"]["tool_calls"]] == ["lookup_capital", "lookup_capital"]
    assert kenya["scores"]["exact_match"]["passed"] and kenya["trajectory"]["steps"] == 3
    crash = next(i for i in rep["items"] if i["item_id"] == "crash")
    assert crash["status"] == "error" and "cannot parse question" in crash["error"]
    repro = rep["target"]["reproducibility"]
    assert repro["config_hash"] and repro["dataset_version"] == 1 and repro["command"][-1].endswith("rule_agent.py")
    assert "git" in repro

    cmp = (await gw.post("/api/evaluations/compare", json={"baseline_run_id": v1, "candidate_run_ids": [v2]})).json()
    em = cmp["comparisons"][0]["scorers"]["exact_match"]
    assert cmp["comparisons"][0]["regression"] and em["newly_failing_items"] == ["canada"]

    fails = (await gw.get(f"/api/evaluations/{v2}/failures")).json()
    cats = {c["category"]: c["item_ids"] for c in fails["categories"]}
    assert cats["agent error: ValueError: cannot parse question"] == ["crash"]
    assert set(cats["wrong result after tool errors"]) == {"atlantis"}
    assert cats["wrong result: exact_match"] == ["canada"]
    assert cats["right answer, trajectory scorer failed: tool_success"] == ["kenya"]

    for fmt, needle in (("csv", "exact_match_score"), ("html", "lookup_capital"), ("json", '"trajectory"')):
        r = await gw.get(f"/api/evaluations/{v1}/export", params={"format": fmt})
        assert r.status_code == 200 and needle in r.text and Path(r.headers["X-Report-Path"]).exists()
    html = await gw.post("/api/evaluations/compare", params={"format": "html"},
                         json={"baseline_run_id": v1, "candidate_run_ids": [v2]})
    assert "regression" in html.text and "agent rules / v2-buggy" in html.text


async def test_timeout_and_cancel_kill_the_agent_process(gw, tmp_path, monkeypatch):
    write_registry(tmp_path, monkeypatch)
    toml = (tmp_path / "agents.toml").read_text().replace("timeout_s = 20", "timeout_s = 2", 1)
    (tmp_path / "agents.toml").write_text(toml)
    await gw.post("/api/datasets", json={"name": "hang", "items": [{"id": "h", "input": "hang", "expected": "x"}]})
    out = await run_and_wait(gw, {"agent": "rules", "dataset": "hang", "scorers": [{"name": "exact_match"}]})
    rep = (await gw.get(f"/api/evaluations/{out['runs'][0]['run_id']}")).json()
    assert rep["items"][0]["status"] == "error" and "timed out" in rep["items"][0]["error"]
    pid_file = Path(rep["items"][0]["agent_meta"]["workdir"]) / "pid"
    assert not _alive(int(pid_file.read_text()))

    (tmp_path / "agents.toml").write_text(toml.replace("timeout_s = 2", "timeout_s = 120", 1))
    r = (await gw.post("/api/agents/runs", json={"agent": "rules", "dataset": "hang", "scorers": [{"name": "exact_match"}]})).json()
    run_id = r["runs"][0]["run_id"]
    pid_file = gw.core.settings.home / "agent_runs" / run_id / "h" / "pid"
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.1)
    pid = int(pid_file.read_text())
    assert _alive(pid)
    assert (await gw.post(f"/api/executions/{r['runs'][0]['execution_id']}/cancel")).json()["status"] == "cancelled"
    await asyncio.sleep(0.5)
    assert not _alive(pid)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        import os
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def test_unknown_agent_and_bad_registry(gw, tmp_path, monkeypatch):
    write_registry(tmp_path, monkeypatch)
    await gw.post("/api/datasets", json={"name": "d", "items": [{"input": "x"}]})
    r = await gw.post("/api/agents/runs", json={"agent": "nope", "dataset": "d", "scorers": [{"name": "exact_match"}]})
    assert r.status_code == 404 and "not registered" in r.text
    r = await gw.post("/api/agents/runs", json={"agent": "rules", "dataset": "d", "scorers": [{"name": "exact_match"}],
                                                "variants": [{"label": "a"}, {"label": "a"}]})
    assert r.status_code == 422
    (tmp_path / "agents.toml").write_text("[agents.bad name]\ncommand = []\n")
    assert "error" in (await gw.get("/api/agents")).json()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
async def test_typescript_agent(gw, tmp_path, monkeypatch):
    root = str(ROOT).replace("\\", "/")
    write_registry(tmp_path, monkeypatch, extra=f"""
[agents.calc-ts]
command = ["node", "--experimental-strip-types", "--no-warnings", "examples/agents/calc_agent.ts"]
cwd = "{root}"
timeout_s = 30
""")
    items = [{"id": "a", "input": "What is 6 * 7?", "expected": "42", "expected_tools": ["calculate"]},
             {"id": "b", "input": "Tell me a joke", "expected": "?", "expected_tools": []}]
    await gw.post("/api/datasets", json={"name": "calc", "items": items})
    out = await run_and_wait(gw, {"agent": "calc-ts", "dataset": "calc",
                                  "scorers": [{"name": "exact_match"}, {"name": "tool_selection"}]})
    rep = (await gw.get(f"/api/evaluations/{out['runs'][0]['run_id']}")).json()
    a = next(i for i in rep["items"] if i["item_id"] == "a")
    assert a["output"] == "42" and a["scores"]["tool_selection"]["score"] == 1.0
    assert rep["summary"]["agent"]["tool_success_rate"] == 0.5


async def test_bridge_agent_counts_model_calls_per_item(live_gateway, tmp_path, monkeypatch):  # noqa: F811
    write_registry(tmp_path, monkeypatch)
    base, token = f"http://127.0.0.1:{live_gateway.port}", live_gateway.gateway_token()
    stop = asyncio.Event()

    async def serve():
        async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30) as c:
            while not stop.is_set():
                req = (await c.get("/api/bridge/next", params={"wait": 1})).json()["request"]
                if req:
                    await c.post(f"/api/bridge/{req['request_id']}/answer", json=stand_in_claude(req))

    server = asyncio.create_task(serve())
    try:
        async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30) as gw:
            await gw.post("/api/datasets", json={"name": "trips", "items": [
                {"id": "pb", "input": "How far is Paris from Berlin, in miles?", "expected": "545",
                 "expected_trajectory": ["road_distance_km", "calculate"]}]})
            out = await run_and_wait(gw, {"agent": "trip", "dataset": "trips",
                                          "scorers": [{"name": "contains"}, {"name": "trajectory_match"}]})
            rep = (await gw.get(f"/api/evaluations/{out['runs'][0]['run_id']}")).json()
    finally:
        stop.set()
        await server
    item = rep["items"][0]
    assert item["status"] != "error", item
    assert item["agent_meta"]["model_calls"] == 3 and item["agent_meta"]["usage_source"] == "gateway"
    assert item["scores"]["contains"]["passed"] and item["scores"]["trajectory_match"]["score"] == 1.0
    assert rep["summary"]["agent"]["model_calls"] == 3
    assert json.dumps(item["trajectory"]).count("road_distance_km") >= 1
