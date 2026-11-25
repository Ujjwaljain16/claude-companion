# MCP tools

The `companion` MCP server exposes 24 tools to the Claude Code session. Each one validates its input
against a JSON Schema, returns structured JSON, and turns gateway errors into tool errors with the
reason. Read-only tools carry the `readOnlyHint` annotation.

Long jobs return an `execution_id` right away. Tools with `wait_seconds` (at most 55, under Claude
Code's tool timeout) poll for up to that long and report MCP progress while they wait. Cancelling the
tool call stops the wait, not the job; use `cancel_execution` for that.

## System

| Tool | Does | Key inputs |
|---|---|---|
| `inspect_system_health` | Gateway version and settings, database, Claude Desktop install detection, MCP clients seen, providers, execution counts, open session tasks, 24 h model-call errors | – |
| `inspect_providers` | Providers and whether each is configured; `probe` makes a live list-models call | `probe` |
| `list_available_models` | Live model lists per configured provider | `provider` |
| `inspect_usage` | Calls, tokens, latency, errors and cost by model and day | `days` (1–365) |

## Datasets and evaluations

| Tool | Does | Key inputs |
|---|---|---|
| `list_datasets` | Datasets with latest version | – |
| `create_evaluation_dataset` | Create a dataset or add a version | `name`, `items` (≤ 2000), `description`, `note` |
| `run_evaluation` | Evaluate a model target over a dataset | `dataset`, `provider` (`session` or a provider), `model`, `scorers`, `scorer_params`, `judge_provider`/`judge_model`/`judge_rubric`, `system_prompt`, `version`, `experiment`, `wait_seconds` |
| `retrieve_evaluation_report` | Summary and per-item results | `run_id`, `include_items`, `failures_only` |
| `compare_model_results` | Baseline vs candidates: per-item wins/losses, regressions, latency and cost deltas | `baseline_run_id`, `candidate_run_ids`, `regression_threshold` |
| `create_experiment` | Group runs | `name`, `description`, `tags` |
| `search_experiments` | Find experiments and their runs | `query`, `tag`, `limit` |

## Executions and session tasks

| Tool | Does | Key inputs |
|---|---|---|
| `get_execution_status` | Status, progress, result and open tasks of any execution | `execution_id`, `wait_seconds` |
| `cancel_execution` | Cancel a running or waiting execution (kills agent processes); audited | `execution_id` |
| `list_session_tasks` | Work waiting for the session: `generate`, `judge`, `approval` | `execution_id`, `kind` |
| `submit_session_task` | Answer one task (see [evaluations](evaluations.md#session-tasks), [workflows](workflows.md)) | `task_id`, `response` |

## Agent workflows

| Tool | Does | Key inputs |
|---|---|---|
| `execute_agent_workflow` | Run the LangGraph `draft_review` workflow | `task`, `provider`, `model`, `require_approval`, `max_revisions`, `wait_seconds` |
| `inspect_agent_trace` | Spans (steps, model calls, durations, tokens) for an execution | `execution_id` |

## Session model bridge

| Tool | Does | Key inputs |
|---|---|---|
| `wait_for_model_request` | Claim the next model call from an agent (model `claude-session`), or `null`. Each call claims a different request | `wait_seconds` (default 45) |
| `watch_model_requests` | Counts of unclaimed and claimed requests, without contents, for a coordinator starting one helper per request | `wait_seconds` |
| `answer_model_request` | Reply to that call as the agent's model | `request_id`, `content`, `tool_calls` (`[{name, arguments}]`) |

See [model-backends.md](model-backends.md) and [session-bridge.md](session-bridge.md). The `/serve-agent` and `/serve-agent-parallel` commands and the `model-responder` subagent in `.claude/` drive these tools for you.

## Agent runner

| Tool | Does | Key inputs |
|---|---|---|
| `list_agents` | Registered agents and the registry file path | – |
| `run_agent_evaluation` | Run a registered agent over a dataset, one run per variant | `agent`, `dataset`, `scorers`, `scorer_params`, `variants` (`[{label, config}]`), `judge_*`, `parallel`, `version`, `experiment` |
| `analyze_failures` | Failing items grouped by cause, with examples | `run_id`, `limit` |
| `export_report` | Write an HTML, CSV or JSON report, or an HTML comparison, and return its path | `run_id`, `format`, `compare_with` |

See [agent-runner.md](agent-runner.md).

## Things no tool can do

- Register or change an agent command (only `agents.toml` can).
- Run shell commands or code.
- Approve a human-approval task, unless you set `COMPANION_MCP_CAN_APPROVE=true`.
- Read provider keys.
