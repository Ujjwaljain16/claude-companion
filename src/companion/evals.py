"""Datasets, evaluation runs, LLM/session judging, reports and comparisons."""

from __future__ import annotations

import asyncio
import json
import re
import statistics
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from companion.core import Core, NotFound, PermissionDenied
from companion.models import Dataset, DatasetVersion, EvalResult, EvalRun, Experiment, SessionTask, utcnow
from companion.providers import SESSION_TARGET, ProviderError, complete_with_retry
from companion.scorers import SCORERS, Score, run_scorer
from companion.telemetry import tracer

CHUNK = 20
CONCURRENCY = 4


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=100_000)


class DatasetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=120)
    input: str | list[Message]
    expected: Any = None
    context: str | None = Field(default=None, max_length=100_000, description="Source text for hallucination judging")
    expected_tools: list[str] | None = None
    expected_trajectory: list[str] | None = None
    relevant_ids: list[str] | None = None
    pattern: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScorerSpec(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in SCORERS:
            raise ValueError(f"unknown scorer '{v}'; available: {', '.join(SCORERS)}")
        return v


class Target(BaseModel):
    provider: str = Field(description="Provider name, or 'session' for outputs written by the Claude session")
    model: str | None = None
    max_tokens: int = Field(default=1024, ge=1, le=64_000)


class JudgeSpec(BaseModel):
    provider: str = Field(description="Provider name, or 'session' to have the Claude session judge")
    model: str | None = None
    rubric: str = Field(default="correctness", max_length=4000)
    pass_threshold: float = Field(default=0.5, ge=0, le=1)


class RunRequest(BaseModel):
    dataset: str = Field(description="Dataset name or id")
    version: int | None = Field(default=None, description="Dataset version; latest if omitted")
    target: Target
    scorers: list[ScorerSpec] = Field(default_factory=list)
    judge: JudgeSpec | None = None
    system_prompt: str = Field(default="", max_length=20_000)
    experiment: str | None = Field(default=None, description="Experiment name or id to file this run under")


RUBRICS = {
    "correctness": "Score 1 if the output correctly and completely answers the input (using the expected answer as reference when given), 0 if it is wrong, and partial credit in between.",
    "hallucination": "Score 1 if every factual claim in the output is supported by the provided context, 0 if it contains claims that the context contradicts or does not support. Ignore style.",
    "helpfulness": "Score 1 if the output is directly useful, well-scoped and actionable for the input, 0 if it is unhelpful or evasive.",
}

JUDGE_SYSTEM = (
    "You are an impartial evaluator. Reply with JSON only, no prose: "
    '{"score": <number between 0 and 1>, "reason": "<one sentence>"}'
)


def _messages(inp: Any) -> list[dict[str, str]]:
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    return [{"role": m["role"], "content": m["content"]} for m in inp]


def _input_text(inp: Any) -> str:
    return inp if isinstance(inp, str) else "\n".join(f"{m['role']}: {m['content']}" for m in inp)


def judge_prompt(rubric: str, item: dict, output: str) -> str:
    parts = [f"Rubric: {RUBRICS.get(rubric, rubric)}", f"Input:\n{_input_text(item['input'])}"]
    if item.get("context"):
        parts.append(f"Context:\n{item['context']}")
    if item.get("expected") is not None:
        parts.append(f"Reference answer:\n{json.dumps(item['expected']) if not isinstance(item['expected'], str) else item['expected']}")
    parts.append(f"Output to grade:\n{output}")
    return "\n\n".join(parts)


def parse_judgment(text: str) -> tuple[float, str]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("judge reply had no JSON object")
    data = json.loads(m.group())
    score = float(data["score"])
    if not 0 <= score <= 1:
        raise ValueError("judge score out of range")
    return score, str(data.get("reason", ""))[:500]


# datasets ---------------------------------------------------------------

async def create_dataset(core: Core, name: str, items: list[dict], description: str, note: str, actor: str) -> dict:
    core.require("write", actor)
    if not items:
        raise ValueError("a dataset needs at least one item")
    if len(items) > core.settings.max_dataset_items:
        raise ValueError(f"too many items (max {core.settings.max_dataset_items})")
    parsed = [DatasetItem.model_validate(i).model_dump(mode="json", exclude_none=True) for i in items]
    seen: set[str] = set()
    for n, it in enumerate(parsed):
        it["id"] = it.get("id") or f"item-{n + 1}"
        if it["id"] in seen:
            raise ValueError(f"duplicate item id '{it['id']}'")
        seen.add(it["id"])
    async with core.sessions() as s:
        ds = await s.scalar(select(Dataset).where(Dataset.name == name))
        if ds is None:
            ds = Dataset(name=name, description=description)
            s.add(ds)
            await s.flush()
        elif description:
            ds.description = description
        latest = await s.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == ds.id)) or 0
        ver = DatasetVersion(dataset_id=ds.id, version=latest + 1, items=parsed, item_count=len(parsed), note=note)
        s.add(ver)
        await s.commit()
        result = {"dataset_id": ds.id, "name": ds.name, "version": ver.version, "item_count": ver.item_count}
    await core.audit(actor, "dataset.create_version", name, version=result["version"], items=len(parsed))
    return result


