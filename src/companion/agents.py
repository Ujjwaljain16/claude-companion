"""Agent runner: evaluate your own agent over a dataset.

Agents are registered only in a TOML file you edit yourself
(~/.claude-companion/agents.toml, or COMPANION_AGENTS_FILE). MCP clients can
list and run registered agents but can never add one, so no tool call can
make the gateway execute an arbitrary command.

    [agents.trip]
    command = ["uv", "run", "--no-sync", "python", "examples/agents/trip_agent_runner.py"]
    cwd = "D:/Projects/claude-companion"
    timeout_s = 600
    description = "Travel agent, OpenAI SDK"
    env = { AGENT_MODEL = "claude-session" }

Each dataset item runs the command once, as its own process, with the task
in COMPANION_TASK_FILE; the agent writes COMPANION_RESULT_FILE (see
sdk/python/companion_agent.py). Model calls the agent makes through the
gateway carry COMPANION_TAG, so the runner counts them per item.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from companion.core import Core, NotFound
from companion.evals import JudgeSpec, ScorerSpec, resolve_dataset
from companion.models import EvalResult, EvalRun, Experiment, UsageRecord
from companion.scorers import TRAJECTORY_SCORERS
from companion.telemetry import tracer

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
TAIL = 4000


class AgentSpec(BaseModel):
    name: str
    command: list[str] = Field(min_length=1)
    cwd: str | None = None
    timeout_s: float = Field(default=300, gt=0, le=3600)
    env: dict[str, str] = Field(default_factory=dict)
    description: str = ""


class Variant(BaseModel):
    label: str = Field(default="default", max_length=60, pattern=r"^[\w.\- ]+$")
    config: dict[str, Any] = Field(default_factory=dict, description="Passed to the agent as task['config']")


class AgentRunRequest(BaseModel):
    agent: str
    dataset: str
    version: int | None = None
    scorers: list[ScorerSpec] = Field(default_factory=list)
    judge: JudgeSpec | None = None
    variants: list[Variant] = Field(default_factory=lambda: [Variant()], min_length=1, max_length=12)
    parallel: int = Field(default=1, ge=1, le=8)
    experiment: str | None = None

    @field_validator("variants")
    @classmethod
    def unique_labels(cls, v: list[Variant]) -> list[Variant]:
        if len({x.label for x in v}) != len(v):
            raise ValueError("variant labels must be unique")
        return v


def registry_path(core: Core) -> Path:
    return Path(os.environ.get("COMPANION_AGENTS_FILE") or core.settings.home / "agents.toml")


def load_agents(core: Core) -> dict[str, AgentSpec]:
    path = registry_path(core)
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = {}
    for name, raw in (data.get("agents") or {}).items():
        if not NAME.match(name):
            raise ValueError(f"agent name '{name}' in {path} must match {NAME.pattern}")
        out[name] = AgentSpec(name=name, **raw)
    return out


def _git(cwd: str | None) -> dict:
    if not cwd or not shutil.which("git"):
        return {}
    try:
        head = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if head.returncode != 0:
        return {}
    return {"commit": head.stdout.strip(), "dirty": bool(dirty.stdout.strip())}


def _config_hash(spec: AgentSpec, config: dict) -> str:
    blob = json.dumps({"command": spec.command, "env": spec.env, "config": config}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


async def start_agent_runs(core: Core, req: AgentRunRequest, actor: str) -> dict:
    from companion import evals

    core.require("execute", actor)
    agents = load_agents(core)
    if req.agent not in agents:
        raise NotFound(f"agent '{req.agent}' is not registered in {registry_path(core)}; registered: {sorted(agents)}")
    spec = agents[req.agent]
    if not req.scorers and req.judge is None:
        raise ValueError("give at least one scorer or a judge")
    if req.judge and req.judge.provider != "session":
        p = core.registry.get(req.judge.provider)
        if not p.configured or not req.judge.model:
            raise ValueError(f"judge provider '{req.judge.provider}' needs a key and a model")
        if p.paid:
            core.require("paid", actor)
    _, ver = await resolve_dataset(core, req.dataset, req.version)
    experiment_id = None
    if req.experiment:
        async with core.sessions() as s:
            exp = await s.scalar(select(Experiment).where((Experiment.name == req.experiment) | (Experiment.id == req.experiment)))
            if exp is None:
                raise NotFound(f"experiment '{req.experiment}' not found")
            experiment_id = exp.id
    git = await asyncio.to_thread(_git, spec.cwd)
    runs = []
    for variant in req.variants:
        target = {"provider": "agent", "agent": spec.name, "variant": variant.label, "config": variant.config,
                  "parallel": req.parallel,
                  "reproducibility": {"command": spec.command, "cwd": spec.cwd, "git": git,
                                      "config_hash": _config_hash(spec, variant.config),
                                      "dataset": req.dataset, "dataset_version": ver.version,
                                      "python": sys.version.split()[0]}}
        ex = await core.create_execution("agent_evaluation", {**req.model_dump(mode="json"), "variant": variant.label}, actor)
        async with core.sessions() as s:
            run = EvalRun(execution_id=ex.id, experiment_id=experiment_id, dataset_version_id=ver.id, target=target,
                          scorers=[sc.model_dump() for sc in req.scorers], judge=req.judge.model_dump() if req.judge else None)
            s.add(run)
            await s.flush()
            s.add_all(EvalResult(run_id=run.id, item_id=it["id"], input=it["input"], expected=it.get("expected")) for it in ver.items)
            await s.commit()
        await core.audit(actor, "agent_evaluation.start", run.id, agent=spec.name, variant=variant.label, dataset=req.dataset)
        core.spawn(ex.id, lambda run_id=run.id: evals._drive(core, run_id))
        runs.append({"run_id": run.id, "execution_id": ex.id, "variant": variant.label})
    return {"agent": spec.name, "dataset": req.dataset, "dataset_version": ver.version, "item_count": ver.item_count, "runs": runs}


def _child_env(core: Core, spec: AgentSpec, tag: str, task_file: Path, result_file: Path) -> dict[str, str]:
    # The gateway's own provider keys stay out of the agent's environment.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COMPANION_")}
    env.update(spec.env)
    base = f"http://127.0.0.1:{core.settings.port}"
    env.update({
        "COMPANION_TASK_FILE": str(task_file), "COMPANION_RESULT_FILE": str(result_file),
        "COMPANION_OPENAI_BASE_URL": f"{base}/v1", "COMPANION_ANTHROPIC_BASE_URL": base,
        "COMPANION_GATEWAY_TOKEN": core.settings.gateway_token(), "COMPANION_TAG": tag,
        "PYTHONIOENCODING": "utf-8",
    })
    return env


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        # proc.kill() would leave children (e.g. python under `uv run`) running.
        killer = await asyncio.create_subprocess_exec("taskkill", "/F", "/T", "/PID", str(proc.pid),
                                                      stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await killer.wait()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), 10)
    except asyncio.TimeoutError:
        pass


def _tail(b: bytes) -> str:
    return b[-TAIL:].decode("utf-8", errors="replace")


async def run_item(core: Core, spec: AgentSpec, run: EvalRun, r: EvalResult, item: dict) -> None:
    workdir = core.settings.home / "agent_runs" / run.id / re.sub(r"[^\w.-]", "_", r.item_id)
    workdir.mkdir(parents=True, exist_ok=True)
    task_file, result_file = workdir / "task.json", workdir / "result.json"
    result_file.unlink(missing_ok=True)
    task_file.write_text(json.dumps({"item_id": r.item_id, "input": item.get("input"), "metadata": item.get("metadata", {}),
                                     "context": item.get("context"), "config": run.target.get("config", {}),
                                     "run_id": run.id}), encoding="utf-8")
    tag = f"{run.id}:{r.item_id}"[:120]
    meta: dict[str, Any] = {"workdir": str(workdir)}
    started = time.perf_counter()
    with tracer().start_as_current_span("agent.item", attributes={
            "companion.execution_id": run.execution_id, "companion.agent": spec.name, "companion.item_id": r.item_id}) as span:
        try:
            proc = await asyncio.create_subprocess_exec(
                *spec.command, cwd=spec.cwd, env=_child_env(core, spec, tag, task_file, result_file),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=sys.platform != "win32")
        except OSError as e:
            r.status, r.error = "error", f"could not start agent: {e}"
            span.set_attribute("error", r.error)
            r.agent_meta = meta
            return
        try:
            out, err = await asyncio.wait_for(proc.communicate(), spec.timeout_s)
            meta.update(exit_code=proc.returncode, stdout_tail=_tail(out), stderr_tail=_tail(err))
        except asyncio.TimeoutError:
            await _kill_tree(proc)
            meta.update(exit_code=None, timed_out=True)
        except asyncio.CancelledError:
            await _kill_tree(proc)
            raise
        r.latency_ms = round((time.perf_counter() - started) * 1000, 1)

        result: dict | None = None
        if result_file.exists():
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except (ValueError, OSError) as e:
                meta["result_error"] = f"unreadable result file: {e}"
        async with core.sessions() as s:
            calls, tin, tout = (await s.execute(
                select(func.count(), func.sum(UsageRecord.input_tokens), func.sum(UsageRecord.output_tokens))
                .where(UsageRecord.tag == tag))).one()
        reported = (result or {}).get("usage") or {}
        meta["model_calls"] = calls or int(reported.get("model_calls", 0))
        meta["usage_source"] = "gateway" if calls else ("agent-reported" if reported else "none")
        r.input_tokens = int(tin or reported.get("input_tokens", 0) or 0)
        r.output_tokens = int(tout or reported.get("output_tokens", 0) or 0)
        r.cost_usd = 0.0 if calls else reported.get("cost_usd")

        if meta.get("timed_out"):
            r.status, r.error = "error", f"timed out after {spec.timeout_s:.0f}s"
        elif result is None:
            r.status = "error"
            r.error = meta.get("result_error") or f"agent exited with code {meta.get('exit_code')} without writing a result"
        else:
            output = result.get("output")
            r.output = output if isinstance(output, str) else (json.dumps(output) if output is not None else "")
            calls_list = [c for c in result.get("tool_calls") or [] if isinstance(c, dict) and "name" in c]
            r.trajectory = {"tool_calls": calls_list[:500], "steps": int(result.get("steps") or 0),
                            "events": (result.get("events") or [])[-500:]}
            if result.get("retrieved_ids") is not None:
                r.trajectory["retrieved_ids"] = result["retrieved_ids"]
            meta["agent_metadata"] = result.get("metadata") or {}
            if result.get("error"):
                r.status, r.error = "error", str(result["error"])[:2000]
            else:
                r.status = "generated"
        span.set_attribute("companion.status", r.status)
        span.set_attribute("companion.model_calls", meta["model_calls"])
        span.set_attribute("companion.tool_calls", len((r.trajectory or {}).get("tool_calls", [])))
    r.agent_meta = meta


async def generate(core: Core, run: EvalRun, pending: list[EvalResult], items: dict[str, dict]) -> None:
    agents = load_agents(core)
    name = run.target["agent"]
    if name not in agents:
        raise NotFound(f"agent '{name}' is no longer registered")
    spec = agents[name]
    sem = asyncio.Semaphore(int(run.target.get("parallel", 1)))
    done = 0

    async def one(r: EvalResult) -> None:
        nonlocal done
        async with sem:
            await run_item(core, spec, run, r, items.get(r.item_id, {"input": r.input}))
            async with core.sessions() as s:
                await s.merge(r)
                await s.commit()
            done += 1
            await core.set_status(run.execution_id, progress=round(done / max(len(pending), 1) * 0.9, 3),
                                  message=f"ran {done}/{len(pending)} items")

    await asyncio.gather(*(one(r) for r in pending))


def agent_metrics(results: list[EvalResult]) -> dict | None:
    rows = [r for r in results if r.agent_meta is not None]
    if not rows:
        return None
    n = len(rows)

    def passed(r: EvalResult) -> bool:
        return r.status not in ("error", "skipped") and bool(r.scores) and all(v["passed"] for v in r.scores.values())

    def answer_ok(r: EvalResult) -> bool:
        out = {k: v for k, v in (r.scores or {}).items() if k not in TRAJECTORY_SCORERS}
        return r.status not in ("error", "skipped") and bool(out) and all(v["passed"] for v in out.values())

    def partial(r: EvalResult) -> float:
        return sum(v["score"] for v in r.scores.values()) / len(r.scores) if r.scores else 0.0

    tool_calls = [c for r in rows for c in (r.trajectory or {}).get("tool_calls", [])]
    had_tool_error = [r for r in rows if any(not c.get("ok", True) for c in (r.trajectory or {}).get("tool_calls", []))]
    steps = [(r.trajectory or {}).get("steps", 0) for r in rows]
    return {
        "completion_rate": round(sum(r.status != "error" for r in rows) / n, 4),
        "success_rate": round(sum(passed(r) for r in rows) / n, 4),
        "partial_score": round(sum(partial(r) for r in rows) / n, 4),
        "avg_steps": round(sum(steps) / n, 2),
        "avg_tool_calls": round(len(tool_calls) / n, 2),
        "tool_success_rate": round(sum(c.get("ok", True) for c in tool_calls) / len(tool_calls), 4) if tool_calls else None,
        # Of the items where a tool call failed, how many still ended with a correct answer.
        "failure_recovery_rate": round(sum(answer_ok(r) for r in had_tool_error) / len(had_tool_error), 4) if had_tool_error else None,
        "model_calls": sum(int((r.agent_meta or {}).get("model_calls", 0)) for r in rows),
        "timeouts": sum(bool((r.agent_meta or {}).get("timed_out")) for r in rows),
        "errors": sum(r.status == "error" for r in rows),
    }


def analyze_failures(results: list[EvalResult], items: dict[str, dict], limit: int = 15) -> dict:
    """Group failing items so Claude (or you) can see patterns; it does not
    call a model, the recommendations are left to whoever reads this."""
    groups: dict[str, list[str]] = {}
    examples = []
    for r in results:
        failing = [k for k, v in (r.scores or {}).items() if not v["passed"]]
        meta = r.agent_meta or {}
        tool_errors = [c for c in (r.trajectory or {}).get("tool_calls", []) if not c.get("ok", True)]
        if r.status != "error" and not failing:
            continue
        if meta.get("timed_out"):
            kind = "timeout"
        elif r.status == "error" and "without writing a result" in (r.error or ""):
            kind = "crashed (no result)"
        elif r.status == "error":
            kind = f"agent error: {(r.error or '').splitlines()[0][:80]}"
        elif not set(failing) - TRAJECTORY_SCORERS:
            kind = "right answer, trajectory scorer failed: " + ", ".join(sorted(failing))
        elif tool_errors:
            kind = "wrong result after tool errors"
        else:
            kind = "wrong result: " + ", ".join(sorted(failing))
        groups.setdefault(kind, []).append(r.item_id)
        if len(examples) < limit:
            examples.append({
                "item_id": r.item_id, "category": kind, "input": items.get(r.item_id, {}).get("input", r.input),
                "expected": r.expected, "output": (r.output or "")[:1500], "error": r.error,
                "failing_scorers": {k: r.scores[k]["reason"] for k in failing},
                "tool_errors": [{"name": c["name"], "error": c.get("error")} for c in tool_errors][:10],
                "tool_sequence": [c["name"] for c in (r.trajectory or {}).get("tool_calls", [])][:40],
                "steps": (r.trajectory or {}).get("steps"), "stderr_tail": (meta.get("stderr_tail") or "")[-1500:],
            })
    total = len(results)
    failed = sum(len(v) for v in groups.values())
    return {"items": total, "failed": failed, "failure_rate": round(failed / total, 4) if total else 0.0,
            "categories": [{"category": k, "count": len(v), "item_ids": v[:30]}
                           for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))],
            "examples": examples}
