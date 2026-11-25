"""A small tool-calling agent written against the plain OpenAI SDK.

Nothing in it knows about Claude Desktop: point base_url anywhere
OpenAI-compatible. Pointed at the companion gateway with model
"claude-session", its model calls are answered by the Claude session in the
Code tab (which must be serving: ask Claude to "serve my agent's model requests").

    uv run python examples/agents/trip_agent.py "How far is Paris from Berlin, and what is that in miles?"
"""

from __future__ import annotations

import ast
import json
import operator
import os
import sys
from pathlib import Path

from openai import OpenAI

DISTANCES_KM = {("berlin", "paris"): 878, ("london", "paris"): 344, ("madrid", "paris"): 1053, ("berlin", "rome"): 1184}

TOOLS = [
    {"type": "function", "function": {
        "name": "road_distance_km", "description": "Driving distance in km between two European cities.",
        "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]}}},
    {"type": "function", "function": {
        "name": "calculate", "description": "Evaluate an arithmetic expression, e.g. '878 * 0.621371'.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
]

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only arithmetic is allowed")


def run_tool(name: str, args: dict) -> str:
    if name == "road_distance_km":
        key = tuple(sorted((args["a"].strip().lower(), args["b"].strip().lower())))
        if key not in DISTANCES_KM:
            return json.dumps({"error": f"unknown route {key}"})
        return json.dumps({"km": DISTANCES_KM[key]})
    if name == "calculate":
        return json.dumps({"result": round(_eval(ast.parse(args["expression"], mode="eval").body), 3)})
    return json.dumps({"error": f"unknown tool {name}"})


def run(question: str, client: OpenAI, model: str, max_steps: int = 6) -> dict:
    messages = [{"role": "system", "content": "You are a travel assistant. Use the tools for facts and arithmetic; "
                                              "answer in one sentence."},
                {"role": "user", "content": question}]
    trajectory = []
    for step in range(max_steps):
        resp = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return {"answer": msg.content, "steps": step + 1, "tool_calls": trajectory}
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            trajectory.append({"name": call.function.name, "args": args, "ok": "error" not in result, "result": json.loads(result)})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    return {"answer": None, "steps": max_steps, "tool_calls": trajectory, "error": "step limit reached"}


def gateway_client() -> OpenAI:
    home = Path(os.environ.get("COMPANION_HOME", Path.home() / ".claude-companion"))
    port = os.environ.get("COMPANION_PORT", "8765")
    return OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key=(home / "gateway.token").read_text().strip(),
                  default_headers={"X-Agent-Name": "trip_agent"}, timeout=900)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "How far is Paris from Berlin by road, and what is that in miles?"
    print(json.dumps(run(q, gateway_client(), os.environ.get("AGENT_MODEL", "claude-session")), indent=2))
