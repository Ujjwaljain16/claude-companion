"""Session model bridge.

Gives agents under development an OpenAI-compatible (/v1/chat/completions)
and Anthropic-compatible (/v1/messages) endpoint whose "model" is the Claude
session in the Desktop Code tab. Each request is parked as a session task;
Claude picks it up with the wait_for_model_request MCP tool and replies with
answer_model_request, in the open, inside the user's normal conversation.
Nothing here holds or forwards Claude credentials: the gateway never calls
Anthropic, it only relays what Claude writes in the chat.

It is slow (one chat turn per model call) and sequential, so it suits
building and debugging agents and small benchmark runs, not bulk throughput.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from datetime import timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from companion.core import Core, NotFound
from companion.models import SessionTask, utcnow
from companion.telemetry import tracer

BRIDGE_MODEL = "claude-session"
MAX_PAYLOAD_CHARS = 200_000


class ToolCallIn(BaseModel):
    name: str = Field(max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AnswerIn(BaseModel):
    content: str = Field(default="", max_length=200_000)
    tool_calls: list[ToolCallIn] = Field(default_factory=list, max_length=32)


# request normalisation ------------------------------------------------------

def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for p in content:
        if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text"):
            parts.append(p.get("text", ""))
        elif isinstance(p, dict) and p.get("type") in ("image_url", "image"):
            parts.append("[image omitted]")
    return "\n".join(parts)


def normalize_openai(body: dict) -> dict:
    system, messages = [], []
    for m in body.get("messages", []):
        role = m.get("role")
        if role in ("system", "developer"):
            system.append(_text(m.get("content")))
        elif role == "tool":
            messages.append({"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": _text(m.get("content"))})
        else:
            out = {"role": role, "content": _text(m.get("content"))}
            if m.get("tool_calls"):
                out["tool_calls"] = [
                    {"id": c.get("id"), "name": c["function"]["name"], "arguments": _loads(c["function"].get("arguments"))}
                    for c in m["tool_calls"] if c.get("type", "function") == "function"
                ]
            messages.append(out)
    tools = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
              "parameters": t["function"].get("parameters", {})}
             for t in body.get("tools") or [] if t.get("type", "function") == "function"]
    return {"format": "openai", "model": body.get("model"), "system": "\n\n".join(system), "messages": messages,
            "tools": tools, "tool_choice": body.get("tool_choice"),
            "max_tokens": body.get("max_completion_tokens") or body.get("max_tokens"),
            "response_format": body.get("response_format")}


def normalize_anthropic(body: dict) -> dict:
    system = body.get("system")
    system = _text(system) if isinstance(system, list) else (system or "")
    messages = []
    for m in body.get("messages", []):
        content = m.get("content")
        if isinstance(content, str):
            messages.append({"role": m["role"], "content": content})
            continue
        text, calls = [], []
        for block in content or []:
            kind = block.get("type")
            if kind == "text":
                text.append(block.get("text", ""))
            elif kind == "tool_use":
                calls.append({"id": block.get("id"), "name": block.get("name"), "arguments": block.get("input", {})})
            elif kind == "tool_result":
                messages.append({"role": "tool", "tool_call_id": block.get("tool_use_id"),
                                 "content": _text(block.get("content")), "is_error": block.get("is_error", False)})
        if text or calls:
            out: dict[str, Any] = {"role": m["role"], "content": "\n".join(text)}
            if calls:
                out["tool_calls"] = calls
            messages.append(out)
    tools = [{"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema", {})}
             for t in body.get("tools") or [] if "name" in t and "input_schema" in t]
    return {"format": "anthropic", "model": body.get("model"), "system": system, "messages": messages, "tools": tools,
            "tool_choice": body.get("tool_choice"), "max_tokens": body.get("max_tokens")}


def _loads(s: Any) -> Any:
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s or "{}")
    except json.JSONDecodeError:
        return {"_raw": s}


def _estimate_tokens(obj: Any) -> int:
    return max(1, len(json.dumps(obj, ensure_ascii=False)) // 4)


# response building -------------------------------------------------------------

def openai_response(req: dict, ans: AnswerIn, usage: dict) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": ans.content or None}
    if ans.tool_calls:
        msg["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:24]}", "type": "function",
                              "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in ans.tool_calls]
    return {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion", "created": int(time.time()),
            "model": req.get("model") or BRIDGE_MODEL,
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if ans.tool_calls else "stop"}],
            "usage": {"prompt_tokens": usage["input"], "completion_tokens": usage["output"],
                      "total_tokens": usage["input"] + usage["output"]}}


def anthropic_response(req: dict, ans: AnswerIn, usage: dict) -> dict:
    content: list[dict] = []
    if ans.content:
        content.append({"type": "text", "text": ans.content})
    for c in ans.tool_calls:
        content.append({"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:24]}", "name": c.name, "input": c.arguments})
    return {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant",
            "model": req.get("model") or BRIDGE_MODEL, "content": content,
            "stop_reason": "tool_use" if ans.tool_calls else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": usage["input"], "output_tokens": usage["output"]}}


def _sse(data: Any, event: str | None = None) -> str:
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {json.dumps(data)}\n\n"


def openai_stream_events(full: dict) -> list[str]:
    msg = full["choices"][0]["message"]
    base = {"id": full["id"], "object": "chat.completion.chunk", "created": full["created"], "model": full["model"]}
    out = [_sse({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": msg["content"] or ""}, "finish_reason": None}]})]
    for i, c in enumerate(msg.get("tool_calls") or []):
        out.append(_sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, **c}]}, "finish_reason": None}]}))
    out.append(_sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": full["choices"][0]["finish_reason"]}],
                     "usage": full["usage"]}))
    out.append("data: [DONE]\n\n")
    return out


def anthropic_stream_events(full: dict) -> list[str]:
    start = {**full, "content": [], "stop_reason": None, "usage": {"input_tokens": full["usage"]["input_tokens"], "output_tokens": 0}}
    out = [_sse({"type": "message_start", "message": start}, "message_start")]
    for i, block in enumerate(full["content"]):
        if block["type"] == "text":
            out.append(_sse({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}, "content_block_start"))
            out.append(_sse({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": block["text"]}}, "content_block_delta"))
        else:
            out.append(_sse({"type": "content_block_start", "index": i,
                             "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}, "content_block_start"))
            out.append(_sse({"type": "content_block_delta", "index": i,
                             "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}}, "content_block_delta"))
        out.append(_sse({"type": "content_block_stop", "index": i}, "content_block_stop"))
    out.append(_sse({"type": "message_delta", "delta": {"stop_reason": full["stop_reason"], "stop_sequence": None},
                     "usage": {"output_tokens": full["usage"]["output_tokens"]}}, "message_delta"))
    out.append(_sse({"type": "message_stop"}, "message_stop"))
    return out


# the bridge itself ---------------------------------------------------------------

class Bridge:
    def __init__(self, core: Core, timeout_s: float):
        self.core = core
        self.timeout_s = timeout_s
        self.waiters: dict[str, asyncio.Future[AnswerIn]] = {}
        self.tags: dict[str, str | None] = {}
        self.arrived = asyncio.Event()

    async def submit(self, normalized: dict, agent: str, tag: str | None = None) -> tuple[str, str, asyncio.Future[AnswerIn]]:
        size = len(json.dumps(normalized))
        if size > MAX_PAYLOAD_CHARS:
            raise HTTPException(413, f"request is {size} characters; the session bridge accepts up to {MAX_PAYLOAD_CHARS}")
        ex = await self.core.create_execution("model_request", {"agent": agent, "format": normalized["format"],
                                                                 "model": normalized.get("model"), "tag": tag}, "api")
        task = await self.core.open_task(ex.id, "llm", {"source": "bridge", "agent": agent, "tag": tag, **normalized})
        self.tags[task.id] = tag
        await self.core.set_status(ex.id, status="waiting", message="waiting for the Claude session to answer")
        fut: asyncio.Future[AnswerIn] = asyncio.get_running_loop().create_future()
        self.waiters[task.id] = fut
        self.arrived.set()
        return ex.id, task.id, fut

    async def wait_result(self, execution_id: str, task_id: str, fut: asyncio.Future[AnswerIn], normalized: dict,
                          disconnected=None) -> tuple[AnswerIn, dict]:
        started = time.perf_counter()
        with tracer().start_as_current_span("bridge.model_request", attributes={
                "companion.execution_id": execution_id, "gen_ai.system": BRIDGE_MODEL,
                "gen_ai.request.model": normalized.get("model") or BRIDGE_MODEL}) as span:
            try:
                deadline = time.monotonic() + self.timeout_s
                # Servers do not cancel a plain request when its client hangs up, so
                # check for that ourselves; otherwise Claude would answer a dead call.
                while True:
                    try:
                        ans = await asyncio.wait_for(asyncio.shield(fut), 1.0)
                        break
                    except asyncio.TimeoutError:
                        if time.monotonic() > deadline:
                            raise
                        if disconnected is not None and await disconnected():
                            raise asyncio.CancelledError from None
            except (asyncio.TimeoutError, asyncio.CancelledError):
                fut.cancel()
                await self._expire(task_id, execution_id, "no answer from the Claude session in time"
                                   if time.monotonic() > started + self.timeout_s else "caller disconnected")
                raise
            finally:
                self.waiters.pop(task_id, None)
            usage = {"input": _estimate_tokens(normalized["messages"]) + _estimate_tokens(normalized["system"]),
                     "output": _estimate_tokens(ans.model_dump())}
            span.set_attribute("gen_ai.usage.input_tokens", usage["input"])
            span.set_attribute("gen_ai.usage.output_tokens", usage["output"])
            span.set_attribute("companion.tool_calls", len(ans.tool_calls))
        latency = round((time.perf_counter() - started) * 1000, 1)
        await self.core.record_usage(provider=BRIDGE_MODEL, model=normalized.get("model") or BRIDGE_MODEL, source="bridge",
                                     execution_id=execution_id, input_tokens=usage["input"], output_tokens=usage["output"],
                                     cost_usd=0.0, latency_ms=latency, tag=self.tags.pop(task_id, None))
        await self.core.set_status(execution_id, status="succeeded", progress=1.0, message="answered",
                                   result={"tool_calls": [c.name for c in ans.tool_calls], "chars": len(ans.content),
                                           "tokens_estimated": True})
        return ans, usage

    async def _expire(self, task_id: str, execution_id: str, why: str) -> None:
        async with self.core.sessions() as s:
            await s.execute(update(SessionTask).where(SessionTask.id == task_id, SessionTask.status == "open")
                            .values(status="expired", resolved_at=utcnow()))
            await s.commit()
        await self.core.set_status(execution_id, status="failed", error=why)

    async def next_request(self, wait_s: float) -> SessionTask | None:
        deadline = time.monotonic() + wait_s
        while True:
            async with self.core.sessions() as s:
                tasks = (await s.scalars(select(SessionTask).where(SessionTask.kind == "llm", SessionTask.status == "open")
                                         .order_by(SessionTask.created_at))).all()
            for t in tasks:
                fut = self.waiters.get(t.id)
                if fut is not None and not fut.done():
                    return t
                created = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=timezone.utc)
                # A brand-new task may not have its waiter registered yet (submit is
                # mid-flight); only expire ones that are stale or whose caller gave up.
                if fut is not None or (utcnow() - created).total_seconds() > 10:
                    await self._expire(t.id, t.execution_id, "caller disconnected" if fut is not None
                                       else "gateway restarted before it was answered")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.arrived.clear()
            try:
                await asyncio.wait_for(self.arrived.wait(), remaining)
            except asyncio.TimeoutError:
                return None

    async def answer(self, task_id: str, ans: AnswerIn, actor: str) -> dict:
        async with self.core.sessions() as s:
            task = await s.get(SessionTask, task_id)
            if task is None or task.kind != "llm":
                raise NotFound(f"model request {task_id} not found")
            if task.status != "open":
                raise ValueError(f"model request {task_id} is {task.status}")
            known = {t["name"] for t in task.payload.get("tools", [])}
            unknown = [c.name for c in ans.tool_calls if c.name not in known]
            if unknown:
                raise ValueError(f"tool(s) {unknown} are not offered in this request; offered: {sorted(known)}")
            if not ans.content and not ans.tool_calls:
                raise ValueError("give content, tool_calls, or both")
            fut = self.waiters.get(task_id)
            if fut is None or fut.done():
                task.status, task.resolved_at = "expired", utcnow()
                await s.commit()
                raise ValueError("the agent stopped waiting for this request")
            task.status, task.response, task.resolved_by, task.resolved_at = "resolved", ans.model_dump(), actor, utcnow()
            await s.commit()
        fut.set_result(ans)
        return {"request_id": task_id, "delivered": True, "tool_calls": [c.name for c in ans.tool_calls]}


def view(task: SessionTask) -> dict:
    p = task.payload
    return {"request_id": task.id, "agent": p.get("agent"), "format": p.get("format"), "model": p.get("model"),
            "system": p.get("system"), "messages": p.get("messages"), "tools": p.get("tools"),
            "tool_choice": p.get("tool_choice"), "max_tokens": p.get("max_tokens"),
            "response_format": p.get("response_format"), "created_at": task.created_at.isoformat(),
            "instructions": "You are acting as the model for this agent. Reply exactly as the model should: "
                            "answer_model_request with content and/or tool_calls (only tools listed here). "
                            "Do not add commentary meant for the user."}


def make_router(token: str, core_of) -> APIRouter:
    router = APIRouter()

    def check(authorization: str, x_api_key: str) -> None:
        supplied = (x_api_key or authorization.removeprefix("Bearer ")).strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "use the gateway token (~/.claude-companion/gateway.token) as the API key")

    def bridge(request: Request) -> Bridge:
        return request.app.state.bridge

    async def handle(request: Request, body: dict, fmt: str, agent: str, tag: str | None):
        core = core_of(request)
        core.require("execute", "api")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise HTTPException(400, "messages must be a non-empty list")
        normalized = normalize_openai(body) if fmt == "openai" else normalize_anthropic(body)
        b = bridge(request)
        ex_id, task_id, fut = await b.submit(normalized, agent, tag)
        build = openai_response if fmt == "openai" else anthropic_response

        if not body.get("stream"):
            # The body is already read, so the next ASGI message is the disconnect.
            async def listen() -> None:
                while (await request.receive())["type"] != "http.disconnect":
                    pass

            listener = asyncio.create_task(listen())

            async def gone() -> bool:
                return listener.done()

            try:
                ans, usage = await b.wait_result(ex_id, task_id, fut, normalized, gone)
            except asyncio.CancelledError:
                raise HTTPException(499, "client disconnected") from None
            except asyncio.TimeoutError:
                raise HTTPException(504, "the Claude session did not answer in time; is it serving requests "
                                         "(wait_for_model_request)?") from None
            finally:
                listener.cancel()
            return JSONResponse(build(normalized, ans, usage))

        async def events():
            waiter = asyncio.create_task(b.wait_result(ex_id, task_id, fut, normalized))
            try:
                while not waiter.done():
                    done, _ = await asyncio.wait({waiter}, timeout=10)
                    if not done:
                        yield ": waiting for the Claude session\n\n"
                ans, usage = waiter.result()
            except asyncio.TimeoutError:
                yield _sse({"type": "error", "error": {"type": "timeout", "message": "no answer from the Claude session"}}, "error")
                return
            finally:
                if not waiter.done():
                    waiter.cancel()
            full = build(normalized, ans, usage)
            for chunk in (openai_stream_events(full) if fmt == "openai" else anthropic_stream_events(full)):
                yield chunk

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request, authorization: str = Header(default=""), x_api_key: str = Header(default=""),
                               x_agent_name: str = Header(default="agent"),
                       x_companion_tag: str = Header(default="")):
        check(authorization, x_api_key)
        return await handle(request, await request.json(), "openai", x_agent_name[:80], x_companion_tag[:120] or None)

    @router.post("/v1/messages")
    async def messages(request: Request, authorization: str = Header(default=""), x_api_key: str = Header(default=""),
                       x_agent_name: str = Header(default="agent"),
                       x_companion_tag: str = Header(default="")):
        check(authorization, x_api_key)
        return await handle(request, await request.json(), "anthropic", x_agent_name[:80], x_companion_tag[:120] or None)

    @router.get("/v1/models")
    async def models(authorization: str = Header(default=""), x_api_key: str = Header(default="")):
        check(authorization, x_api_key)
        return {"object": "list", "data": [{"id": BRIDGE_MODEL, "object": "model", "owned_by": "claude-desktop-session",
                                            "type": "model", "display_name": "Claude (your Code tab session)"}]}

    @router.get("/api/bridge/next")
    async def next_request(request: Request, wait: float = 50, authorization: str = Header(default="")):
        check(authorization, "")
        task = await bridge(request).next_request(min(max(wait, 0), 55))
        return {"request": view(task) if task else None}

    @router.get("/api/bridge/pending")
    async def pending(request: Request, authorization: str = Header(default="")):
        check(authorization, "")
        tasks = await core_of(request).list_tasks(kind="llm")
        live = [t for t in tasks if t.id in bridge(request).waiters]
        return {"pending": len(live), "requests": [{"request_id": t.id, "agent": t.payload.get("agent"),
                                                    "messages": len(t.payload.get("messages", [])),
                                                    "created_at": t.created_at.isoformat()} for t in live]}

    @router.post("/api/bridge/{task_id}/answer")
    async def answer(task_id: str, body: AnswerIn, request: Request, authorization: str = Header(default=""),
                     x_companion_actor: str = Header(default="api")):
        check(authorization, "")
        return await bridge(request).answer(task_id, body, x_companion_actor)

    return router
