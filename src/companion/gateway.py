"""The local gateway: FastAPI app that owns storage, jobs and providers."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import platform
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from companion import __version__, agents, bridge, evals, reports, workflows
from companion.config import Settings, get_settings
from companion.core import TERMINAL, Core, NotFound, PermissionDenied
from companion.db import make_engine, migrate, sessionmaker
from companion.models import AuditLog, Execution, McpClient, SessionTask, TraceSpan, UsageRecord, utcnow
from companion.providers import ProviderError, ProviderRegistry
from companion.scorers import SCORERS
from companion.telemetry import flush_spans, setup_logging, setup_tracing, span_flush_loop

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
ACTORS = {"mcp", "dashboard", "api"}


class Heartbeat(BaseModel):
    client_name: str = Field(max_length=120)
    client_version: str = Field(default="", max_length=60)
    protocol_version: str = Field(default="", max_length=20)
    tool: str = Field(default="", max_length=80)


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[\w.\- ]+$")
    items: list[dict[str, Any]]
    description: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=500)


class ExperimentIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class CompareIn(BaseModel):
    baseline_run_id: str
    candidate_run_ids: list[str] = Field(min_length=1, max_length=10)
    regression_threshold: float = Field(default=0.05, ge=0, le=1)


class SubmitIn(BaseModel):
    response: dict[str, Any]


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header.split("]")[0] + "]"
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


def detect_desktop() -> dict[str, Any]:
    """Looks only for the app's install location. It never opens files inside
    it; credentials and session data are none of the gateway's business."""
    found: list[str] = []
    system = platform.system()
    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        if (local / "AnthropicClaude").exists():
            found.append(str(local / "AnthropicClaude"))
        packages = local / "Packages"
        if packages.exists():
            found += [str(p) for p in packages.glob("Claude_*")]
    elif system == "Darwin":
        if Path("/Applications/Claude.app").exists():
            found.append("/Applications/Claude.app")
    return {"platform": system, "installed": bool(found), "locations": found}


