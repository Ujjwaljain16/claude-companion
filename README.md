# Claude Companion

A local AI engineering workstation that you drive from the **Code tab of Claude Desktop**, using the
session you're already signed in to. No extra login, no CLI install, and no Anthropic API key are
needed.

It does three things:

1. **Lets your own agents use Claude without an API key.** Point any OpenAI- or Anthropic-SDK agent at
   the gateway. The model id picks who answers ([model backends](docs/model-backends.md)):
   - `claude-session`: Claude answers in your Code tab chat, one at a time or with parallel helper
     subagents. No setup.
   - `claude-headless`: a fresh `claude -p` process per call. Fast, parallel and unattended, after a
     one-time `claude setup-token`.
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

- `/serve-agent`, and in a terminal: `uv run python examples/agents/trip_agent.py`
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

## Which backend when

| You want to... | Use | Why |
|---|---|---|
| Build or debug an agent, step by step | `claude-session` + `/serve-agent` | No setup; you see every call in the chat |
| Run a small eval with independent answers | `claude-session` + `/serve-agent-parallel` | One fresh helper per call, several at once; no login |
| Run long, large or unattended evals on your subscription | `claude-headless` | No human in the loop, parallel, real token counts; needs `claude setup-token` once |
| Ship something other people use, or run CI | a provider API key | The subscription paths are for your personal use only |
| Run free bulk jobs offline | Ollama | Local GPU, no account |

Full comparison, setup steps and caveats (billing, the token login, emulated tool calling):
[docs/model-backends.md](docs/model-backends.md).

## Important limits

- **The session paths are a chat in the loop.** Claude Code doesn't support MCP sampling, and this
  project never extracts or forwards credentials, so `claude-session` calls are answered by Claude in
  your chat: a few seconds each, and a session has to keep serving. Their token counts are estimates.
- **The headless path uses Claude Code's documented non-interactive mode** on a token from
  `claude setup-token`. Anthropic bills `claude -p` on subscriptions separately from chat usage. It's
  for your own use; don't share your subscription through it.
  Details: [docs/model-backends.md](docs/model-backends.md), [docs/desktop-integration.md](docs/desktop-integration.md).
- **Public benchmarks (SWE-bench, WebArena, GAIA, ...) are not included.** Add one as a dataset plus
  scorer when you need it.

## Documentation

| Guide | What it covers |
|---|---|
| [Getting started](docs/getting-started.md) | Install, connect Claude Desktop, first evaluation, dashboard, provider keys |
| [Model backends](docs/model-backends.md) | Session vs parallel helpers vs headless vs API keys: what, when, why, how |
| [Session model bridge](docs/session-bridge.md) | The bridge endpoints, wire formats and what's supported |
| [Agent runner](docs/agent-runner.md) | Register an agent, the task/result contract, metrics, variants, reports |
| [Evaluations](docs/evaluations.md) | Datasets, scorers, judges, comparisons, experiments |
| [Workflows](docs/workflows.md) | The LangGraph `draft_review` workflow and human approval |
| [MCP tools](docs/mcp-tools.md) | All 24 tools Claude can call |
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

The Python suite (62 tests) includes MCP contract tests that start the real stdio server as a
subprocess, bridge tests using the real OpenAI and Anthropic SDKs, and agent-runner tests that run
Python and TypeScript agent processes, including checks that timeouts and cancels kill them.

Layout: `src/companion/` (gateway, MCP server, bridge, runner), `sdk/` (agent helpers),
`examples/agents/` (example agents), `dashboard/` (Next.js), `tests/`, `docs/`.
