# Configuration

Settings come from environment variables or a `.env` file. The gateway reads
`%USERPROFILE%\.claude-companion\.env` (or `$COMPANION_HOME/.env`), then `.env` in its working
directory. Restart the gateway after changing them.

## Gateway

| Variable | Default | Meaning |
|---|---|---|
| `COMPANION_HOME` | `~/.claude-companion` | Data folder (see below) |
| `COMPANION_HOST` | `127.0.0.1` | Bind address; anything non-local is refused unless the next setting is true |
| `COMPANION_ALLOW_NON_LOCAL_BIND` | `false` | Allow non-local binds and Host headers. Don't, unless you know why. |
| `COMPANION_PORT` | `8765` | Gateway port; the MCP server and dashboard use the same variable |
| `COMPANION_DATABASE_URL` | SQLite in the data folder | e.g. `postgresql+asyncpg://user:pass@127.0.0.1/companion` (needs `uv sync --extra postgres`) |
| `COMPANION_MAX_DATASET_ITEMS` | `2000` | Items per dataset version |
| `COMPANION_BRIDGE_TIMEOUT_S` | `900` | How long a bridge call waits for Claude to answer |
| `COMPANION_AGENTS_FILE` | `<home>/agents.toml` | Agent registry |
| `COMPANION_PRICING_FILE` | – | JSON `{"model-id": [input_usd_per_mtok, output_usd_per_mtok]}` added to the built-in prices |

## Permissions

| Variable | Default | Controls |
|---|---|---|
| `COMPANION_ALLOW_WRITE` | `true` | Creating datasets and experiments |
| `COMPANION_ALLOW_EXECUTE` | `true` | Starting, cancelling and resuming evaluations, agent runs, workflows and bridge calls |
| `COMPANION_ALLOW_PAID_CALLS` | `true` | Calls to paid provider APIs |
| `COMPANION_MAX_RUN_COST_USD` | `1.0` | Per evaluation, stop paid generation past this known spend |
| `COMPANION_MCP_CAN_APPROVE` | `false` | Let MCP clients submit human approvals |

These are enforced by the gateway, so they apply to the MCP server, the dashboard and direct API
calls alike.

## Providers

| Variable | Provider |
|---|---|
| `COMPANION_ANTHROPIC_API_KEY` | Anthropic API (official SDK) |
| `COMPANION_OPENAI_API_KEY` | OpenAI |
| `COMPANION_GEMINI_API_KEY` | Google Gemini |
| `COMPANION_OPENROUTER_API_KEY` | OpenRouter |
| `COMPANION_OLLAMA_URL` | Ollama, default `http://127.0.0.1:11434`; always listed, free |
| `COMPANION_CUSTOM_BASE_URL`, `COMPANION_CUSTOM_API_KEY` | Any OpenAI-compatible API |

Never set `ANTHROPIC_API_KEY` for this: Claude Code would use it and switch to API billing. The
gateway ignores the unprefixed vendor variables on purpose.

## MCP server

| Variable | Default | Meaning |
|---|---|---|
| `COMPANION_GATEWAY_URL` | `http://127.0.0.1:$COMPANION_PORT` | Gateway the MCP server talks to |
| `COMPANION_AUTOSTART` | `true` | Start the gateway when it isn't running |

## Dashboard

It uses `COMPANION_HOME`, `COMPANION_PORT` and `COMPANION_GATEWAY_URL` the same way, and serves on
`127.0.0.1:3765` (`pnpm start` / `pnpm dev`).

## Tracing

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Also export spans over OTLP/HTTP (`uv pip install opentelemetry-exporter-otlp`) |
| `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` | LangSmith tracing of LangGraph workflows |

## Data folder

```
~/.claude-companion/
  .env                 provider keys and settings (you create it)
  agents.toml          agent registry (you create it)
  gateway.token        random local API token, created on first start
  companion.db         SQLite database (unless COMPANION_DATABASE_URL is set)
  checkpoints.db       LangGraph workflow checkpoints
  gateway.log          log of a gateway started by the MCP server
  reports/             exported HTML / CSV / JSON reports
  agent_runs/<run>/<item>/   task.json, result.json per agent item
```

Deleting `gateway.token` makes a new one on the next start. Reconnect the MCP server and restart the
dashboard afterwards.

## Database migrations

Alembic migrations in `src/companion/migrations` run automatically when the gateway starts, on SQLite
or Postgres.
