"""MCP server for the Claude Desktop Code tab (stdio transport).

A thin, validated front for the gateway's HTTP API. The Code tab spawns this
process itself from .mcp.json, so it runs inside the user's already signed-in
Desktop session; nothing here logs in to anything, and nothing here can make
the Desktop model run inference on its own. When an evaluation or workflow
targets "session", the gateway queues the prompts as session tasks and the
Claude model in the chat answers them through list_session_tasks and
submit_session_task, in plain view of the user.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from companion import __version__
from companion.config import get_settings

INSTRUCTIONS = """\
Tools for the user's local AI gateway (evaluations, datasets, experiments, agent workflows, traces, usage).
- The gateway calls external model APIs only with keys the user configured for it; check inspect_providers.
- Without any keys, use target provider "session": the gateway queues work as session tasks, you write
  the outputs or judgments yourself via list_session_tasks / submit_session_task. Be honest: grade as an
  impartial judge and answer as the model under test, not as yourself grading your own answers leniently.
- Long jobs return an execution_id; poll get_execution_status rather than re-running.
- Human approvals in workflows belong to the user; do not submit approval tasks unless the user tells you their decision.
- Session model bridge: the user's own agents can use you as their model by pointing an OpenAI- or
  Anthropic-compatible client at the gateway (base URL http://127.0.0.1:8765/v1, API key = gateway token).
  When the user asks you to serve their agent, loop: wait_for_model_request, then answer_model_request,
  replying exactly as that agent's model would (plain content and/or tool calls from the offered tools).
"""

server = MCPServer(name="claude-companion", version=__version__, instructions=INSTRUCTIONS)

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
EXECUTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

_http: httpx.AsyncClient | None = None
_started_gateway: subprocess.Popen | None = None


def _gateway_url() -> str:
    s = get_settings()
    return os.environ.get("COMPANION_GATEWAY_URL", f"http://127.0.0.1:{s.port}")


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            base_url=_gateway_url(),
            timeout=httpx.Timeout(30, connect=3),
            headers={"Authorization": f"Bearer {get_settings().gateway_token()}", "X-Companion-Actor": "mcp"},
        )
    return _http


async def _gateway_up() -> bool:
    try:
        return (await _client().get("/api/health", timeout=2)).status_code == 200
    except httpx.HTTPError:
        return False


async def _ensure_gateway() -> None:
    """Start the gateway in the background if it is not running, so the Code
    tab works without a separate terminal. COMPANION_AUTOSTART=false turns this off."""
    global _started_gateway
    if await _gateway_up():
        return
    if os.environ.get("COMPANION_AUTOSTART", "true").lower() == "false":
        raise ToolError(f"gateway not reachable at {_gateway_url()}; start it with `uv run companion-gateway`")
    # Start another one if the gateway we started earlier has since exited; only
    # refuse while our own child is still alive but not answering.
    if _started_gateway is not None and _started_gateway.poll() is None:
        raise ToolError(f"the gateway started earlier is not answering at {_gateway_url()}; "
                        f"see {get_settings().home / 'gateway.log'}")
    s = get_settings()
    s.ensure_home()
    log = open(s.home / "gateway.log", "ab")
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
    _started_gateway = subprocess.Popen([sys.executable, "-m", "companion.gateway"], stdout=log, stderr=log,
                                        stdin=subprocess.DEVNULL, creationflags=flags,
                                        start_new_session=sys.platform != "win32", close_fds=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        if await _gateway_up():
            return
    raise ToolError(f"started the gateway but it did not come up; see {s.home / 'gateway.log'}")


def _client_info(ctx: Context | None) -> tuple[str, str, str]:
    try:
        params = ctx.session.client_params if ctx else None
        info = getattr(params, "client_info", None) or getattr(params, "clientInfo", None)
        proto = str(getattr(params, "protocol_version", None) or getattr(params, "protocolVersion", "") or "")
        return (getattr(info, "name", "unknown"), getattr(info, "version", ""), proto)
    except Exception:
        return ("unknown", "", "")


async def _call(ctx: Context | None, tool: str, method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
    await _ensure_gateway()
    name, version, proto = _client_info(ctx)
    try:
        await _client().post("/api/mcp/heartbeat", json={"client_name": name, "client_version": version,
                                                         "protocol_version": proto, "tool": tool})
        r = await _client().request(method, path, json=json, params={k: v for k, v in (params or {}).items() if v is not None})
    except httpx.TimeoutException:
        raise ToolError("gateway timed out") from None
    except httpx.HTTPError as e:
        raise ToolError(f"gateway request failed: {e}") from None
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        raise ToolError(f"{detail} (HTTP {r.status_code})")
    return r.json()


async def _wait(ctx: Context, execution_id: str, wait_seconds: int) -> dict:
    deadline = time.monotonic() + wait_seconds
    while True:
        ex = await _call(ctx, "get_execution_status", "GET", f"/api/executions/{execution_id}")
        await ctx.report_progress(ex["progress"], 1.0, ex["message"] or ex["status"])
        if ex["terminal"] or ex["status"] == "waiting" or time.monotonic() >= deadline:
            return ex
        await asyncio.sleep(1)


ProviderName = Annotated[str, Field(description="Provider name from inspect_providers, or 'session'", max_length=40)]


@server.tool(annotations=READ)
async def inspect_system_health(ctx: Context) -> dict[str, Any]:
    """Gateway status: database, Claude Desktop install detection, MCP clients seen, provider
    configuration, executions and open session tasks. Desktop connection is not model API access."""
    return await _call(ctx, "inspect_system_health", "GET", "/api/system")


@server.tool(annotations=READ)
async def inspect_providers(
    ctx: Context,
    probe: Annotated[bool, Field(description="Also make a live list-models call to each configured provider")] = False,
) -> dict[str, Any]:
    """List model providers the gateway can call with its own keys, and whether each is configured/reachable."""
    providers = await _call(ctx, "inspect_providers", "GET", "/api/providers", params={"probe": probe})
    return {"providers": providers, "session_target": "Always available: the connected Claude session supplies outputs via session tasks."}


@server.tool(annotations=READ)
async def list_available_models(
    ctx: Context, provider: Annotated[str | None, Field(description="Only this provider", max_length=40)] = None
) -> dict[str, Any]:
    """Models reported live by each configured provider."""
    return await _call(ctx, "list_available_models", "GET", "/api/models", params={"provider": provider})


@server.tool(annotations=READ)
async def list_datasets(ctx: Context) -> dict[str, Any]:
    """Evaluation datasets with their latest version numbers."""
    return {"datasets": await _call(ctx, "list_datasets", "GET", "/api/datasets")}


@server.tool(annotations=WRITE)
async def create_evaluation_dataset(
    ctx: Context,
    name: Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[\w.\- ]+$")],
    items: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=2000, description=(
        "Items: {id?, input: str | [{role, content}], expected?, context?, expected_tools?, "
        "expected_trajectory?, relevant_ids?, pattern?, metadata?}"))],
    description: Annotated[str, Field(max_length=2000)] = "",
    note: Annotated[str, Field(max_length=500, description="Change note for this version")] = "",
) -> dict[str, Any]:
    """Create a dataset, or add a new immutable version if the name already exists."""
    return await _call(ctx, "create_evaluation_dataset", "POST", "/api/datasets",
                       json={"name": name, "items": items, "description": description, "note": note})


@server.tool(annotations=EXECUTE)
async def run_evaluation(
    ctx: Context,
    dataset: Annotated[str, Field(description="Dataset name or id", max_length=120)],
    provider: ProviderName,
    model: Annotated[str | None, Field(description="Model id; required unless provider is 'session'", max_length=160)] = None,
    scorers: Annotated[list[Literal["exact_match", "contains", "regex", "json_valid", "json_fields", "numeric_close",
                                    "tool_selection", "tool_success", "trajectory_match", "recall_at_k", "mrr"]],
                       Field(description="Deterministic scorers to apply", max_length=11)] = [],  # noqa: B006
    scorer_params: Annotated[dict[str, dict[str, Any]], Field(description="Per-scorer params, e.g. {'recall_at_k': {'k': 3}}")] = {},  # noqa: B006
    judge_provider: Annotated[str | None, Field(description="LLM judge provider, or 'session' to judge in this chat", max_length=40)] = None,
    judge_model: Annotated[str | None, Field(max_length=160)] = None,
    judge_rubric: Annotated[str, Field(description="'correctness', 'hallucination', 'helpfulness' or custom text", max_length=4000)] = "correctness",
    system_prompt: Annotated[str, Field(max_length=20_000)] = "",
    version: Annotated[int | None, Field(ge=1)] = None,
    experiment: Annotated[str | None, Field(description="Experiment name or id", max_length=160)] = None,
    wait_seconds: Annotated[int, Field(ge=0, le=55, description="Wait this long for completion, reporting progress")] = 20,
) -> dict[str, Any]:
    """Start a batch evaluation. Returns execution_id/run_id; with provider 'session' the run waits for you
    to submit outputs via session tasks."""
    body: dict[str, Any] = {
        "dataset": dataset, "version": version, "experiment": experiment, "system_prompt": system_prompt,
        "target": {"provider": provider, "model": model},
        "scorers": [{"name": s, "params": scorer_params.get(s, {})} for s in scorers],
    }
    if judge_provider:
        body["judge"] = {"provider": judge_provider, "model": judge_model, "rubric": judge_rubric}
    started = await _call(ctx, "run_evaluation", "POST", "/api/evaluations", json=body)
    ex = await _wait(ctx, started["execution_id"], wait_seconds) if wait_seconds else {"status": started["status"]}
    return {**started, "status": ex["status"], "message": ex.get("message"), "open_tasks": ex.get("open_tasks", [])}


@server.tool(annotations=READ)
async def get_execution_status(
    ctx: Context,
    execution_id: Annotated[str, Field(max_length=32)],
    wait_seconds: Annotated[int, Field(ge=0, le=55, description="Wait for a terminal or waiting state")] = 0,
) -> dict[str, Any]:
    """Status, progress, result and open session tasks of an evaluation or workflow execution."""
    if wait_seconds:
        return await _wait(ctx, execution_id, wait_seconds)
    return await _call(ctx, "get_execution_status", "GET", f"/api/executions/{execution_id}")


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
async def cancel_execution(ctx: Context, execution_id: Annotated[str, Field(max_length=32)]) -> dict[str, Any]:
    """Cancel a running or waiting execution. Audited."""
    return await _call(ctx, "cancel_execution", "POST", f"/api/executions/{execution_id}/cancel")


@server.tool(annotations=READ)
async def list_session_tasks(
    ctx: Context,
    execution_id: Annotated[str | None, Field(max_length=32)] = None,
    kind: Annotated[Literal["generate", "judge", "approval"] | None, Field()] = None,
) -> dict[str, Any]:
    """Open work the gateway is waiting on: outputs to write ('generate'), outputs to grade ('judge'),
    or human approvals ('approval', which belong to the user)."""
    tasks = await _call(ctx, "list_session_tasks", "GET", "/api/tasks", params={"execution_id": execution_id, "kind": kind})
    return {"tasks": tasks}


@server.tool(annotations=EXECUTE)
async def submit_session_task(
    ctx: Context,
    task_id: Annotated[str, Field(max_length=32)],
    response: Annotated[dict[str, Any], Field(description=(
        "generate (eval): {outputs: [{item_id, output}]}; generate (workflow): {text}; "
        "judge: {judgments: [{item_id, score 0-1, reason}]}; approval: {approved, comment}"))],
) -> dict[str, Any]:
    """Submit the response for one session task. The gateway validates it and resumes the execution."""
    return await _call(ctx, "submit_session_task", "POST", f"/api/tasks/{task_id}/submit", json={"response": response})


@server.tool(annotations=READ)
async def retrieve_evaluation_report(
    ctx: Context,
    run_id: Annotated[str, Field(max_length=32)],
    include_items: bool = True,
    failures_only: bool = False,
) -> dict[str, Any]:
    """Summary (per-scorer mean and pass rate, latency, tokens, cost) and per-item results of a run."""
    return await _call(ctx, "retrieve_evaluation_report", "GET", f"/api/evaluations/{run_id}",
                       params={"items": include_items, "failures_only": failures_only})


@server.tool(annotations=READ)
async def compare_model_results(
    ctx: Context,
    baseline_run_id: Annotated[str, Field(max_length=32)],
    candidate_run_ids: Annotated[list[str], Field(min_length=1, max_length=10)],
    regression_threshold: Annotated[float, Field(ge=0, le=1, description="Mean drop that counts as a regression")] = 0.05,
) -> dict[str, Any]:
    """Pairwise per-item comparison (wins/losses/ties per scorer), regressions, latency and cost deltas."""
    return await _call(ctx, "compare_model_results", "POST", "/api/evaluations/compare",
                       json={"baseline_run_id": baseline_run_id, "candidate_run_ids": candidate_run_ids,
                             "regression_threshold": regression_threshold})


@server.tool(annotations=EXECUTE)
async def execute_agent_workflow(
    ctx: Context,
    task: Annotated[str, Field(min_length=1, max_length=20_000)],
    workflow: Literal["draft_review"] = "draft_review",
    provider: ProviderName = "session",
    model: Annotated[str | None, Field(max_length=160)] = None,
    require_approval: bool = True,
    max_revisions: Annotated[int, Field(ge=0, le=5)] = 2,
    wait_seconds: Annotated[int, Field(ge=0, le=55)] = 20,
) -> dict[str, Any]:
    """Run a LangGraph multi-agent workflow (supervisor, drafter, critic, optional human approval).
    With provider 'session' each agent step becomes a session task for you to answer."""
    started = await _call(ctx, "execute_agent_workflow", "POST", f"/api/workflows/{workflow}",
                          json={"task": task, "provider": provider, "model": model,
                                "require_approval": require_approval, "max_revisions": max_revisions})
    ex = await _wait(ctx, started["execution_id"], wait_seconds) if wait_seconds else {"status": started["status"]}
    return {**started, "status": ex["status"], "message": ex.get("message"), "result": ex.get("result"),
            "open_tasks": ex.get("open_tasks", [])}


@server.tool(annotations=READ)
async def inspect_agent_trace(ctx: Context, execution_id: Annotated[str, Field(max_length=32)]) -> dict[str, Any]:
    """Execution spans (agents, model calls, durations, token counts) for one execution."""
    spans = await _call(ctx, "inspect_agent_trace", "GET", "/api/traces", params={"execution_id": execution_id})
    ex = await _call(ctx, "inspect_agent_trace", "GET", f"/api/executions/{execution_id}")
    return {"execution": ex, "spans": spans}


@server.tool(annotations=WRITE)
async def create_experiment(
    ctx: Context,
    name: Annotated[str, Field(min_length=1, max_length=160)],
    description: Annotated[str, Field(max_length=4000)] = "",
    tags: Annotated[list[str], Field(max_length=20)] = [],  # noqa: B006
) -> dict[str, Any]:
    """Create an experiment to group related evaluation runs."""
    return await _call(ctx, "create_experiment", "POST", "/api/experiments", json={"name": name, "description": description, "tags": tags})


@server.tool(annotations=READ)
async def search_experiments(
    ctx: Context,
    query: Annotated[str, Field(max_length=200)] = "",
    tag: Annotated[str | None, Field(max_length=60)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Find experiments by name/description/tag, with their runs and summaries."""
    return {"experiments": await _call(ctx, "search_experiments", "GET", "/api/experiments",
                                       params={"q": query, "tag": tag, "limit": limit})}


@server.tool(annotations=READ)
async def list_agents(ctx: Context) -> dict[str, Any]:
    """Agents registered for evaluation. Agents are added only by editing the registry file shown
    (agents.toml); tools cannot register commands."""
    return await _call(ctx, "list_agents", "GET", "/api/agents")


@server.tool(annotations=EXECUTE)
async def run_agent_evaluation(
    ctx: Context,
    agent: Annotated[str, Field(max_length=60, description="Registered agent name (list_agents)")],
    dataset: Annotated[str, Field(max_length=120)],
    scorers: Annotated[list[Literal["exact_match", "contains", "regex", "json_valid", "json_fields", "numeric_close",
                                    "tool_selection", "tool_success", "trajectory_match", "recall_at_k", "mrr"]],
                       Field(max_length=11)] = [],  # noqa: B006
    scorer_params: Annotated[dict[str, dict[str, Any]], Field(description="Per-scorer params")] = {},  # noqa: B006
    variants: Annotated[list[dict[str, Any]], Field(max_length=12, description=(
        "Versions to compare, each {label, config}; config reaches the agent as task['config'] "
        "(prompt version, model, temperature...). One run per variant."))] = [],  # noqa: B006
    judge_provider: Annotated[str | None, Field(max_length=40, description="Provider for an LLM judge, or 'session'")] = None,
    judge_model: Annotated[str | None, Field(max_length=160)] = None,
    judge_rubric: Annotated[str, Field(max_length=4000)] = "correctness",
    parallel: Annotated[int, Field(ge=1, le=8, description="Items run at once (bridge calls are still served one at a time)")] = 1,
    version: Annotated[int | None, Field(ge=1)] = None,
    experiment: Annotated[str | None, Field(max_length=160)] = None,
) -> dict[str, Any]:
    """Run a registered agent over every item of a dataset (one process per item), capture its answer,
    tool calls, steps and model calls, then score. Returns immediately with one run per variant.
    If the agent uses the session bridge, keep serving with wait_for_model_request while it runs."""
    body: dict[str, Any] = {"agent": agent, "dataset": dataset, "version": version, "parallel": parallel,
                            "experiment": experiment,
                            "scorers": [{"name": s, "params": scorer_params.get(s, {})} for s in scorers]}
    if variants:
        body["variants"] = variants
    if judge_provider:
        body["judge"] = {"provider": judge_provider, "model": judge_model, "rubric": judge_rubric}
    return await _call(ctx, "run_agent_evaluation", "POST", "/api/agents/runs", json=body)


@server.tool(annotations=READ)
async def analyze_failures(
    ctx: Context,
    run_id: Annotated[str, Field(max_length=32)],
    limit: Annotated[int, Field(ge=1, le=50)] = 15,
) -> dict[str, Any]:
    """Failing items of a run grouped by cause (timeouts, crashes, agent errors, tool errors, wrong
    answers per scorer) with examples: input, output, expected, tool sequence, stderr. Use it to explain
    failures and recommend fixes."""
    return await _call(ctx, "analyze_failures", "GET", f"/api/evaluations/{run_id}/failures", params={"limit": limit})


@server.tool(annotations=WRITE)
async def export_report(
    ctx: Context,
    run_id: Annotated[str, Field(max_length=32)],
    format: Literal["html", "csv", "json"] = "html",
    compare_with: Annotated[list[str], Field(max_length=10, description="If given: an HTML comparison of these runs against run_id as baseline")] = [],  # noqa: B006
) -> dict[str, Any]:
    """Write a report file to the gateway's reports folder and return its path: a run report
    (HTML with charts and per-item trajectories, CSV, or JSON) or a baseline comparison."""
    await _ensure_gateway()
    try:
        if compare_with:
            r = await _client().post("/api/evaluations/compare", params={"format": "html"},
                                     json={"baseline_run_id": run_id, "candidate_run_ids": compare_with})
        else:
            r = await _client().get(f"/api/evaluations/{run_id}/export", params={"format": format})
    except httpx.HTTPError as e:
        raise ToolError(f"gateway request failed: {e}") from None
    if r.status_code >= 400:
        raise ToolError(f"{r.text[:300]} (HTTP {r.status_code})")
    return {"path": r.headers.get("X-Report-Path"), "bytes": len(r.content),
            "format": "html" if compare_with else format}


@server.tool(annotations=READ)
async def wait_for_model_request(
    ctx: Context,
    wait_seconds: Annotated[int, Field(ge=0, le=55, description="How long to wait for the next request")] = 45,
) -> dict[str, Any]:
    """Session model bridge: claim the next model call from an agent pointed at the gateway's
    /v1/chat/completions or /v1/messages endpoint (model claude-session). You then act as that agent's
    model and reply with answer_model_request. Each call claims a different request, so several helpers
    can serve in parallel. Returns request=null if none arrived; call again to keep serving."""
    await _ensure_gateway()
    name, version, proto = _client_info(ctx)
    try:
        await _client().post("/api/mcp/heartbeat", json={"client_name": name, "client_version": version,
                                                         "protocol_version": proto, "tool": "wait_for_model_request"})
        r = await _client().get("/api/bridge/next", params={"wait": wait_seconds}, timeout=wait_seconds + 15)
    except httpx.HTTPError as e:
        raise ToolError(f"gateway request failed: {e}") from None
    if r.status_code >= 400:
        raise ToolError(f"{r.text[:300]} (HTTP {r.status_code})")
    return r.json()


@server.tool(annotations=READ)
async def watch_model_requests(
    ctx: Context,
    wait_seconds: Annotated[int, Field(ge=0, le=55, description="Wait up to this long for an unclaimed request")] = 45,
) -> dict[str, Any]:
    """Session model bridge, parallel serving: how many agent model calls are waiting, without their
    contents. A coordinator uses this to start one fresh model-responder helper per unclaimed request,
    so answers are independent and run in parallel. Does not claim anything."""
    await _ensure_gateway()
    try:
        r = await _client().get("/api/bridge/pending", params={"wait": wait_seconds}, timeout=wait_seconds + 15)
    except httpx.HTTPError as e:
        raise ToolError(f"gateway request failed: {e}") from None
    if r.status_code >= 400:
        raise ToolError(f"{r.text[:300]} (HTTP {r.status_code})")
    return r.json()


@server.tool(annotations=EXECUTE)
async def answer_model_request(
    ctx: Context,
    request_id: Annotated[str, Field(max_length=32)],
    content: Annotated[str, Field(max_length=200_000, description="Assistant text, as the model would write it")] = "",
    tool_calls: Annotated[list[dict[str, Any]], Field(max_length=32, description=(
        "Tool calls to make, each {name, arguments: {...}}; names must be among the request's tools"))] = [],  # noqa: B006
) -> dict[str, Any]:
    """Session model bridge: deliver your reply to one agent model request. The agent receives it as a
    normal OpenAI/Anthropic API response."""
    return await _call(ctx, "answer_model_request", "POST", f"/api/bridge/{request_id}/answer",
                       json={"content": content, "tool_calls": tool_calls})


@server.tool(annotations=READ)
async def inspect_usage(ctx: Context, days: Annotated[int, Field(ge=1, le=365)] = 7) -> dict[str, Any]:
    """Calls, tokens, latency, errors and cost of the gateway's own provider calls."""
    return await _call(ctx, "inspect_usage", "GET", "/api/usage", params={"days": days})


def main() -> None:
    from companion.telemetry import setup_logging

    setup_logging("WARNING")
    server.run("stdio")


if __name__ == "__main__":
    main()
