"""Helper for agents run by the claude-companion agent runner.

Standalone and dependency-free: copy it next to your agent or add this folder
to sys.path. The runner starts your command once per dataset item and talks
to it through files, so printing to stdout is fine.

    import companion_agent as ca

    def solve(task, traj):
        client = OpenAI(**ca.openai_settings())          # model calls go via the gateway
        ...
        traj.tool("search", {"q": "x"}, ok=True)          # record what your agent did
        return "final answer"

    ca.run(solve)

Environment the runner provides:
    COMPANION_TASK_FILE      JSON: item_id, input, metadata, config, run_id
    COMPANION_RESULT_FILE    where this helper writes the result JSON
    COMPANION_OPENAI_BASE_URL / COMPANION_ANTHROPIC_BASE_URL / COMPANION_GATEWAY_TOKEN
    COMPANION_TAG            links your model calls to this run item
"""

from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any, Callable


def task() -> dict:
    path = os.environ.get("COMPANION_TASK_FILE")
    if not path:
        raise RuntimeError("COMPANION_TASK_FILE is not set; run this agent through the companion agent runner")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _headers(agent_name: str | None) -> dict[str, str]:
    h = {"X-Companion-Tag": os.environ.get("COMPANION_TAG", "")}
    if agent_name:
        h["X-Agent-Name"] = agent_name
    return h


def openai_settings(agent_name: str | None = None) -> dict:
    """Keyword arguments for openai.OpenAI(...) pointed at the gateway."""
    return {"base_url": os.environ["COMPANION_OPENAI_BASE_URL"], "api_key": os.environ["COMPANION_GATEWAY_TOKEN"],
            "default_headers": _headers(agent_name), "timeout": 900, "max_retries": 0}


def anthropic_settings(agent_name: str | None = None) -> dict:
    """Keyword arguments for anthropic.Anthropic(...) pointed at the gateway."""
    return {"base_url": os.environ["COMPANION_ANTHROPIC_BASE_URL"], "api_key": os.environ["COMPANION_GATEWAY_TOKEN"],
            "default_headers": _headers(agent_name), "timeout": 900, "max_retries": 0}


class Trajectory:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.steps = 0
        self.events: list[dict] = []
        self.retrieved_ids: list[str] | None = None
        self.usage: dict[str, Any] = {}
        self._t0 = time.perf_counter()

    def _ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)

    def step(self, note: str = "") -> None:
        self.steps += 1
        self.events.append({"type": "step", "n": self.steps, "note": note[:500], "t_ms": self._ms()})

    def tool(self, name: str, args: dict | None = None, ok: bool = True, error: str | None = None,
             result: Any = None, latency_ms: float | None = None) -> None:
        call = {"name": name, "args": args or {}, "ok": ok and not error}
        if error:
            call["error"] = str(error)[:1000]
        if result is not None:
            call["result"] = result if isinstance(result, (str, int, float, bool, dict, list)) else str(result)
        if latency_ms is not None:
            call["latency_ms"] = latency_ms
        self.tool_calls.append(call)
        self.events.append({"type": "tool_call", "name": name, "ok": call["ok"], "t_ms": self._ms()})

    def log(self, message: str) -> None:
        self.events.append({"type": "log", "message": message[:1000], "t_ms": self._ms()})

    def add_usage(self, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float | None = None) -> None:
        """For model calls that do not go through the gateway (it measures its own)."""
        u = self.usage
        u["input_tokens"] = u.get("input_tokens", 0) + input_tokens
        u["output_tokens"] = u.get("output_tokens", 0) + output_tokens
        u["model_calls"] = u.get("model_calls", 0) + 1
        if cost_usd is not None:
            u["cost_usd"] = round(u.get("cost_usd", 0.0) + cost_usd, 6)


def finish(output: Any, traj: Trajectory | None = None, error: str | None = None, metadata: dict | None = None) -> None:
    traj = traj or Trajectory()
    result = {"output": output if isinstance(output, str) or output is None else json.dumps(output),
              "error": error, "steps": traj.steps, "tool_calls": traj.tool_calls, "events": traj.events[-500:],
              "usage": traj.usage, "metadata": metadata or {}}
    if traj.retrieved_ids is not None:
        result["retrieved_ids"] = traj.retrieved_ids
    with open(os.environ["COMPANION_RESULT_FILE"], "w", encoding="utf-8") as f:
        json.dump(result, f)


def run(solve: Callable[[dict, Trajectory], Any]) -> None:
    """Call solve(task, trajectory) and always write a result, even if it raises."""
    traj = Trajectory()
    try:
        out = solve(task(), traj)
    except Exception as e:
        finish(None, traj, error=f"{type(e).__name__}: {e}", metadata={"traceback": traceback.format_exc()[-4000:]})
        raise SystemExit(1) from None
    finish(out, traj)
