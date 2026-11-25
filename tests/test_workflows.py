import httpx

from companion.gateway import create_app
from companion.providers import ProviderRegistry
from tests.conftest import FakeProvider, wait_for


def as_actor(gw, actor):
    return {"X-Companion-Actor": actor}


async def test_session_workflow_with_human_approval(gw):
    r = (await gw.post("/api/workflows/draft_review", json={"task": "Write a haiku about tests", "provider": "session"},
                       headers=as_actor(gw, "mcp"))).json()
    eid = r["execution_id"]

    ex = await wait_for(gw, eid)
    t = (await gw.get("/api/tasks", params={"execution_id": eid})).json()[0]
    assert ex["status"] == "waiting" and t["kind"] == "generate" and t["payload"]["role"] == "drafter"
    await gw.post(f"/api/tasks/{t['task_id']}/submit", json={"response": {"text": "green bars at dawn"}}, headers=as_actor(gw, "mcp"))

    await wait_for(gw, eid, states=("waiting",))
    t = (await gw.get("/api/tasks", params={"execution_id": eid})).json()[0]
    assert t["payload"]["role"] == "critic"
    await gw.post(f"/api/tasks/{t['task_id']}/submit", json={"response": {"text": '{"verdict": "approve", "feedback": "nice"}'}},
                  headers=as_actor(gw, "mcp"))

    await wait_for(gw, eid, states=("waiting",))
    t = (await gw.get("/api/tasks", params={"execution_id": eid})).json()[0]
    assert t["kind"] == "approval" and t["payload"]["draft"] == "green bars at dawn"

    denied = await gw.post(f"/api/tasks/{t['task_id']}/submit", json={"response": {"approved": True}}, headers=as_actor(gw, "mcp"))
    assert denied.status_code == 403 and "dashboard" in denied.text

    ok = await gw.post(f"/api/tasks/{t['task_id']}/submit", json={"response": {"approved": True, "comment": "ship it"}},
                       headers=as_actor(gw, "dashboard"))
    assert ok.status_code == 200
    ex = await wait_for(gw, eid, states=("succeeded", "failed"))
    assert ex["status"] == "succeeded" and ex["result"]["final"] == "green bars at dawn" and ex["result"]["approved"] is True
    names = {s["name"] for s in (await gw.get("/api/traces", params={"execution_id": eid})).json()}
    assert {"agent.drafter", "agent.critic", "workflow.step"} <= names


async def test_critic_revise_loop_respects_max_revisions(gw):
    r = (await gw.post("/api/workflows/draft_review", json={"task": "t", "provider": "session", "require_approval": False,
                                                           "max_revisions": 1})).json()
    eid = r["execution_id"]
    roles = []
    for _ in range(10):
        ex = await wait_for(gw, eid, states=("waiting", "succeeded", "failed"))
        if ex["status"] != "waiting":
            break
        t = (await gw.get("/api/tasks", params={"execution_id": eid})).json()[0]
        roles.append(t["payload"]["role"])
        text = '{"verdict": "revise", "feedback": "more"}' if t["payload"]["role"] == "critic" else f"draft {len(roles)}"
        await gw.post(f"/api/tasks/{t['task_id']}/submit", json={"response": {"text": text}})
    assert roles == ["drafter", "critic", "drafter", "critic"]
    assert ex["status"] == "succeeded" and ex["result"]["revisions"] == 1


async def test_provider_workflow_runs_end_to_end(gw):
    r = (await gw.post("/api/workflows/draft_review", json={"task": "Summarise X", "provider": "fake", "model": "fake-small",
                                                           "require_approval": False})).json()
    ex = await wait_for(gw, r["execution_id"], states=("succeeded", "failed"))
    assert ex["status"] == "succeeded", ex
    assert ex["result"]["final"].startswith("draft for:")


async def test_waiting_workflow_survives_gateway_restart(settings):
    def app_with_fake():
        reg = ProviderRegistry(settings)
        reg.register(FakeProvider())
        return create_app(settings, reg)

    headers = {"Authorization": f"Bearer {settings.gateway_token()}"}
    app = app_with_fake()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=headers) as c:
            eid = (await c.post("/api/workflows/draft_review", json={"task": "t", "provider": "session", "require_approval": False})).json()["execution_id"]
            await wait_for(c, eid)

    app = app_with_fake()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=headers) as c:
            t = (await c.get("/api/tasks", params={"execution_id": eid})).json()[0]
            await c.post(f"/api/tasks/{t['task_id']}/submit", json={"response": {"text": "first draft"}})
            ex = await wait_for(c, eid, states=("waiting",))
            t = (await c.get("/api/tasks", params={"execution_id": eid})).json()[0]
            assert ex["status"] == "waiting" and t["payload"]["role"] == "critic" and "first draft" in t["payload"]["prompt"]
