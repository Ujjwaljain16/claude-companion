# Architecture

## Components

| Component | Code | Role |
|---|---|---|
| MCP server | `src/companion/mcp_server.py` | Started by Claude Code over stdio, one per session. A thin, validated client of the gateway's HTTP API. Starts the gateway if needed and reports the MCP client's name and version on each call. |
| Gateway | `src/companion/gateway.py` | FastAPI app on 127.0.0.1:8765. Owns storage, jobs, providers, permissions and the audit log. Long-lived, so jobs outlive any single MCP session. |
| Core | `src/companion/core.py` | Permission checks, audit, usage records, execution lifecycle (asyncio tasks), session tasks |
| Evaluations | `evals.py`, `scorers.py` | Datasets, runs, scoring, judges, reports, comparisons |
| Agent runner | `agents.py`, `sdk/` | Registry, one process per item, trajectories, agent metrics, failure analysis |
| Model bridge | `bridge.py`, `headless.py` | OpenAI/Anthropic-compatible endpoints; `claude-session` requests are claimed and answered from the chat, `claude-headless` requests run `claude -p` |
| Workflows | `workflows.py` | LangGraph graphs with SQLite checkpoints and interrupts |
| Providers | `providers.py` | Anthropic (official SDK), OpenAI-compatible (OpenAI, OpenRouter, Ollama, custom) and Gemini over httpx; retries, pricing |
| Reports | `reports.py` | JSON, CSV, self-contained HTML |
| Telemetry | `telemetry.py` | OpenTelemetry tracer, spans stored in the database, JSON logs on stderr |
| Dashboard | `dashboard/` | Next.js 16 server components plus server actions; reads the token server-side |

## Why the gateway is separate from the MCP server

Claude Code starts a fresh MCP server per session and stops it when the session ends. Evaluations,
agent runs and workflows can take longer than that. They also have to be visible to the dashboard and
resumable. So the gateway owns all state, and the MCP server is stateless.

## Data model

```
Dataset 1─* DatasetVersion (immutable items)
Experiment 1─* EvalRun *─1 DatasetVersion
EvalRun 1─1 Execution            target: {provider|session|agent, model|agent, variant, config, reproducibility}
EvalRun 1─* EvalResult           output, scores, latency, tokens, cost, trajectory, agent_meta
Execution 1─* SessionTask        kind: generate | judge | approval | llm (bridge)
Execution 1─* TraceSpan          via the companion.execution_id attribute
UsageRecord                      every model call the gateway made or relayed; tag links bridge calls to agent items
AuditLog, McpClient
```

## Execution lifecycle

```
queued ─▶ running ─┬─▶ succeeded
                   ├─▶ failed
                   ├─▶ cancelled
                   └─▶ waiting ──(session task answered)──▶ running ...
```

- **Model evaluation:** generate (provider or session task) → score → judge (provider or session task)
  → finalize the summary.
- **Agent evaluation:** for each item, start the agent process → read the result file → count tagged
  bridge calls → score → judge → finalize. Items run with a concurrency limit.
- **Workflow:** run the graph until it ends or hits an interrupt. An interrupt becomes a session task
  (`generate` or `approval`), and submitting it resumes the graph from its checkpoint.
- **Session bridge call:** a `model_request` execution plus an `llm` session task, held open by the
  HTTP request. A server claims it (`open` → `claimed`), answers via `answer_model_request`, or the
  claim lapses and it's handed out again; it expires on timeout or disconnect.
- **Headless bridge call:** a `model_request` execution and a `claude -p` process, killed if the
  caller disconnects or the timeout passes.

On startup, executions left `queued` or `running` are marked failed. `waiting` ones stay, because
their state is persisted.

## Storage choices

SQLite with WAL mode by default, so there's nothing to install. Postgres works through
`COMPANION_DATABASE_URL`. There's no Redis: the job queue is in-process asyncio, which suits a
single-user gateway. Alembic migrations run on start.

## Inference vs orchestration

Orchestration (graphs, runners, scoring, routing) never assumes where inference happens. A model call
goes to one of three places: a configured provider using the gateway's own keys; a session task
answered by Claude in the chat; or, for your own agents, the session bridge, which is again Claude in
the chat. There is no path where the gateway calls Claude with Desktop's credentials.
