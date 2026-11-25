# Troubleshooting

## The `companion` tools don't show up in the Code tab

- The session must be started in this repository, or in a project whose `.mcp.json` lists `companion`.
- Claude Code asks once to allow project MCP servers. If you declined, re-enable it from Claude Code's
  MCP settings (`/mcp`).
- `uv` must be on the PATH that Claude Desktop sees. Check with `uv --version` in a new terminal.

## New or changed tools are missing after updating the code

A running session keeps the MCP server it started with. Reconnect `companion` (`/mcp` → reconnect) or
start a new session.

## "gateway not reachable at http://127.0.0.1:8765"

- With `COMPANION_AUTOSTART=false`, start it yourself: `uv run companion-gateway`.
- Otherwise the gateway the MCP server started died. Look at `~/.claude-companion/gateway.log`. The
  next tool call starts a new one (older versions of the MCP server didn't; reconnect it).
- Port 8765 in use by something else: set `COMPANION_PORT` for the gateway, the MCP server (in
  `.mcp.json` `env`) and the dashboard.

## The gateway stops when the Code session ends

A gateway started by the MCP server can be killed together with it: MCP clients stop the server's
process tree on exit. Waiting evaluations and workflows resume after a restart; running ones are
marked failed. Run `uv run companion-gateway` yourself when you need it to stay up, for example for the
dashboard or long agent runs.

## My agent's call through the bridge hangs, then returns 504

Nobody was serving. Ask Claude to "serve my agent" before or while the agent runs. Raise
`COMPANION_BRIDGE_TIMEOUT_S` if you need more than 900 s, and set your client's own timeout at least
as high.

## My agent got two answers / Claude saw the same request twice

The client retried. Set `max_retries=0` (OpenAI, Anthropic SDKs) for the bridge.

## Bridge returns 401

The API key must be the contents of `~/.claude-companion/gateway.token` (no newline), not an Anthropic
key.

## Agent run items fail with "agent exited with code N without writing a result"

The agent crashed before writing `COMPANION_RESULT_FILE`. Look at `stderr_tail` in the item (dashboard
run page, `analyze_failures`) or at `~/.claude-companion/agent_runs/<run>/<item>/`. Using `ca.run(...)`
from the helper always writes a result, including the traceback.

## Agent run items time out

Raise `timeout_s` in `agents.toml`. If the agent uses the bridge, remember that every model call waits
for Claude, so allow a few seconds per call.

## "agent 'x' is not registered"

Check the path that `list_agents` reports, and the TOML syntax (`GET /api/agents` shows parse
errors). Names allow letters, digits, `_`, `.` and `-`.

## `uv sync` fails: "failed to remove file ... companion-mcp.exe ... used by another process"

The Code session's MCP server holds the executable open. Either install without touching the project,
`uv pip install <package>`, or stop that session and run `uv sync` again.

## "database is locked"

Fixed with a 30 s SQLite lock timeout. If it still happens under heavy parallel agent runs, lower
`parallel` or switch to Postgres.

## Dashboard says "Gateway unavailable"

Start the gateway. The dashboard and gateway must agree on `COMPANION_HOME` (token location) and
`COMPANION_PORT`.

## Costs show as "unknown"

The model has no price entry. Add it with `COMPANION_PRICING_FILE`. Bridge calls are always $0, and
their tokens are estimates.
