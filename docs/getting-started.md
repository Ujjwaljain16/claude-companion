# Getting started

## 1. Install

Requirements: Windows, macOS or Linux; Python 3.11+; [uv](https://docs.astral.sh/uv/); Claude Desktop
with the Code tab. For the dashboard you also need Node 20+ and pnpm.

```powershell
cd D:\Projects\claude-companion
uv sync
```

That creates `.venv` and installs the `companion-gateway` and `companion-mcp` commands.

## 2. Connect Claude Desktop

1. Open Claude Desktop, go to the **Code** tab, and start a session whose folder is this repository.
2. Claude Code reads [`.mcp.json`](../.mcp.json):

   ```json
   { "mcpServers": { "companion": { "type": "stdio", "command": "uv",
       "args": ["run", "--frozen", "--quiet", "companion-mcp"], "env": {} } } }
   ```

   It asks once whether to allow the `companion` server. Allow it. That's the only setup step: no
   login, no key.
3. Ask Claude to run `inspect_system_health`. The MCP server starts the gateway in the background if it
   isn't running (logs go to `~/.claude-companion/gateway.log`). The result shows Claude Desktop as
   installed, `claude-code` as the MCP client, and which providers are configured.

Claude Code asks before each tool call unless you allow the tool. You can allow the read-only ones
(`inspect_*`, `list_*`, `get_*`, `retrieve_*`, `search_*`) freely.

### Using the tools from other projects

Add the same entry to that project's `.mcp.json`, pointing uv at this repository:

```json
{ "mcpServers": { "companion": { "type": "stdio", "command": "uv",
    "args": ["run", "--frozen", "--quiet", "--directory", "D:\\Projects\\claude-companion", "companion-mcp"] } } }
```

### After updating this repository

A running Code session keeps the MCP server it started with. To pick up new or changed tools, reconnect
the `companion` server (type `/mcp` in the message box and reconnect it) or start a new session.

## 3. First evaluation (no keys needed)

Ask Claude:

> Create a dataset called `arith` with 5 questions like "What is 17 * 3? Reply with the number only."
> and expected answers. Evaluate it with provider `session` and the `exact_match` scorer.

What happens:

1. `create_evaluation_dataset` stores version 1 of `arith`.
2. `run_evaluation` with provider `session` queues the items as a *session task* and waits.
3. Claude reads the task (`list_session_tasks`), writes the answers as the model under test, and submits
   them (`submit_session_task`).
4. The gateway scores them and `retrieve_evaluation_report` returns the summary.

With a provider key configured, use e.g. `provider: openai, model: <id>` instead and the gateway makes
the calls itself.

## 4. Run your own agent on Claude

See [session-bridge.md](session-bridge.md). In short: point your agent at `http://127.0.0.1:8765/v1`
with model `claude-session` and the gateway token as its API key, tell Claude "serve my agent", and
run the agent.

## 5. Evaluate your own agent

See [agent-runner.md](agent-runner.md). In short: register it in `~/.claude-companion/agents.toml`, wrap
it with the small helper in `sdk/`, and ask Claude to run it over a dataset.

## 6. Dashboard

```powershell
uv run companion-gateway
```

```powershell
cd dashboard; pnpm install; pnpm build; pnpm start
```

Open http://127.0.0.1:3765. Pages: Overview, Claude Desktop, MCP Tools, Providers, Models, Agents,
Agent Workflows, Evaluations, Datasets, Experiments, Traces, Usage, Settings. Human approvals for
workflows are made here.

Run the gateway yourself, as above, when you use the dashboard. A gateway that the MCP server starts
can stop when the Code session ends (see [troubleshooting](troubleshooting.md)).

## 7. Optional: provider keys

Create `%USERPROFILE%\.claude-companion\.env`:

```
COMPANION_ANTHROPIC_API_KEY=...
COMPANION_OPENAI_API_KEY=...
COMPANION_GEMINI_API_KEY=...
COMPANION_OPENROUTER_API_KEY=...
COMPANION_OLLAMA_URL=http://127.0.0.1:11434
COMPANION_CUSTOM_BASE_URL=http://127.0.0.1:8000/v1
COMPANION_CUSTOM_API_KEY=...
```

Restart the gateway afterwards. Don't put `ANTHROPIC_API_KEY` in your environment: Claude Code would use
it and bill the API instead of your subscription. That's why every gateway key has the `COMPANION_`
prefix.
