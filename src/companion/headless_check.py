"""`companion-headless-check`: one real round trip through the headless backend,
so you know `claude -p` is installed and signed in before an agent depends on it."""

from __future__ import annotations

import asyncio
import json
import sys
import time

from companion.config import get_settings
from companion.headless import HeadlessBackend, HeadlessError


async def _check() -> int:
    backend = HeadlessBackend(get_settings())
    print(json.dumps(backend.status(), indent=2))
    req = {"model": "claude-headless", "system": "Reply briefly.", "tool_choice": "required",
           "messages": [{"role": "user", "content": "Call the ping tool with message 'ok'."}],
           "tools": [{"name": "ping", "description": "Echo a message.",
                      "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}]}
    started = time.perf_counter()
    try:
        ans, usage = await backend.answer(req)
    except HeadlessError as e:
        print(f"FAILED: {e}")
        return 1
    print(f"OK in {time.perf_counter() - started:.1f}s: tool_calls={[c.model_dump() for c in ans.tool_calls]} "
          f"content={ans.content!r} usage={usage}")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_check()))


if __name__ == "__main__":
    main()