async def resolve_dataset(core: Core, ref: str, version: int | None) -> tuple[Dataset, DatasetVersion]:
    async with core.sessions() as s:
        ds = await s.scalar(select(Dataset).where((Dataset.name == ref) | (Dataset.id == ref)))
        if ds is None:
            raise NotFound(f"dataset '{ref}' not found")
        q = select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
        q = q.where(DatasetVersion.version == version) if version else q.order_by(DatasetVersion.version.desc())
        ver = await s.scalar(q.limit(1))
        if ver is None:
            raise NotFound(f"dataset '{ref}' has no version {version}")
        return ds, ver


async def list_datasets(core: Core) -> list[dict]:
    async with core.sessions() as s:
        rows = (await s.execute(
            select(Dataset, func.max(DatasetVersion.version), func.count(DatasetVersion.id))
            .join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id, isouter=True)
            .group_by(Dataset.id).order_by(Dataset.created_at.desc())
        )).all()
    return [{"id": d.id, "name": d.name, "description": d.description, "latest_version": v, "versions": c,
             "created_at": d.created_at.isoformat()} for d, v, c in rows]


async def get_dataset(core: Core, ref: str, version: int | None) -> dict:
    ds, ver = await resolve_dataset(core, ref, version)
    async with core.sessions() as s:
        versions = (await s.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id).order_by(DatasetVersion.version))).all()
    return {"id": ds.id, "name": ds.name, "description": ds.description, "version": ver.version, "items": ver.items,
            "versions": [{"version": v.version, "item_count": v.item_count, "note": v.note, "created_at": v.created_at.isoformat()} for v in versions]}


# experiments ------------------------------------------------------------

async def create_experiment(core: Core, name: str, description: str, tags: list[str], actor: str) -> dict:
    core.require("write", actor)
    async with core.sessions() as s:
        if await s.scalar(select(Experiment).where(Experiment.name == name)):
            raise ValueError(f"experiment '{name}' already exists")
        e = Experiment(name=name, description=description, tags=tags)
        s.add(e)
        await s.commit()
    await core.audit(actor, "experiment.create", name)
    return {"id": e.id, "name": e.name, "description": e.description, "tags": e.tags}


async def search_experiments(core: Core, query: str = "", tag: str | None = None, limit: int = 20) -> list[dict]:
    async with core.sessions() as s:
        q = select(Experiment).order_by(Experiment.created_at.desc())
        if query:
            like = f"%{query}%"
            q = q.where(Experiment.name.ilike(like) | Experiment.description.ilike(like))
        exps = [e for e in (await s.scalars(q)).all() if not tag or tag in (e.tags or [])][:limit]
        out = []
        for e in exps:
            runs = (await s.scalars(select(EvalRun).where(EvalRun.experiment_id == e.id).order_by(EvalRun.created_at.desc()))).all()
            out.append({"id": e.id, "name": e.name, "description": e.description, "tags": e.tags,
                        "created_at": e.created_at.isoformat(),
                        "runs": [{"run_id": r.id, "status": r.status, "target": r.target, "summary": r.summary,
                                  "created_at": r.created_at.isoformat()} for r in runs]})
    return out


# runs -------------------------------------------------------------------

