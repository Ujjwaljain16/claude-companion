# Claude Companion

A local AI engineering workstation that you drive from the **Code tab of Claude Desktop**, using the
session you're already signed in to. No extra login, no CLI install, and no Anthropic API key are
needed.

It does three things:

1. **Lets your own agents use Claude without an API key.** Point any OpenAI- or Anthropic-SDK agent at
   the gateway, tell Claude "serve my agent", and Claude answers your agent's model calls in the chat
   ([session model bridge](docs/session-bridge.md)).
2. **Evaluates your agents.** It runs your agent over a dataset and scores its answers and tool use.
   You can compare prompt or model versions, catch regressions, group failures by cause, and export
   HTML, CSV or JSON reports ([agent runner](docs/agent-runner.md)).
3. **Gives you evaluation and experiment infrastructure:** versioned datasets, 11 deterministic scorers,
   LLM or in-chat judges, experiments, LangGraph multi-agent workflows with human approval, traces,
   usage tracking, and a dashboard ([evaluations](docs/evaluations.md), [workflows](docs/workflows.md)).

```
Claude Desktop (Code tab) ──MCP over stdio──▶ companion-mcp ──HTTP + local token──▶ gateway  127.0.0.1:8765
                                                                                     ├─ datasets, runs, experiments, traces, audit
Your agent (any language) ──OpenAI / Anthropic API format──────────────────────────▶ ├─ session model bridge  (/v1/...)
                                                                                     ├─ agent runner (one process per item)
Dashboard  127.0.0.1:3765 ──server-side, local token───────────────────────────────▶ └─ optional providers: Anthropic API,
                                                                                        OpenAI, Gemini, OpenRouter, Ollama, custom
```

## Quick start

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/), and, for the dashboard, Node 20+ with pnpm.

```powershell
cd D:\Projects\claude-companion
uv sync
```

1. In Claude Desktop, open the **Code** tab and start a session in this folder.
2. Claude Code finds [.mcp.json](.mcp.json) and asks once whether to allow the `companion` server. Allow it.
3. Ask Claude: *"Run inspect_system_health."* The gateway starts automatically.

Then try:

- *"Serve my agent"*, and in a terminal: `uv run python examples/agents/trip_agent.py`
- *"Run the agent `rules` over a capitals dataset with exact_match and tool_success, variants v1 and
  v2-buggy (config buggy=true), then compare them"* (copy [examples/agents.toml](examples/agents.toml) to
  `%USERPROFILE%\.claude-companion\agents.toml` first)
- *"Create a dataset of 10 arithmetic questions and evaluate it with provider session and exact_match"*

Dashboard (optional):

```powershell
cd dashboard; pnpm install; pnpm build; pnpm start
```

Open http://127.0.0.1:3765.

Full walkthrough: [docs/getting-started.md](docs/getting-started.md).

## Important limits

- **It cannot use your Claude subscription as a programmatic API.** Claude Code doesn't support MCP
  sampling, and this project doesn't extract or forward credentials. Every bridge call is answered by
  Claude in your chat, so it's slow (a few seconds per call, one at a time) and needs a session that
  keeps serving. Use it for building and debugging agents and small runs; use a provider key or a
  local model for bulk evaluation. Details: [docs/desktop-integration.md](docs/desktop-integration.md).
- **Bridge token counts are estimates**, and cost shows as $0 because it runs on your plan.
- **Public benchmarks (SWE-bench, WebArena, GAIA, ...) are not included.** Add one as a dataset plus
  scorer when you need it.

## Documentation

| Guide | What it covers |
|---|---|
| [Getting started](docs/getting-started.md) | Install, connect Claude Desktop, first evaluation, dashboard, provider keys |
| [Session model bridge](docs/session-bridge.md) | Run any agent on Claude through the chat, with no API key |
| [Agent runner](docs/agent-runner.md) | Register an agent, the task/result contract, metrics, variants, reports |
| [Evaluations](docs/evaluations.md) | Datasets, scorers, judges, comparisons, experiments |
| [Workflows](docs/workflows.md) | The LangGraph `draft_review` workflow and human approval |
| [MCP tools](docs/mcp-tools.md) | All 23 tools Claude can call |
| [HTTP API](docs/http-api.md) | Gateway endpoints |
| [Configuration](docs/configuration.md) | Every setting, the data folder, Postgres, tracing |
| [Security](docs/security.md) | Threat model and protections |
| [Architecture](docs/architecture.md) | Components, data model, how executions flow |
| [Troubleshooting](docs/troubleshooting.md) | Problems seen so far and fixes |
| [Desktop integration](docs/desktop-integration.md) | What the Desktop integration supports and what it doesn't |

## Development

```powershell
uv run pytest -q
```

```powershell
cd dashboard; pnpm test; pnpm lint; pnpm build
```

The Python suite (54 tests) includes MCP contract tests that start the real stdio server as a
subprocess, bridge tests using the real OpenAI and Anthropic SDKs, and agent-runner tests that run
Python and TypeScript agent processes, including checks that timeouts and cancels kill them.

Layout: `src/companion/` (gateway, MCP server, bridge, runner), `sdk/` (agent helpers),
`examples/agents/` (example agents), `dashboard/` (Next.js), `tests/`, `docs/`.
