"""The same idea with the Anthropic Python SDK, for agents written against
the Messages API. Point base_url at the gateway and the Claude session in the
Code tab answers; point it at api.anthropic.com with a real key and the code
runs unchanged.

    uv run python examples/agents/anthropic_sdk_agent.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic

TOOLS = [{"name": "get_stock", "description": "Units in stock for a product SKU.",
          "input_schema": {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]}}]
STOCK = {"A-100": 12, "B-200": 0}


def run(question: str, client: anthropic.Anthropic, model: str) -> dict:
    messages: list = [{"role": "user", "content": question}]
    calls = []
    for step in range(5):
        msg = client.messages.create(model=model, max_tokens=1024, tools=TOOLS, messages=messages,
                                     system="You are an inventory assistant. Use get_stock; answer briefly.")
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})
        if msg.stop_reason != "tool_use":
            return {"answer": "".join(b.text for b in msg.content if b.type == "text"), "steps": step + 1, "tool_calls": calls}
        results = []
        for b in msg.content:
            if b.type == "tool_use":
                sku = str(b.input.get("sku", ""))
                out = {"sku": sku, "units": STOCK.get(sku)} if sku in STOCK else {"error": f"unknown sku {sku}"}
                calls.append({"name": b.name, "args": b.input, "ok": "error" not in out})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)})
        messages.append({"role": "user", "content": results})
    return {"answer": None, "steps": 5, "tool_calls": calls, "error": "step limit reached"}


def gateway_client() -> anthropic.Anthropic:
    home = Path(os.environ.get("COMPANION_HOME", Path.home() / ".claude-companion"))
    port = os.environ.get("COMPANION_PORT", "8765")
    return anthropic.Anthropic(base_url=f"http://127.0.0.1:{port}", api_key=(home / "gateway.token").read_text().strip(),
                               default_headers={"X-Agent-Name": "anthropic_sdk_agent"}, timeout=900, max_retries=0)


if __name__ == "__main__":
    print(json.dumps(run("Do we have A-100 and B-200 in stock?", gateway_client(), "claude-session"), indent=2))
