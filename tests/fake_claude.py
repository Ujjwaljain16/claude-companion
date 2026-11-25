"""Stand-in for `claude -p --output-format json` used by the headless tests.

Prints the same result shape the real CLI 2.1.330 prints, records how it was
called (args, environment, timing) in FAKE_CLAUDE_LOG, and decides its reply
from the prompt like a tiny model would."""

import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
prompt = sys.stdin.read()
log_dir = Path(os.environ["FAKE_CLAUDE_LOG"])
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
started = time.time()


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def out(reply=None, error=None):
    data = {"type": "result", "subtype": "success", "is_error": error is not None, "num_turns": 1, "duration_ms": 5,
            "result": error if error else json.dumps(reply), "session_id": "fake",
            "total_cost_usd": 0.0012,
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100, "output_tokens": 7}}
    if reply is not None and mode != "no_structured":
        data["structured_output"] = reply
    (log_dir / f"call-{os.getpid()}.json").write_text(json.dumps({
        "args": args, "prompt": prompt, "start": started, "end": time.time(), "cwd": os.getcwd(),
        "env_simple": os.environ.get("CLAUDE_CODE_SIMPLE"), "env_api_key": os.environ.get("ANTHROPIC_API_KEY"),
        "env_oauth": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "env_companion": sorted(k for k in os.environ if k.startswith("COMPANION_"))}))
    print(json.dumps(data))
    sys.exit(1 if error else 0)


time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
if mode == "not_logged_in":
    out(error="Not logged in · Please run /login")
if mode == "bad" or (mode == "bad_once" and not list(log_dir.glob("call-*.json"))):
    out({"content": "", "tool_calls": [{"name": "rm_rf", "arguments": {}}]})

if "road_distance_km" in prompt:
    results = prompt.count("[tool result")
    if results == 0:
        out({"content": "", "tool_calls": [{"name": "road_distance_km", "arguments": {"a": "Paris", "b": "Berlin"}}]})
    if results == 1:
        out({"content": "", "tool_calls": [{"name": "calculate", "arguments": {"expression": "878 * 0.621371"}}]})
    out({"content": "Paris to Berlin is 878 km, about 545.6 miles.", "tool_calls": []})
out({"content": "pong", "tool_calls": []})
