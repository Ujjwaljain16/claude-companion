"""trip_agent wrapped for the agent runner: reads the task, runs the agent
against the gateway (model calls answered by the Claude session or whatever
model task['config']['model'] names), and reports answer + tool calls."""

import sys
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "sdk" / "python"), str(Path(__file__).parent)]

import companion_agent as ca  # noqa: E402
import trip_agent  # noqa: E402


def solve(task: dict, traj: ca.Trajectory) -> str:
    client = OpenAI(**ca.openai_settings("trip_agent"))
    result = trip_agent.run(str(task["input"]), client, task["config"].get("model", "claude-session"),
                            max_steps=int(task["config"].get("max_steps", 6)))
    for call in result["tool_calls"]:
        traj.tool(call["name"], call["args"], ok=call["ok"], result=call.get("result"))
    for _ in range(result["steps"]):
        traj.step()
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["answer"]


ca.run(solve)
