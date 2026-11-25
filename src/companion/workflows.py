"""Agent workflows built on LangGraph.

Orchestration (graph, routing, retries, checkpoints, approvals) lives here;
inference is a separate step that either calls a configured provider or, for
the "session" target, pauses the graph and hands the prompt to the Claude
session over MCP. Pauses use LangGraph interrupts on a persistent SQLite
checkpointer, so a waiting workflow survives a gateway restart.

LangSmith tracing turns on through LangGraph's own environment variables
(LANGSMITH_TRACING=true, LANGSMITH_API_KEY); spans also go to the gateway's
trace store.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import BaseModel, Field

from companion.core import Core
from companion.models import SessionTask, utcnow
from companion.providers import SESSION_TARGET, ProviderError, complete_with_retry
from companion.telemetry import tracer


class WorkflowRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20_000, description="What the agents should produce")
    provider: str = Field(default=SESSION_TARGET, description="Provider for the agents' model calls, or 'session'")
    model: str | None = None
    require_approval: bool = Field(default=True, description="Pause for a human approval before finishing")
    max_revisions: int = Field(default=2, ge=0, le=5)


class DraftState(TypedDict, total=False):
    task: str
    draft: str
    critique: str
    verdict: str
    revisions: int
    max_revisions: int
    require_approval: bool
    approved: bool | None
    approval_comment: str
    final: str | None
    log: list[str]


WORKFLOWS = {
    "draft_review": {
        "description": "Supervisor routes between a drafter and a critic until the critic approves "
                       "(or max_revisions), then optionally pauses for human approval.",
        "agents": ["supervisor", "drafter", "critic", "human_approval"],
    }
}


def _parse_verdict(text: str) -> tuple[str, str]:
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group())
            verdict = str(data.get("verdict", "")).lower()
            if verdict in ("approve", "revise"):
                return verdict, str(data.get("feedback", ""))
        except json.JSONDecodeError:
            pass
    return ("revise" if "revise" in text.lower() else "approve"), text.strip()[:2000]


def build_graph(core: Core, req: WorkflowRequest, execution_id: str, checkpointer: Any):
    async def llm(role: str, prompt: str, system: str) -> str:
        with tracer().start_as_current_span(f"agent.{role}", attributes={
            "companion.execution_id": execution_id, "gen_ai.system": req.provider, "companion.agent": role}) as span:
            if req.provider == SESSION_TARGET:
                answer = interrupt({"kind": "generate", "role": role, "system": system, "prompt": prompt,
                                    "instructions": "Write this agent's reply and submit {\"text\": \"...\"}."})
                text = answer.get("text", "") if isinstance(answer, dict) else str(answer)
                span.set_attribute("companion.source", "session")
                return text
            provider = core.registry.get(req.provider)
            try:
                c = await complete_with_retry(provider, req.model, [{"role": "user", "content": prompt}], system, 2048)
            except ProviderError as e:
                await core.record_usage(provider=provider.name, model=req.model or "", source="workflow",
                                        execution_id=execution_id, ok=False, error=str(e))
                raise
            cost = core.registry.cost(provider, req.model or "", c.input_tokens, c.output_tokens)
            span.set_attribute("gen_ai.usage.input_tokens", c.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", c.output_tokens)
            await core.record_usage(provider=provider.name, model=req.model or "", source="workflow", execution_id=execution_id,
                                    input_tokens=c.input_tokens, output_tokens=c.output_tokens, cost_usd=cost, latency_ms=c.latency_ms)
            return c.text

    async def supervisor(state: DraftState) -> dict:
        return {}

    def route(state: DraftState) -> Literal["drafter", "critic", "human_approval", "finalize"]:
        if not state.get("draft"):
            return "drafter"
        if not state.get("verdict"):
            return "critic"
        if state["verdict"] == "revise" and state.get("revisions", 0) < state.get("max_revisions", 2):
            return "drafter"
        return "human_approval" if state.get("require_approval") else "finalize"

    async def drafter(state: DraftState) -> dict:
        if state.get("critique"):
            prompt = f"Task:\n{state['task']}\n\nYour previous draft:\n{state['draft']}\n\nCritic feedback:\n{state['critique']}\n\nWrite an improved draft."
            revisions = state.get("revisions", 0) + 1
        else:
            prompt = f"Task:\n{state['task']}\n\nWrite the first draft."
            revisions = state.get("revisions", 0)
        text = await llm("drafter", prompt, "You are the drafting agent. Produce the deliverable only, no commentary.")
        return {"draft": text, "verdict": "", "revisions": revisions,
                "log": state.get("log", []) + [f"drafter wrote draft (revision {revisions})"]}

    async def critic(state: DraftState) -> dict:
        prompt = (f"Task:\n{state['task']}\n\nDraft:\n{state['draft']}\n\n"
                  'Reply with JSON only: {"verdict": "approve" or "revise", "feedback": "<specific changes>"}')
        text = await llm("critic", prompt, "You are a strict reviewing agent.")
        verdict, feedback = _parse_verdict(text)
        return {"verdict": verdict, "critique": feedback, "log": state.get("log", []) + [f"critic: {verdict}"]}

    async def human_approval(state: DraftState) -> dict:
        decision = interrupt({"kind": "approval", "draft": state["draft"], "task": state["task"],
                              "instructions": "Approve or reject this result: {\"approved\": true|false, \"comment\": \"...\"}"})
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
        comment = str(decision.get("comment", "")) if isinstance(decision, dict) else ""
        return {"approved": approved, "approval_comment": comment,
                "log": state.get("log", []) + [f"human {'approved' if approved else 'rejected'}"]}

    async def finalize(state: DraftState) -> dict:
        ok = state.get("approved") is not False
        return {"final": state["draft"] if ok else None, "log": state.get("log", []) + ["finished"]}

    retry = RetryPolicy(max_attempts=3, retry_on=lambda e: isinstance(e, ProviderError) and e.retryable)
    g = StateGraph(DraftState)
    g.add_node("supervisor", supervisor)
    g.add_node("drafter", drafter, retry_policy=retry)
    g.add_node("critic", critic, retry_policy=retry)
    g.add_node("human_approval", human_approval)
    g.add_node("finalize", finalize)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route)
    g.add_edge("drafter", "supervisor")
    g.add_edge("critic", "supervisor")
    g.add_edge("human_approval", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


async def start_workflow(core: Core, name: str, req: WorkflowRequest, actor: str) -> dict:
    core.require("execute", actor)
    if name not in WORKFLOWS:
        raise ValueError(f"unknown workflow '{name}'; available: {', '.join(WORKFLOWS)}")
    if req.provider != SESSION_TARGET:
        p = core.registry.get(req.provider)
        if not p.configured:
            raise ValueError(f"provider '{req.provider}' is not configured; use provider 'session' or add its key")
        if not req.model:
            raise ValueError(f"a model is required for provider '{req.provider}'")
        if p.paid:
            core.require("paid", actor)
    ex = await core.create_execution("workflow", {"workflow": name, **req.model_dump()}, actor)
    await core.audit(actor, "workflow.start", ex.id, workflow=name, provider=req.provider)
    initial: DraftState = {"task": req.task, "revisions": 0, "max_revisions": req.max_revisions,
                           "require_approval": req.require_approval, "log": []}
    core.spawn(ex.id, lambda: _advance(core, ex.id, req, initial))
    return {"execution_id": ex.id, "workflow": name, "status": "queued"}


async def _advance(core: Core, execution_id: str, req: WorkflowRequest, inp: Any) -> None:
    await core.set_status(execution_id, status="running", message="agents working")
    graph = build_graph(core, req, execution_id, core.checkpointer)
    config = {"configurable": {"thread_id": execution_id}}
    with tracer().start_as_current_span("workflow.step", attributes={"companion.execution_id": execution_id}):
        result = await graph.ainvoke(inp, config)
    pending = result.get("__interrupt__") or []
    if pending:
        value = dict(pending[0].value)
        kind = value.pop("kind")
        await core.open_task(execution_id, kind, {"source": "workflow", **value})
        who = "a human (dashboard)" if kind == "approval" else "the Claude session"
        what = f"{value['role']} step" if "role" in value else kind
        await core.set_status(execution_id, status="waiting", message=f"waiting for {who}: {what}")
        return
    approved = result.get("approved")
    await core.set_status(
        execution_id, status="succeeded", progress=1.0,
        message="rejected by reviewer" if approved is False else "done",
        result={"final": result.get("final"), "approved": approved, "revisions": result.get("revisions", 0),
                "critique": result.get("critique"), "log": result.get("log", [])},
    )


async def submit_workflow_task(core: Core, task: SessionTask, response: dict, actor: str) -> dict:
    if task.kind == "generate":
        if not isinstance(response.get("text"), str) or not response["text"].strip():
            raise ValueError("response must be {\"text\": \"<non-empty>\"}")
    elif task.kind == "approval":
        if not isinstance(response.get("approved"), bool):
            raise ValueError("response must be {\"approved\": true|false, \"comment\": \"...\"}")
    async with core.sessions() as s:
        t = await s.get(SessionTask, task.id)
        assert t is not None
        t.status, t.response, t.resolved_by, t.resolved_at = "resolved", response, actor, utcnow()
        await s.commit()
    ex = await core.get_execution(task.execution_id)
    params = {k: v for k, v in ex.params.items() if k != "workflow"}
    req = WorkflowRequest.model_validate(params)
    await core.set_status(ex.id, status="running", message="agents working")
    core.spawn(ex.id, lambda: _advance(core, ex.id, req, Command(resume=response)))
    await core.audit(actor, f"workflow.{task.kind}", ex.id, approved=response.get("approved"))
    return {"task_id": task.id, "execution_id": ex.id, "execution_status": "running"}
