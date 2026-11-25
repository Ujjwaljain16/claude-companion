import httpx
import pytest

from companion.config import Settings
from companion.gateway import create_app
from tests.conftest import MATH, wait_for


async def make_dataset(gw, name="math", items=MATH):
    r = await gw.post("/api/datasets", json={"name": name, "items": items})
    assert r.status_code == 200, r.text
    return r.json()


async def test_requires_token_and_local_host(gw):
    async with httpx.AsyncClient(transport=gw._transport, base_url="http://127.0.0.1") as anon:
        assert (await anon.get("/api/datasets")).status_code == 401
        assert (await anon.get("/api/datasets", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await anon.get("/api/health")).status_code == 200
    r = await gw.get("/api/datasets", headers={"host": "attacker.example:8765"})
    assert r.status_code == 403


async def test_dataset_versioning_and_validation(gw):
    v1 = await make_dataset(gw)
    v2 = await make_dataset(gw, items=MATH[:2])
    assert (v1["version"], v2["version"]) == (1, 2)
    ds = (await gw.get("/api/datasets/math", params={"version": 1})).json()
    assert len(ds["items"]) == 5 and [v["version"] for v in ds["versions"]] == [1, 2]
    bad = await gw.post("/api/datasets", json={"name": "x", "items": [{"input": "a", "id": "1"}, {"input": "b", "id": "1"}]})
    assert bad.status_code == 422 and "duplicate" in bad.text
    unknown = await gw.post("/api/datasets", json={"name": "x", "items": [{"input": "a", "surprise": 1}]})
    assert unknown.status_code == 422


async def test_provider_eval_with_llm_judge_tracks_tokens_cost_latency(gw, fake):
    await make_dataset(gw)
    r = await gw.post("/api/evaluations", json={
        "dataset": "math", "target": {"provider": "fake", "model": "fake-small"},
        "scorers": [{"name": "exact_match"}], "judge": {"provider": "fake", "model": "fake-small"}})
    assert r.status_code == 200, r.text
    ex = await wait_for(gw, r.json()["execution_id"])
    assert ex["status"] == "succeeded", ex
    rep = (await gw.get(f"/api/evaluations/{r.json()['run_id']}")).json()
    s = rep["summary"]
    assert s["scores"]["exact_match"]["mean"] == 1.0 and s["scores"]["judge"]["mean"] == 1.0
    assert s["tokens"] == {"input": 500, "output": 100}
    assert s["cost_usd"] == pytest.approx(5 * (100 * 1 + 20 * 2) / 1e6)
    assert s["latency_ms"]["p50"] == 2.0
    usage = (await gw.get("/api/usage")).json()
    assert usage["totals"]["calls"] == 10  # 5 generations + 5 judgments
    spans = (await gw.get("/api/traces", params={"execution_id": ex["execution_id"]})).json()
    assert {"evaluation", "llm.generate", "llm.judge"} <= {sp["name"] for sp in spans}


async def test_session_target_and_session_judge_round_trip(gw):
    await make_dataset(gw)
    r = (await gw.post("/api/evaluations", json={
        "dataset": "math", "target": {"provider": "session"}, "scorers": [{"name": "exact_match"}],
        "judge": {"provider": "session", "rubric": "correctness"}})).json()
    ex = await wait_for(gw, r["execution_id"])
    assert ex["status"] == "waiting" and ex["open_tasks"][0]["kind"] == "generate"
    task = (await gw.get("/api/tasks", params={"execution_id": r["execution_id"]})).json()[0]
    outputs = [{"item_id": it["item_id"], "output": "4" if it["item_id"] == "q2" else "wrong"} for it in task["payload"]["items"]]

    incomplete = await gw.post(f"/api/tasks/{task['task_id']}/submit", json={"response": {"outputs": outputs[:2]}})
    assert incomplete.status_code == 422 and "missing outputs" in incomplete.text

    ok = (await gw.post(f"/api/tasks/{task['task_id']}/submit", json={"response": {"outputs": outputs}})).json()
    assert ok["execution_status"] == "waiting"  # now waiting for judgments
    again = await gw.post(f"/api/tasks/{task['task_id']}/submit", json={"response": {"outputs": outputs}})
    assert again.status_code == 422 and "already resolved" in again.text

    jtask = (await gw.get("/api/tasks", params={"kind": "judge"})).json()[0]
    assert "rubric" in jtask["payload"] and jtask["payload"]["items"][0]["output"]
    judgments = [{"item_id": it["item_id"], "score": 1 if it["output"] == "4" else 0, "reason": "r"} for it in jtask["payload"]["items"]]
    done = (await gw.post(f"/api/tasks/{jtask['task_id']}/submit", json={"response": {"judgments": judgments}})).json()
    assert done["execution_status"] == "succeeded"
    s = (await gw.get(f"/api/evaluations/{r['run_id']}")).json()["summary"]
    assert s["scores"]["exact_match"]["mean"] == 0.2 and s["scores"]["judge"]["pass_rate"] == 0.2
    assert s["cost_usd"] == 0.0


async def test_budget_stops_paid_calls(settings, fake):
    from companion.providers import ProviderRegistry

    settings.max_run_cost_usd = 0.0001  # one call costs 0.00014
    reg = ProviderRegistry(settings)
    reg.register(fake)
    reg.pricing["fake-small"] = (1.0, 2.0)
    app = create_app(settings, reg)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
                                     headers={"Authorization": f"Bearer {settings.gateway_token()}"}) as c:
            await c.post("/api/datasets", json={"name": "m", "items": MATH})
            r = (await c.post("/api/evaluations", json={"dataset": "m", "target": {"provider": "fake", "model": "fake-small"},
                                                       "scorers": [{"name": "exact_match"}]})).json()
            await wait_for(c, r["execution_id"])
            s = (await c.get(f"/api/evaluations/{r['run_id']}")).json()["summary"]
    assert s["errors"] >= 1 and fake.calls < 5


