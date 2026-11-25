# Agent runner

Run your own agent over every item of a dataset, capture what it did, score it, and compare versions.

## 1. Register the agent

Agents live in `%USERPROFILE%\.claude-companion\agents.toml` (or the file named by
`COMPANION_AGENTS_FILE`). Only this file can register an agent: MCP tools and the API can list and run
agents but never add or change one, so no tool call can make the gateway execute an arbitrary
command. Start from [examples/agents.toml](../examples/agents.toml):

```toml
[agents.trip]
description = "Travel agent on the plain OpenAI SDK"
command = ["uv", "run", "--no-sync", "python", "examples/agents/trip_agent_runner.py"]
cwd = "D:/Projects/claude-companion"
timeout_s = 900
env = { MY_SETTING = "x" }
```

| Field | Required | Meaning |
|---|---|---|
| `command` | yes | Program and arguments, run once per dataset item. No shell is involved. |
| `cwd` | no | Working directory. Also used to record the git commit. |
| `timeout_s` | no | Per item; default 300, maximum 3600. On timeout the whole process tree is killed. |
| `env` | no | Extra environment variables for the agent. |
| `description` | no | Shown in `list_agents` and the dashboard. |

Names may contain letters, digits, `_`, `.` and `-`. The file is reread on every list and run, so edits
apply without restarting the gateway.

## 2. Speak the contract

For each item the runner starts your command with these environment variables:

| Variable | Contents |
|---|---|
| `COMPANION_TASK_FILE` | Path to the task JSON (below) |
| `COMPANION_RESULT_FILE` | Path where your agent writes the result JSON |
| `COMPANION_OPENAI_BASE_URL` | `http://127.0.0.1:<port>/v1`, the session bridge or providers |
| `COMPANION_ANTHROPIC_BASE_URL` | `http://127.0.0.1:<port>` |
| `COMPANION_GATEWAY_TOKEN` | API key for the two URLs above |
| `COMPANION_TAG` | `<run_id>:<item_id>`; send it as the `X-Companion-Tag` header so model calls are counted per item |

Every other `COMPANION_*` variable, including provider keys, is removed from the agent's environment.

Task file:

```json
{"item_id": "paris-berlin", "input": "How far is ...?", "metadata": {}, "context": null,
 "config": {"prompt": "v2"}, "run_id": "d97e2a969e8b41c4"}
```

`config` is the variant's config (see below). `input` is a string or a list of `{role, content}`
messages, as stored in the dataset.

Result file:

```json
{
  "output": "final answer as text",
  "error": null,
  "steps": 3,
  "tool_calls": [{"name": "road_distance_km", "args": {"a": "Paris", "b": "Berlin"}, "ok": true, "result": {"km": 878}},
                 {"name": "lookup", "args": {}, "ok": false, "error": "503"}],
  "events": [{"type": "step", "n": 1, "note": "plan", "t_ms": 12.5}],
  "usage": {"input_tokens": 0, "output_tokens": 0, "model_calls": 0, "cost_usd": 0.0},
  "retrieved_ids": ["doc-3", "doc-9"],
  "metadata": {}
}
```

Only `output` matters for a minimal agent. A non-null `error` marks the item as failed. If the process
exits without writing the file, the item fails with "agent exited with code N without writing a
result". Stdout and stderr are captured (the last 4 KB) and are free for your own logging.

`usage` is only for model calls that bypass the gateway; calls through the gateway are measured there.
`retrieved_ids` feeds the retrieval scorers.

### Helpers

Two dependency-free files do all of this for you. Copy one next to your agent or add its folder to
your path.

Python, [sdk/python/companion_agent.py](../sdk/python/companion_agent.py):

```python
import companion_agent as ca
from openai import OpenAI

def solve(task: dict, traj: ca.Trajectory) -> str:
    client = OpenAI(**ca.openai_settings("my-agent"))      # base_url, api_key, tag header, timeout
    traj.step("plan")
    traj.tool("search", {"q": task["input"]}, ok=True, result=["doc-3"])
    traj.retrieved_ids = ["doc-3"]
    return "final answer"

ca.run(solve)   # always writes a result, also when solve raises
```

`ca.anthropic_settings()` does the same for `anthropic.Anthropic(...)`. `traj.add_usage(...)` records
model calls that don't go through the gateway.

TypeScript (Node 22+), [sdk/typescript/companionAgent.ts](../sdk/typescript/companionAgent.ts):

