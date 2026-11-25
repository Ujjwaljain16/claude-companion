# HTTP API

Base URL `http://127.0.0.1:8765`. Every endpoint except `/api/health` needs the gateway token from
`~/.claude-companion/gateway.token`:

```
Authorization: Bearer <token>
```

The bridge endpoints under `/v1` also accept `x-api-key: <token>`. Optional `X-Companion-Actor: mcp |
dashboard | api` says who is calling; it's recorded in the audit log, and approvals from `mcp` are
refused unless allowed. Requests whose `Host` header isn't local get 403.

Errors are JSON `{"detail": "...", "code": "..."}`: 401 bad token, 403 `permission_denied`, 404
`not_found`, 422 `invalid_request` or validation, 502 `provider_error`.

Interactive schema: http://127.0.0.1:8765/docs (FastAPI).

## System

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | No auth. `{"status": "ok", "version"}` |
| GET | `/api/system` | Health, desktop detection, MCP clients, providers, execution counts |
| GET | `/api/settings` | Non-secret settings and permissions |
| GET | `/api/mcp/tools` | The MCP tool catalog with input schemas |
| POST | `/api/mcp/heartbeat` | Used by the MCP server to report client name/version and tool calls |
| GET | `/api/audit?limit=` | Audit log, newest first |

## Providers

| Method | Path | Notes |
|---|---|---|
| GET | `/api/providers?probe=true` | Providers; `probe` adds reachability and model count |
| GET | `/api/models?provider=` | Live model lists |

## Datasets, experiments, evaluations

| Method | Path | Body / query |
|---|---|---|
| GET | `/api/datasets` | |
| POST | `/api/datasets` | `{name, items, description?, note?}` |
| GET | `/api/datasets/{name or id}?version=` | Items and version list |
| GET | `/api/experiments?q=&tag=&limit=` | |
| POST | `/api/experiments` | `{name, description?, tags?}` |
| GET | `/api/scorers` | Scorer descriptions and judge rubrics |
| POST | `/api/evaluations` | `{dataset, version?, target: {provider, model?, max_tokens?}, scorers: [{name, params?}], judge?: {provider, model?, rubric?, pass_threshold?}, system_prompt?, experiment?}` |
| GET | `/api/evaluations?limit=` | Runs, newest first |
| GET | `/api/evaluations/{run_id}?items=&failures_only=` | Report |
| GET | `/api/evaluations/{run_id}/export?format=json\|csv\|html` | File body; saved path in the `X-Report-Path` header |
| GET | `/api/evaluations/{run_id}/failures?limit=` | Failure analysis |
| POST | `/api/evaluations/compare?format=json\|html` | `{baseline_run_id, candidate_run_ids, regression_threshold?}` |

## Agents

| Method | Path | Body |
|---|---|---|
| GET | `/api/agents` | Registry path and agents (or `error` if the TOML is invalid) |
| POST | `/api/agents/runs` | `{agent, dataset, version?, scorers, judge?, variants?: [{label, config}], parallel?, experiment?}` → one run per variant |

## Workflows, executions, tasks

| Method | Path | Body / query |
|---|---|---|
| GET | `/api/workflows` | Workflow catalog |
| POST | `/api/workflows/{name}` | `{task, provider?, model?, require_approval?, max_revisions?}` |
| GET | `/api/executions?status=&kind=&limit=` | Kinds: `evaluation`, `agent_evaluation`, `workflow`, `model_request` |
| GET | `/api/executions/{id}` | Includes `open_tasks` |
| POST | `/api/executions/{id}/cancel` | |
| GET | `/api/tasks?status=open&kind=&execution_id=` | Session tasks |
| POST | `/api/tasks/{task_id}/submit` | `{response: {...}}` |

Execution statuses: `queued`, `running`, `waiting` (on a session task or approval), `succeeded`,
`failed`, `cancelled`.

## Traces and usage

| Method | Path | Notes |
|---|---|---|
| GET | `/api/traces?execution_id=&limit=` | Spans (name, parent, duration, status, attributes) |
| GET | `/api/usage?days=` | Totals, by model, by day |

## Session model bridge

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI Chat Completions format, `stream` supported |
| POST | `/v1/messages` | Anthropic Messages format, `stream` supported |
| GET | `/v1/models` | `claude-session` |
| GET | `/api/bridge/next?wait=` | Next pending request (used by `wait_for_model_request`) |
| GET | `/api/bridge/pending` | Requests waiting right now |
| POST | `/api/bridge/{request_id}/answer` | `{content?, tool_calls?: [{name, arguments}]}` |

Optional request headers: `X-Agent-Name`, `X-Companion-Tag`.