async def start_run(core: Core, req: RunRequest, actor: str) -> dict:
    core.require("execute", actor)
    _, ver = await resolve_dataset(core, req.dataset, req.version)
    for spec in (req.target, req.judge):
        if spec is None or spec.provider == SESSION_TARGET:
            continue
        p = core.registry.get(spec.provider)
        if not p.configured:
            raise ValueError(f"provider '{spec.provider}' is not configured; use provider 'session' or add its key")
        if not spec.model:
            raise ValueError(f"a model is required for provider '{spec.provider}'")
        if p.paid:
            core.require("paid", actor)
    if not req.scorers and req.judge is None:
        raise ValueError("give at least one scorer or a judge")
    experiment_id = None
    if req.experiment:
        async with core.sessions() as s:
            exp = await s.scalar(select(Experiment).where((Experiment.name == req.experiment) | (Experiment.id == req.experiment)))
            if exp is None:
                raise NotFound(f"experiment '{req.experiment}' not found")
            experiment_id = exp.id

    ex = await core.create_execution("evaluation", req.model_dump(mode="json"), actor)
    async with core.sessions() as s:
        run = EvalRun(execution_id=ex.id, experiment_id=experiment_id, dataset_version_id=ver.id,
                      target=req.target.model_dump(), scorers=[sc.model_dump() for sc in req.scorers],
                      judge=req.judge.model_dump() if req.judge else None, system_prompt=req.system_prompt)
        s.add(run)
        await s.flush()
        s.add_all(EvalResult(run_id=run.id, item_id=it["id"], input=it["input"], expected=it.get("expected"))
                  for it in ver.items)
        await s.commit()
    await core.audit(actor, "evaluation.start", run.id, dataset=req.dataset, model_target=req.target.model_dump())
    core.spawn(ex.id, lambda: _drive(core, run.id))
    return {"execution_id": ex.id, "run_id": run.id, "item_count": ver.item_count, "status": "queued"}


async def _load(core: Core, run_id: str) -> tuple[EvalRun, list[EvalResult], dict[str, dict]]:
    async with core.sessions() as s:
        run = await s.get(EvalRun, run_id)
        if run is None:
            raise NotFound(f"run {run_id} not found")
        results = list((await s.scalars(select(EvalResult).where(EvalResult.run_id == run_id))).all())
        ver = await s.get(DatasetVersion, run.dataset_version_id)
    items = {it["id"]: it for it in (ver.items if ver else [])}
    return run, results, items


async def _drive(core: Core, run_id: str) -> None:
    """Advance a run as far as it can go without outside input."""
    run, results, items = await _load(core, run_id)
    await core.set_status(run.execution_id, status="running", message="starting")
    with tracer().start_as_current_span("evaluation", attributes={"companion.execution_id": run.execution_id, "companion.run_id": run_id}):
        pending = [r for r in results if r.status == "pending"]
        if run.target["provider"] == "agent":
            from companion import agents

            await agents.generate(core, run, pending, items)
        elif run.target["provider"] == SESSION_TARGET:
            if pending:
                await _open_chunks(core, run, "generate", [
                    {"item_id": r.item_id, "input": r.input} for r in pending
                ])
                return
        else:
            await _generate_with_provider(core, run, pending, items)
        await _after_outputs(core, run_id)


async def _open_chunks(core: Core, run: EvalRun, kind: str, entries: list[dict]) -> None:
    for i in range(0, len(entries), CHUNK):
        payload: dict[str, Any] = {"source": "evaluation", "run_id": run.id, "items": entries[i:i + CHUNK]}
        if kind == "generate":
            payload["instructions"] = ("Answer each item's input as the model under test would. "
                                       + (f"System prompt for the model: {run.system_prompt}. " if run.system_prompt else "")
                                       + "Submit {\"outputs\": [{\"item_id\", \"output\"}]}.")
        else:
            judge = run.judge or {}
            payload["rubric"] = RUBRICS.get(judge.get("rubric", "correctness"), judge.get("rubric"))
            payload["instructions"] = "Grade each output against the rubric. Submit {\"judgments\": [{\"item_id\", \"score\" (0-1), \"reason\"}]}."
        await core.open_task(run.execution_id, kind, payload)
    what = "outputs" if kind == "generate" else "judgments"
    await core.set_status(run.execution_id, status="waiting",
                          message=f"waiting for the Claude session to submit {len(entries)} {what} (see list_session_tasks)")