```ts
import { run, openaiSettings } from "./companionAgent.ts";
await run(async (task, traj) => {
  traj.tool("calculate", { expression: "6*7" }, { result: 42 });
  return "42";
});
```

Run TypeScript agents with `node --experimental-strip-types --no-warnings your_agent.ts`. Agents in
any other language just read and write the two JSON files.

## 3. Run it

Ask Claude, for example:

> Run the agent `trip` over the `trip-questions` dataset with scorers contains and trajectory_match,
> variants `v1` (config prompt=short) and `v2` (config prompt=detailed), then compare them.

Or call the API directly: `POST /api/agents/runs` (see [http-api.md](http-api.md)).

| Option | Meaning |
|---|---|
| `scorers` | Any of the [deterministic scorers](evaluations.md#scorers) |
| `judge` | Optional LLM judge (`provider`, `model`, `rubric`), or `session` to have Claude grade in the chat |
| `variants` | Up to 12 `{label, config}` entries, one run each; `config` reaches the agent as `task["config"]` |
| `parallel` | Items running at once (1–8). Bridge calls are still answered one at a time. |
| `version` | Dataset version; latest if omitted |
| `experiment` | Experiment to file the runs under |

If the agent uses the session bridge, Claude has to serve its model calls while the run goes. Say
"serve my agent" once the run has started.

## 4. What gets measured

Per item: answer, status, tool calls (name, args, ok, error), steps, events, wall-clock latency,
exit code, stderr and stdout tails, model calls and tokens made through the gateway, and the work
folder (`~/.claude-companion/agent_runs/<run>/<item>/`).

Per run, in `summary.agent`:

| Metric | Definition |
|---|---|
| `completion_rate` | Items that finished without an error |
| `success_rate` | Items that passed **every** scorer (and the judge) |
| `partial_score` | Mean over items of the average score across scorers |
| `avg_steps`, `avg_tool_calls` | As reported by the agent |
| `tool_success_rate` | Tool calls with `ok: true` / all tool calls |
| `failure_recovery_rate` | Of items with at least one failed tool call, how many still passed all output scorers (scorers other than the trajectory/retrieval ones) |
| `model_calls` | Model calls made through the gateway |
| `timeouts`, `errors` | Counts |

The rest of the summary has per-scorer mean and pass rate, latency p50/p95/mean, tokens, cost, wall
time and throughput (items per minute). Items that crash or time out count as score 0 on every scorer.

## 5. Compare, analyze, report

- **Compare:** `compare_model_results` with a baseline run and candidate runs gives per-item
  wins/losses/ties per scorer, newly failing items, latency and cost deltas, and a `regression` flag.
  A scorer counts as regressed when its mean drops by more than `regression_threshold` (default 0.05).
- **Analyze failures:** `analyze_failures` groups failing items into categories: `timeout`,
  `crashed (no result)`, `agent error: <first line>`, `right answer, trajectory scorer failed: ...`,
  `wrong result after tool errors`, and `wrong result: <scorers>`. Each category comes with examples:
  input, expected, output, failing scorers with reasons, tool errors, tool sequence and stderr. Ask
  Claude to read it and suggest fixes.
- **Reports:** `export_report` writes to `~/.claude-companion/reports/`:
  - `run-<id>.html`: stat cards, a score chart, and per-item tool-call sequences, colored by success
  - `run-<id>.csv`: one row per item, with a score and pass column per scorer
  - `run-<id>.json`: the full report
  - `compare-<ids>.html`: a comparison table with regression flags

  The dashboard's Agents and run pages link to the same exports.

## 6. Reproducibility

Every agent run stores, in `target.reproducibility`: the command, cwd, git commit and dirty flag of
`cwd`, a hash of command + env + variant config, the dataset name and version, and the Python version.
Datasets are immutable per version, so re-running with the same commit, config and dataset version
repeats the experiment. Model answers can still differ between runs.

## Examples

| Agent | What it shows |
|---|---|
| `trip` ([trip_agent_runner.py](../examples/agents/trip_agent_runner.py)) | OpenAI-SDK tool-calling agent on the session bridge |
| `rules` ([rule_agent.py](../examples/agents/rule_agent.py)) | No model. It retries after a flaky tool, crashes on `crash`, hangs on `hang`, and has a `{"buggy": true}` variant that gives two wrong answers, for trying regressions offline |
| `calc-ts` ([calc_agent.ts](../examples/agents/calc_agent.ts)) | TypeScript agent using the TS helper |