def create_app(settings: Settings | None = None, registry: ProviderRegistry | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_home()
    token = settings.gateway_token()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        setup_tracing()
        await migrate(settings)
        engine = make_engine(settings)
        sessions = sessionmaker(engine)
        core = Core(settings, sessions, registry or ProviderRegistry(settings))
        await core.recover_after_restart()
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
            core.checkpointer = saver
            app.state.core = core
            app.state.bridge = bridge.Bridge(core, settings.bridge_timeout_s)
            flusher = asyncio.create_task(span_flush_loop(sessions))
            try:
                yield
            finally:
                flusher.cancel()
                for t in list(core.tasks.values()):
                    t.cancel()
                await asyncio.gather(*core.tasks.values(), return_exceptions=True)
                await flush_spans(sessions)
                await engine.dispose()

    app = FastAPI(title="Claude Companion Gateway", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Blocks DNS-rebinding: a page on evil.example resolving to 127.0.0.1
        # still sends its own hostname in the Host header.
        if not settings.allow_non_local_bind and _hostname(request.headers.get("host", "")) not in LOCAL_HOSTS:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(PermissionDenied)
    async def _perm(_, e: PermissionDenied):
        return JSONResponse({"detail": str(e), "code": "permission_denied"}, status_code=403)

    @app.exception_handler(NotFound)
    async def _nf(_, e: NotFound):
        return JSONResponse({"detail": str(e), "code": "not_found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def _bad(_, e: ValueError):
        return JSONResponse({"detail": str(e), "code": "invalid_request"}, status_code=422)

    @app.exception_handler(ProviderError)
    async def _prov(_, e: ProviderError):
        return JSONResponse({"detail": str(e), "code": "provider_error"}, status_code=502)

    def auth(authorization: str = Header(default=""), x_companion_actor: str = Header(default="api")) -> str:
        supplied = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "missing or wrong gateway token")
        return x_companion_actor if x_companion_actor in ACTORS else "api"

    def core_of(request: Request) -> Core:
        return request.app.state.core

    Actor = Depends(auth)
    app.include_router(bridge.make_router(token, core_of))

    # health & system -----------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/system")
    async def system(request: Request, actor: str = Actor):
        core = core_of(request)
        async with core.sessions() as s:
            await s.execute(text("SELECT 1"))
            counts = dict((await s.execute(select(Execution.status, func.count()).group_by(Execution.status))).all())
            day_ago = utcnow() - timedelta(days=1)
            usage = (await s.execute(select(func.count(), func.sum(UsageRecord.ok == False)).where(UsageRecord.created_at >= day_ago))).one()  # noqa: E712
            open_tasks = await s.scalar(select(func.count()).select_from(SessionTask).where(SessionTask.status == "open"))
        return {
            "gateway": {"version": __version__, "python": sys.version.split()[0], **settings.public_view()},
            "database": {"ok": True},
            "desktop": detect_desktop(),
            "mcp_clients": await _mcp_clients(core),
            "providers": core.registry.describe(),
            "executions": {**counts, "active": len(core.tasks)},
            "open_session_tasks": open_tasks,
            "model_calls_24h": {"total": usage[0] or 0, "errors": int(usage[1] or 0)},
        }

    async def _mcp_clients(core: Core) -> list[dict]:
        async with core.sessions() as s:
            rows = (await s.scalars(select(McpClient).order_by(McpClient.last_seen.desc()))).all()
        now = datetime.now(timezone.utc)
        out = []
        for c in rows:
            last = c.last_seen if c.last_seen.tzinfo else c.last_seen.replace(tzinfo=timezone.utc)
            out.append({"client": c.client_name, "version": c.client_version, "protocol_version": c.protocol_version,
                        "first_seen": c.first_seen.isoformat(), "last_seen": last.isoformat(),
                        "seconds_since_last_call": int((now - last).total_seconds()), "tool_calls": c.tool_calls, "last_tool": c.last_tool})
        return out


    @app.post("/api/mcp/heartbeat")
    async def heartbeat(hb: Heartbeat, request: Request, actor: str = Actor):
        core = core_of(request)
        key = f"{hb.client_name}@{hb.client_version}"
        async with core.sessions() as s:
            row = await s.get(McpClient, key)
            if row is None:
                row = McpClient(client_key=key, client_name=hb.client_name, client_version=hb.client_version)
                s.add(row)
            row.protocol_version, row.last_seen = hb.protocol_version, utcnow()
            if hb.tool:
                row.tool_calls = (row.tool_calls or 0) + 1
                row.last_tool = hb.tool
            await s.commit()
        return {"ok": True}

    @app.get("/api/mcp/tools")
    async def mcp_tools(actor: str = Actor):
        from companion.mcp_server import server

        tools = await server.list_tools()
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema,
                 "read_only": bool(t.annotations and t.annotations.read_only_hint)} for t in tools]

    @app.get("/api/settings")
    async def get_public_settings(actor: str = Actor):
        return settings.public_view()

    # providers ------------------------------------------------------------
    @app.get("/api/providers")
    async def providers(request: Request, probe: bool = False, actor: str = Actor):
        core = core_of(request)
        listed = core.registry.describe()
        if probe:
            results = await asyncio.gather(*(core.registry.probe(p["name"]) for p in listed))
            for p, r in zip(listed, results):
                p["probe"] = {k: v for k, v in r.items() if k != "models"}
        return listed

    @app.get("/api/models")
    async def models(request: Request, provider: str | None = None, actor: str = Actor):
        core = core_of(request)
        names = [provider] if provider else [p["name"] for p in core.registry.describe() if p["configured"]]
        results = await asyncio.gather(*(core.registry.probe(n) for n in names))
        return {"session": {"note": "Target 'session' means the connected Claude session writes outputs itself; it is not an API model."},
                "providers": [{"provider": r["name"], "reachable": r["reachable"], "models": r.get("models", []),
                               "error": r.get("error")} for r in results]}

    # datasets -------------------------------------------------------------

    @app.get("/api/datasets")
    async def datasets(request: Request, actor: str = Actor):
        return await evals.list_datasets(core_of(request))

    @app.post("/api/datasets")
    async def create_dataset(body: DatasetIn, request: Request, actor: str = Actor):
        return await evals.create_dataset(core_of(request), body.name, body.items, body.description, body.note, actor)

    @app.get("/api/datasets/{ref}")
    async def dataset(ref: str, request: Request, version: int | None = None, actor: str = Actor):
        return await evals.get_dataset(core_of(request), ref, version)

    # experiments ----------------------------------------------------------

    @app.get("/api/experiments")
    async def experiments(request: Request, q: str = "", tag: str | None = None, limit: int = Query(20, le=100), actor: str = Actor):
        return await evals.search_experiments(core_of(request), q, tag, limit)

    @app.post("/api/experiments")
    async def create_experiment(body: ExperimentIn, request: Request, actor: str = Actor):
        return await evals.create_experiment(core_of(request), body.name, body.description, body.tags, actor)

    # evaluations ----------------------------------------------------------
    @app.get("/api/scorers")
    async def scorers(actor: str = Actor):
        return {"scorers": {k: d for k, (_, d) in SCORERS.items()}, "judge_rubrics": evals.RUBRICS}

    @app.post("/api/evaluations")
    async def run_eval(body: evals.RunRequest, request: Request, actor: str = Actor):
        return await evals.start_run(core_of(request), body, actor)

    @app.get("/api/evaluations")
    async def runs(request: Request, limit: int = Query(50, le=200), actor: str = Actor):
        return await evals.list_runs(core_of(request), limit)

    @app.get("/api/evaluations/{run_id}")
    async def run_report(run_id: str, request: Request, items: bool = True, failures_only: bool = False, actor: str = Actor):
        return await evals.report(core_of(request), run_id, items, failures_only)


    def save_report(name: str, content: str) -> str:
        path = settings.home / "reports" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    @app.post("/api/evaluations/compare")
    async def compare(body: CompareIn, request: Request, format: str = "json", actor: str = Actor):
        core = core_of(request)
        result = await evals.compare(core, body.baseline_run_id, body.candidate_run_ids, body.regression_threshold)
        if format == "html":
            labels = {}
            for rid in [body.baseline_run_id, *body.candidate_run_ids]:
                rep = await evals.report(core, rid, include_items=False)
                labels[rid] = f"{reports._target_label(rep['target'])} ({rid})"
            html = reports.comparison_html(result, labels)
            path = save_report(f"compare-{body.baseline_run_id}-{'-'.join(body.candidate_run_ids)}.html"[:150], html)
            return HTMLResponse(html, headers={"X-Report-Path": path})
        return result

    @app.get("/api/evaluations/{run_id}/export")
    async def export(run_id: str, request: Request, format: str = Query("json", pattern="^(json|csv|html)$"), actor: str = Actor):
        rep = await evals.report(core_of(request), run_id, include_items=True)
        if format == "csv":
            body, media = reports.to_csv(rep), "text/csv"
        elif format == "html":
            body, media = reports.to_html(rep), "text/html"
        else:
            body, media = json.dumps(rep, indent=2, default=str), "application/json"
        path = save_report(f"run-{run_id}.{format}", body)
        return Response(body, media_type=media, headers={"X-Report-Path": path})

    @app.get("/api/evaluations/{run_id}/failures")
    async def failures(run_id: str, request: Request, limit: int = Query(15, ge=1, le=50), actor: str = Actor):
        _, results, items = await evals._load(core_of(request), run_id)
        return {"run_id": run_id, **agents.analyze_failures(results, items, limit)}

    # agents ---------------------------------------------------------------
    @app.get("/api/agents")
    async def list_agents(request: Request, actor: str = Actor):
        core = core_of(request)
        path = agents.registry_path(core)
        try:
            found = agents.load_agents(core)
        except Exception as e:  # a typo in the TOML should show up, not crash the page
            return {"registry": str(path), "agents": [], "error": f"{type(e).__name__}: {e}"}
        return {"registry": str(path), "exists": path.exists(),
                "agents": [{"name": a.name, "description": a.description, "command": a.command, "cwd": a.cwd,
                            "timeout_s": a.timeout_s, "env_keys": sorted(a.env)} for a in found.values()]}

    @app.post("/api/agents/runs")
    async def run_agent(body: agents.AgentRunRequest, request: Request, actor: str = Actor):
        return await agents.start_agent_runs(core_of(request), body, actor)

    # workflows ------------------------------------------------------------
    @app.get("/api/workflows")
    async def list_workflows(actor: str = Actor):
        return workflows.WORKFLOWS

    @app.post("/api/workflows/{name}")
    async def run_workflow(name: str, body: workflows.WorkflowRequest, request: Request, actor: str = Actor):
        return await workflows.start_workflow(core_of(request), name, body, actor)

    # executions & tasks -----------------------------------------------------
    def ex_view(ex: Execution) -> dict:
        return {"execution_id": ex.id, "kind": ex.kind, "status": ex.status, "progress": ex.progress, "message": ex.message,
                "error": ex.error, "result": ex.result, "params": ex.params, "actor": ex.actor,
                "created_at": ex.created_at.isoformat(), "updated_at": ex.updated_at.isoformat(), "terminal": ex.status in TERMINAL}

    @app.get("/api/executions")
    async def executions(request: Request, status: str | None = None, kind: str | None = None, limit: int = Query(50, le=200), actor: str = Actor):
        q = select(Execution).order_by(Execution.created_at.desc()).limit(limit)
        if status:
            q = q.where(Execution.status == status)
        if kind:
            q = q.where(Execution.kind == kind)
        async with core_of(request).sessions() as s:
            return [ex_view(e) for e in (await s.scalars(q)).all()]

    @app.get("/api/executions/{execution_id}")
    async def execution(execution_id: str, request: Request, actor: str = Actor):
        core = core_of(request)
        ex = await core.get_execution(execution_id)
        view = ex_view(ex)
        view["open_tasks"] = [{"task_id": t.id, "kind": t.kind} for t in await core.list_tasks(execution_id=execution_id)]
        return view

    @app.post("/api/executions/{execution_id}/cancel")
    async def cancel(execution_id: str, request: Request, actor: str = Actor):
        core = core_of(request)
        core.require("execute", actor)
        ex = await core.cancel(execution_id)
        await core.audit(actor, "execution.cancel", execution_id)
        return ex_view(ex)

    def task_view(t: SessionTask) -> dict:
        return {"task_id": t.id, "execution_id": t.execution_id, "kind": t.kind, "status": t.status, "payload": t.payload,
                "response": t.response, "resolved_by": t.resolved_by, "created_at": t.created_at.isoformat()}

    @app.get("/api/tasks")
    async def tasks(request: Request, status: str = "open", kind: str | None = None, execution_id: str | None = None, actor: str = Actor):
        return [task_view(t) for t in await core_of(request).list_tasks(status, kind, execution_id)]


    _submit_lock = asyncio.Lock()

    @app.post("/api/tasks/{task_id}/submit")
    async def submit(task_id: str, body: SubmitIn, request: Request, actor: str = Actor):
        core = core_of(request)
        async with _submit_lock:  # one submission per task, even if a client retries
            async with core.sessions() as s:
                task = await s.get(SessionTask, task_id)
            if task is None:
                raise NotFound(f"task {task_id} not found")
            if task.status != "open":
                raise ValueError(f"task {task_id} is already {task.status}")
            evals.ensure_not_approval(task, core, actor)
            core.require("execute", actor)
            if task.kind == "llm":
                raise ValueError("answer model requests with answer_model_request, not submit_session_task")
            if task.payload.get("source") == "evaluation":
                out = await evals.submit_eval_task(core, task, body.response)
            else:
                out = await workflows.submit_workflow_task(core, task, body.response, actor)
        await core.audit(actor, "task.submit", task_id, kind=task.kind)
        return out

    # traces, usage, audit ---------------------------------------------------
    @app.get("/api/traces")
    async def traces(request: Request, execution_id: str | None = None, limit: int = Query(500, le=2000), actor: str = Actor):
        core = core_of(request)
        await flush_spans(core.sessions)
        q = select(TraceSpan).order_by(TraceSpan.started_at.desc()).limit(limit)
        if execution_id:
            q = select(TraceSpan).where(TraceSpan.execution_id == execution_id).order_by(TraceSpan.started_at).limit(limit)
        async with core.sessions() as s:
            spans = (await s.scalars(q)).all()
        return [{"trace_id": t.trace_id, "span_id": t.span_id, "parent_span_id": t.parent_span_id, "execution_id": t.execution_id,
                 "name": t.name, "started_at": t.started_at.isoformat(), "duration_ms": round(t.duration_ms, 2), "status": t.status,
                 "attributes": t.attributes} for t in spans]

    @app.get("/api/usage")
    async def usage(request: Request, days: int = Query(7, ge=1, le=365), actor: str = Actor):
        since = utcnow() - timedelta(days=days)
        async with core_of(request).sessions() as s:
            rows = (await s.execute(
                select(UsageRecord.provider, UsageRecord.model, func.count(), func.sum(UsageRecord.input_tokens),
                       func.sum(UsageRecord.output_tokens), func.sum(UsageRecord.cost_usd), func.avg(UsageRecord.latency_ms),
                       func.sum(UsageRecord.ok == False))  # noqa: E712
                .where(UsageRecord.created_at >= since).group_by(UsageRecord.provider, UsageRecord.model)
            )).all()
            recent = (await s.scalars(select(UsageRecord).where(UsageRecord.created_at >= since)
                                      .order_by(UsageRecord.created_at.desc()).limit(500))).all()
        by_day: dict[str, dict] = {}
        for r in recent:
            d = by_day.setdefault(r.created_at.date().isoformat(), {"calls": 0, "cost_usd": 0.0, "tokens": 0})
            d["calls"] += 1
            d["cost_usd"] = round(d["cost_usd"] + (r.cost_usd or 0), 6)
            d["tokens"] += r.input_tokens + r.output_tokens
        models_ = [{"provider": p, "model": m, "calls": c, "input_tokens": int(i or 0), "output_tokens": int(o or 0),
                    "cost_usd": round(cost, 6) if cost is not None else None, "avg_latency_ms": round(lat or 0, 1),
                    "errors": int(err or 0)} for p, m, c, i, o, cost, lat, err in rows]
        return {"days": days, "by_model": models_, "by_day": dict(sorted(by_day.items())),
                "totals": {"calls": sum(m["calls"] for m in models_), "errors": sum(m["errors"] for m in models_),
                           "cost_usd": round(sum(m["cost_usd"] or 0 for m in models_), 6)},
                "note": "Counts only calls the gateway made with its own provider keys. Claude Desktop subscription usage is not visible here."}

    @app.get("/api/audit")
    async def audit(request: Request, limit: int = Query(100, le=500), actor: str = Actor):
        async with core_of(request).sessions() as s:
            rows = (await s.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))).all()
        return [{"at": a.created_at.isoformat(), "actor": a.actor, "action": a.action, "target": a.target,
                 "outcome": a.outcome, "details": a.details} for a in rows]

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    setup_logging()
    if settings.host not in LOCAL_HOSTS and not settings.allow_non_local_bind:
        raise SystemExit(f"refusing to bind {settings.host}; set COMPANION_ALLOW_NON_LOCAL_BIND=true if you really mean it")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