async def test_permissions_are_enforced_and_audited(gw):
    core = gw.core
    core.settings.allow_execute = False
    await make_dataset(gw)
    r = await gw.post("/api/evaluations", json={"dataset": "math", "target": {"provider": "session"}, "scorers": [{"name": "exact_match"}]})
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"
    core.settings.allow_execute = True
    core.settings.allow_paid_calls = False
    r = await gw.post("/api/evaluations", json={"dataset": "math", "target": {"provider": "fake", "model": "fake-small"}, "scorers": [{"name": "exact_match"}]})
    assert r.status_code == 403
    audit = (await gw.get("/api/audit")).json()
    assert audit[0]["action"] == "dataset.create_version"


async def test_unconfigured_provider_and_unknown_scorer_rejected(gw):
    await make_dataset(gw)
    r = await gw.post("/api/evaluations", json={"dataset": "math", "target": {"provider": "openai", "model": "x"}, "scorers": [{"name": "exact_match"}]})
    assert r.status_code == 422 and "not configured" in r.text
    r = await gw.post("/api/evaluations", json={"dataset": "math", "target": {"provider": "session"}, "scorers": [{"name": "rm -rf"}]})
    assert r.status_code == 422


async def test_compare_flags_regression(gw):
    await make_dataset(gw)
    ids = []
    for model in ("fake-small", "fake-wrong"):
        r = (await gw.post("/api/evaluations", json={"dataset": "math", "target": {"provider": "fake", "model": model},
                                                    "scorers": [{"name": "exact_match"}]})).json()
        await wait_for(gw, r["execution_id"])
        ids.append(r["run_id"])
    cmp = (await gw.post("/api/evaluations/compare", json={"baseline_run_id": ids[0], "candidate_run_ids": [ids[1]]})).json()
    c = cmp["comparisons"][0]
    assert c["regression"] is True
    assert c["scorers"]["exact_match"]["losses"] == 5 and len(c["scorers"]["exact_match"]["newly_failing_items"]) == 5


async def test_cancel_waiting_execution(gw):
    await make_dataset(gw)
    r = (await gw.post("/api/evaluations", json={"dataset": "math", "target": {"provider": "session"}, "scorers": [{"name": "exact_match"}]})).json()
    await wait_for(gw, r["execution_id"])
    ex = (await gw.post(f"/api/executions/{r['execution_id']}/cancel")).json()
    assert ex["status"] == "cancelled"
    assert (await gw.get("/api/tasks", params={"execution_id": r["execution_id"]})).json() == []


async def test_experiments_group_runs(gw):
    await make_dataset(gw)
    assert (await gw.post("/api/experiments", json={"name": "prompt-v2", "tags": ["math"]})).status_code == 200
    r = (await gw.post("/api/evaluations", json={"dataset": "math", "experiment": "prompt-v2",
                                                "target": {"provider": "fake", "model": "fake-small"}, "scorers": [{"name": "exact_match"}]})).json()
    await wait_for(gw, r["execution_id"])
    found = (await gw.get("/api/experiments", params={"q": "prompt"})).json()
    assert found[0]["runs"][0]["run_id"] == r["run_id"] and found[0]["runs"][0]["summary"]["scores"]["exact_match"]["mean"] == 1.0


def test_refuses_public_bind(tmp_path, monkeypatch):
    from companion import gateway

    monkeypatch.setattr(gateway, "get_settings", lambda: Settings(home=tmp_path, host="0.0.0.0", _env_file=None))
    with pytest.raises(SystemExit):
        gateway.main()