async def _generate_with_provider(core: Core, run: EvalRun, pending: list[EvalResult], items: dict[str, dict]) -> None:
    provider = core.registry.get(run.target["provider"])
    model, max_tokens = run.target["model"], run.target.get("max_tokens", 1024)
    sem = asyncio.Semaphore(CONCURRENCY)
    spent = 0.0
    done = 0
    budget = core.settings.max_run_cost_usd

    async def one(r: EvalResult) -> None:
        nonlocal spent, done
        async with sem:
            if provider.paid and spent >= budget:
                r.status, r.error = "skipped", "run budget reached"
                return
            with tracer().start_as_current_span("llm.generate", attributes={
                "companion.execution_id": run.execution_id, "gen_ai.system": provider.name,
                "gen_ai.request.model": model, "companion.item_id": r.item_id}) as span:
                try:
                    c = await complete_with_retry(provider, model, _messages(r.input), run.system_prompt, max_tokens)
                except ProviderError as e:
                    r.status, r.error = "error", str(e)
                    span.set_attribute("error", str(e))
                    await core.record_usage(provider=provider.name, model=model, source="eval", execution_id=run.execution_id, ok=False, error=str(e))
                    return
                cost = core.registry.cost(provider, model, c.input_tokens, c.output_tokens)
                span.set_attribute("gen_ai.usage.input_tokens", c.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", c.output_tokens)
            spent += cost or 0.0
            r.output, r.latency_ms, r.input_tokens, r.output_tokens, r.cost_usd = c.text, c.latency_ms, c.input_tokens, c.output_tokens, cost
            r.status = "generated"
            await core.record_usage(provider=provider.name, model=model, source="eval", execution_id=run.execution_id,
                                    input_tokens=c.input_tokens, output_tokens=c.output_tokens, cost_usd=cost, latency_ms=c.latency_ms)
            done += 1
            await core.set_status(run.execution_id, progress=round(done / max(len(pending), 1) * 0.8, 3),
                                  message=f"generated {done}/{len(pending)}")

    await asyncio.gather(*(one(r) for r in pending))
    async with core.sessions() as s:
        for r in pending:
            await s.merge(r)
        await s.commit()


def _score_result(r: EvalResult, scorers: list[dict], item: dict) -> None:
    scores = dict(r.scores or {})
    for spec in scorers:
        scores[spec["name"]] = run_scorer(spec["name"], r.output or "", item, spec.get("params"), r.trajectory).dict()
    r.scores = scores


async def _after_outputs(core: Core, run_id: str) -> None:
    run, results, items = await _load(core, run_id)
    async with core.sessions() as s:
        for r in results:
            if r.status == "generated":
                _score_result(r, run.scorers, items.get(r.item_id, {"input": r.input, "expected": r.expected}))
                r.status = "scored"
                await s.merge(r)
            elif r.status in ("error", "skipped") and not r.scores:
                # A crash or skipped item is a failed item; leaving it unscored would inflate the means.
                reason = f"no output ({r.status}): {(r.error or '')[:200]}"
                r.scores = {spec["name"]: Score(0.0, False, reason).dict() for spec in run.scorers}
                await s.merge(r)
        await s.commit()
    run, results, items = await _load(core, run_id)
    judge = run.judge
    to_judge = [r for r in results if r.status == "scored"]
    if judge and to_judge:
        if judge["provider"] == SESSION_TARGET:
            await _open_chunks(core, run, "judge", [
                {"item_id": r.item_id, "input": r.input, "expected": r.expected,
                 "context": items.get(r.item_id, {}).get("context"), "output": r.output} for r in to_judge
            ])
            return
        await _judge_with_provider(core, run, to_judge, items)
    await _finalize(core, run_id)


async def _judge_with_provider(core: Core, run: EvalRun, results: list[EvalResult], items: dict[str, dict]) -> None:
    judge = run.judge or {}
    provider = core.registry.get(judge["provider"])
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(r: EvalResult) -> None:
        async with sem:
            item = items.get(r.item_id, {"input": r.input, "expected": r.expected})
            with tracer().start_as_current_span("llm.judge", attributes={
                "companion.execution_id": run.execution_id, "gen_ai.system": provider.name,
                "gen_ai.request.model": judge["model"], "companion.item_id": r.item_id}):
                try:
                    c = await complete_with_retry(provider, judge["model"], [{"role": "user", "content": judge_prompt(judge["rubric"], item, r.output or "")}], JUDGE_SYSTEM, 300)
                    score, reason = parse_judgment(c.text)
                    cost = core.registry.cost(provider, judge["model"], c.input_tokens, c.output_tokens)
                    await core.record_usage(provider=provider.name, model=judge["model"], source="judge", execution_id=run.execution_id,
                                            input_tokens=c.input_tokens, output_tokens=c.output_tokens, cost_usd=cost, latency_ms=c.latency_ms)
                    r.scores = {**(r.scores or {}), "judge": Score(score, score >= judge.get("pass_threshold", 0.5), reason).dict()}
                except (ProviderError, ValueError, KeyError) as e:
                    r.scores = {**(r.scores or {}), "judge": Score(0.0, False, f"judge failed: {e}").dict()}
            r.status = "judged"

    await asyncio.gather(*(one(r) for r in results))
    async with core.sessions() as s:
        for r in results:
            await s.merge(r)
        await s.commit()


def summarize(results: list[EvalResult]) -> dict:
    from companion.agents import agent_metrics

    scorer_names = sorted({k for r in results for k in (r.scores or {})})
    per: dict[str, dict] = {}
    for name in scorer_names:
        vals = [r.scores[name] for r in results if name in (r.scores or {})]
        per[name] = {"mean": round(statistics.fmean(v["score"] for v in vals), 4),
                     "pass_rate": round(sum(v["passed"] for v in vals) / len(vals), 4), "n": len(vals)}
    lat = sorted(r.latency_ms for r in results if r.latency_ms is not None)
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return {
        "items": len(results),
        "errors": sum(r.status in ("error", "skipped") for r in results),
        "scores": per,
        "latency_ms": {"p50": lat[len(lat) // 2], "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))], "mean": round(statistics.fmean(lat), 1)} if lat else None,
        "tokens": {"input": sum(r.input_tokens or 0 for r in results), "output": sum(r.output_tokens or 0 for r in results)},
        "cost_usd": round(sum(costs), 6) if costs else (None if any(r.output_tokens for r in results) else 0.0),
        "agent": agent_metrics(results),
    }


async def _finalize(core: Core, run_id: str) -> None:
    run, results, _ = await _load(core, run_id)
    summary = summarize(results)
    elapsed = (utcnow() - (run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=utcnow().tzinfo))).total_seconds()
    summary["wall_time_s"] = round(elapsed, 1)
    summary["throughput_items_per_min"] = round(len(results) / elapsed * 60, 2) if elapsed > 0 else None
    async with core.sessions() as s:
        db_run = await s.get(EvalRun, run_id)
        assert db_run is not None
        db_run.status, db_run.summary, db_run.finished_at = "succeeded", summary, utcnow()
        await s.commit()
    await core.set_status(run.execution_id, status="succeeded", progress=1.0, message="done",
                          result={"run_id": run_id, "summary": summary})


async def submit_eval_task(core: Core, task: SessionTask, response: dict) -> dict:
    run_id = task.payload["run_id"]
    run, results, items = await _load(core, run_id)
    by_id = {r.item_id: r for r in results}
    expected_ids = {e["item_id"] for e in task.payload["items"]}
    if task.kind == "generate":
        entries = response.get("outputs")
        if not isinstance(entries, list):
            raise ValueError("response must be {\"outputs\": [{\"item_id\", \"output\"}]}")
        got = {}
        for e in entries:
            if not isinstance(e, dict) or e.get("item_id") not in expected_ids or not isinstance(e.get("output"), str):
                raise ValueError(f"bad entry {str(e)[:120]}; item_id must be one of this task's items and output a string")
            got[e["item_id"]] = e["output"]
        missing = expected_ids - got.keys()
        if missing:
            raise ValueError(f"missing outputs for {sorted(missing)[:10]}")
        for item_id, out in got.items():
            r = by_id[item_id]
            r.output, r.status = out, "generated"
    else:
        entries = response.get("judgments")
        if not isinstance(entries, list):
            raise ValueError("response must be {\"judgments\": [{\"item_id\", \"score\", \"reason\"}]}")
        threshold = (run.judge or {}).get("pass_threshold", 0.5)
        seen = set()
        for e in entries:
            if not isinstance(e, dict) or e.get("item_id") not in expected_ids:
                raise ValueError(f"bad entry {str(e)[:120]}")
            score = float(e.get("score", -1))
            if not 0 <= score <= 1:
                raise ValueError(f"score for {e['item_id']} must be between 0 and 1")
            r = by_id[e["item_id"]]
            r.scores = {**(r.scores or {}), "judge": Score(score, score >= threshold, str(e.get("reason", ""))[:500]).dict()}
            r.status = "judged"
            seen.add(e["item_id"])
        if expected_ids - seen:
            raise ValueError(f"missing judgments for {sorted(expected_ids - seen)[:10]}")
    async with core.sessions() as s:
        for item_id in expected_ids:
            await s.merge(by_id[item_id])
        t = await s.get(SessionTask, task.id)
        assert t is not None
        t.status, t.response, t.resolved_at = "resolved", response, utcnow()
        await s.commit()
    remaining = await core.list_tasks(status="open", execution_id=run.execution_id)
    if not remaining:
        await core.set_status(run.execution_id, status="running", message="scoring")
        if task.kind == "generate":
            await _after_outputs(core, run_id)
        else:
            await _finalize(core, run_id)
    ex = await core.get_execution(run.execution_id)
    return {"task_id": task.id, "accepted": len(expected_ids), "execution_status": ex.status, "message": ex.message}


async def report(core: Core, run_id: str, include_items: bool = True, failures_only: bool = False) -> dict:
    run, results, _ = await _load(core, run_id)
    ex = await core.get_execution(run.execution_id)
    out: dict[str, Any] = {
        "run_id": run.id, "execution_id": run.execution_id, "status": ex.status, "message": ex.message,
        "target": run.target, "scorers": run.scorers, "judge": run.judge,
        "summary": run.summary or summarize(results), "created_at": run.created_at.isoformat(),
    }
    if include_items:
        rows = [r for r in results if not failures_only or r.status in ("error", "skipped")
                or any(not v["passed"] for v in (r.scores or {}).values())]
        out["items"] = [{"item_id": r.item_id, "status": r.status, "output": (r.output or "")[:2000], "expected": r.expected,
                         "scores": r.scores, "latency_ms": r.latency_ms, "error": r.error,
                         "trajectory": r.trajectory, "agent_meta": r.agent_meta} for r in rows[:200]]
    return out


async def list_runs(core: Core, limit: int = 50) -> list[dict]:
    async with core.sessions() as s:
        runs = (await s.scalars(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit))).all()
    return [{"run_id": r.id, "execution_id": r.execution_id, "status": r.status, "target": r.target,
             "experiment_id": r.experiment_id, "summary": r.summary, "created_at": r.created_at.isoformat()} for r in runs]


async def compare(core: Core, baseline_run_id: str, candidate_run_ids: list[str], regression_threshold: float = 0.05) -> dict:
    """Pairwise per-item comparison of each candidate against the baseline.
    A scorer regresses when its mean drops by more than the threshold."""
    base_run, base_results, _ = await _load(core, baseline_run_id)
    base = {r.item_id: r for r in base_results}
    base_summary = summarize(base_results)
    comparisons = []
    for cid in candidate_run_ids:
        cand_run, cand_results, _ = await _load(core, cid)
        cand_summary = summarize(cand_results)
        per_scorer = {}
        for name in sorted(set(base_summary["scores"]) & set(cand_summary["scores"])):
            wins = losses = ties = 0
            regressed_items = []
            for r in cand_results:
                b = base.get(r.item_id)
                if not b or name not in (b.scores or {}) or name not in (r.scores or {}):
                    continue
                bs, cs = b.scores[name]["score"], r.scores[name]["score"]
                if cs > bs:
                    wins += 1
                elif cs < bs:
                    losses += 1
                else:
                    ties += 1
                if b.scores[name]["passed"] and not r.scores[name]["passed"]:
                    regressed_items.append(r.item_id)
            delta = round(cand_summary["scores"][name]["mean"] - base_summary["scores"][name]["mean"], 4)
            per_scorer[name] = {"baseline_mean": base_summary["scores"][name]["mean"], "candidate_mean": cand_summary["scores"][name]["mean"],
                                "delta": delta, "wins": wins, "losses": losses, "ties": ties,
                                "regressed": delta < -regression_threshold, "newly_failing_items": regressed_items[:50]}
        lat_b, lat_c = base_summary["latency_ms"], cand_summary["latency_ms"]
        comparisons.append({
            "candidate_run_id": cid, "target": cand_run.target, "scorers": per_scorer,
            "regression": any(v["regressed"] for v in per_scorer.values()),
            "latency_p50_delta_ms": round(lat_c["p50"] - lat_b["p50"], 1) if lat_b and lat_c else None,
            "cost_delta_usd": round(cand_summary["cost_usd"] - base_summary["cost_usd"], 6)
            if cand_summary["cost_usd"] is not None and base_summary["cost_usd"] is not None else None,
        })
    return {"baseline_run_id": baseline_run_id, "baseline_target": base_run.target, "regression_threshold": regression_threshold,
            "comparisons": comparisons}


def ensure_not_approval(task: SessionTask, core: Core, actor: str) -> None:
    if task.kind == "approval":
        try:
            core.require("approve", actor)
        except PermissionDenied as e:
            raise PermissionDenied(f"{e}. Human approvals are made in the dashboard (Agent Workflows page).") from None
