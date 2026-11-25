# Security

This is a single-user tool for your own machine. The goal: nothing outside your user account can drive
it, nothing it exposes runs arbitrary code, it never touches Claude credentials, and every change is
recorded.

## What it never does

- Read, copy or forward Claude Desktop or Claude Code credentials, OAuth tokens, or their auth storage.
- Intercept Desktop traffic or call undocumented Desktop APIs.
- Use your Claude credentials itself. The session backend relays what Claude writes in your chat.
  The headless backend runs the official `claude` CLI, which authenticates itself with its own login
  or the `setup-token` token you configure. The gateway never calls Anthropic with your credentials.
- Desktop detection only checks that the install folder exists; it doesn't open anything inside it.

## Network

- The gateway binds to `127.0.0.1` and refuses to start on another address unless
  `COMPANION_ALLOW_NON_LOCAL_BIND=true`.
- Requests with a non-local `Host` header get 403. This blocks DNS-rebinding attacks from web pages.
- Every endpoint except `/api/health` needs the 256-bit random token in
  `~/.claude-companion/gateway.token`, compared in constant time. A web page can't read that file, so
  it can't call the gateway.
- The dashboard binds to `127.0.0.1`. It reads the token on the server only; the browser never sees it.
  Its mutations are Next.js server actions, which check the request origin.

## Code execution

- No MCP tool or API endpoint runs shell commands or code you pass it.
- Agents run commands, but only commands listed in `agents.toml`, a file you edit yourself. Tools can
  list and run registered agents and can never add or change one. Commands run without a shell.
- Agent processes don't get the gateway's provider keys (every `COMPANION_*` variable is stripped,
  except the run's own contract variables). Timeouts and cancels kill the whole process tree.
- Workflows and scorers are selected by name from fixed lists. Regex patterns are capped at 200
  characters and input at 20,000 characters.
- Inputs are validated with Pydantic models and JSON Schemas (types, lengths, ranges, unknown fields
  rejected for dataset items).

## Permissions and oversight

- Gateway switches: write, execute, paid calls, a per-run budget, and MCP approvals (see
  [configuration](configuration.md#permissions)). They apply to every client.
- Claude Code itself asks before each tool call unless you allow it.
- Human approvals in workflows can only come from the dashboard by default, so Claude can't approve
  its own output.
- The audit log records dataset versions, experiments, runs, agent runs, workflows, task submissions
  and cancellations, with the actor (`mcp`, `dashboard`, `api`). View it on the Settings page or at
  `/api/audit`.

## Headless backend

- `claude -p` runs with no built-in tools, no MCP servers, no settings sources, an empty working
  folder and no saved session, so a request can't make it read files, run commands or reach your MCP
  servers. It can only produce the `{content, tool_calls}` JSON, and tool calls are checked against
  the tools your agent offered. Your agent runs those tools itself.
- `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_SIMPLE` and the
  gateway's `COMPANION_*` settings are removed from its environment.
- `COMPANION_CLAUDE_OAUTH_TOKEN` grants access to your Claude subscription. Keep it in the git-ignored
  `.env` and treat it like a password. Don't use it to serve other people.

## Secrets

- Provider keys live in `~/.claude-companion/.env`, are held as secret strings, and are never returned
  by any endpoint (a test checks that the provider listing doesn't contain them).
- `.env` is git-ignored. If you copy the gateway token into a project's `.env`, keep it git-ignored too.

## Known limits

- Any process running as your user can read the token file and call the gateway, as with any local
  developer tool.
- The `X-Companion-Actor` header is set by the client. The approval rule is a guardrail against Claude
  approving its own work through MCP, not a boundary against code running as you.
- Answers that come through the bridge are written by Claude in your chat. Review what your agent does
  with them, as you would with any model output.
