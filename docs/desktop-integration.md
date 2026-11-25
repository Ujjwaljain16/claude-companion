# How this connects to Claude Desktop, and what it cannot do

Findings from checking the official documentation and this machine on 2026-11-24.

## What is supported

- **The Code tab is Claude Code.** It loads MCP servers through Claude Code's own configuration:
  a project's `.mcp.json` (project scope), `~/.claude.json` (user/local scope), plugins, and
  claude.ai connectors. It does not read `claude_desktop_config.json`, which is for the Chat tab.
  (On this machine that file does not exist; Desktop is the MSIX install.)
- **Project-scoped servers need a one-time approval.** The first time a session opens a folder
  with a `.mcp.json`, Claude Code asks whether to allow its servers. That is the only step. No
  login, no `/login`, no API key, no token copying: the server runs inside the session the user
  is already signed in to.
- **Transport:** stdio. Claude Code starts the command in `.mcp.json` and speaks JSON-RPC over
  stdin/stdout. Tool schemas are JSON Schema; read-only/destructive hints are passed as MCP tool
  annotations.
- **Timeouts:** `MCP_TIMEOUT` controls server start-up; tool calls are capped by
  `MCP_TOOL_TIMEOUT` or a per-server timeout, and progress notifications do not extend that cap.
  This is why long jobs return an `execution_id` immediately and tools wait at most 55 seconds.
- **Permissions:** Claude Code asks before each tool call unless the user allows the tool. The
  gateway adds its own switches (write / execute / paid calls / budget / approvals) on top.

## What is not supported, and how the design handles it

| Limitation | Consequence | What this project does instead |
|---|---|---|
| Claude Code does not implement MCP **sampling** (open feature request anthropics/claude-code#1785), and sampling is deprecated from protocol 2026-07-28. | An MCP server cannot ask the Desktop model to generate text. The gateway can never use the subscription as a programmatic model or judge. | Target `session`: the gateway queues prompts as *session tasks*; the Claude model in the chat reads them with `list_session_tasks` and answers with `submit_session_task`, visibly, in the user's conversation. For your own agents, the [session model bridge](session-bridge.md) does the same behind OpenAI/Anthropic-compatible endpoints: each model call becomes a chat turn that Claude answers with `answer_model_request`. Otherwise use a configured provider key. |
| No documented API for reading Desktop's state. | "Connected" cannot be read from the app. | The MCP server reports each tool call to the gateway (client name/version from the MCP handshake). The dashboard shows "last call", not a socket. Install detection only checks that the install folder exists. |
| `ANTHROPIC_API_KEY` in Claude Code's environment switches it to API billing. | Reusing that variable for the gateway could change how Desktop authenticates. | All gateway keys use a `COMPANION_` prefix and live in `~/.claude-companion/.env`. |
| The MCP stdio client may terminate the server's process tree when the session ends (the Python SDK client does, via a Windows job object). | A gateway auto-started by the MCP server can stop with the session. | Waiting evaluations and LangGraph workflows are persisted and resume after restart. For a persistent gateway (and the dashboard), run `uv run companion-gateway` yourself. |

## Not done, on purpose

No network interception, no reading of Desktop's auth stores or OAuth tokens, no undocumented
Desktop APIs, no proxy that pretends to be the Claude API.

## Sources

- Claude Code MCP docs: https://code.claude.com/docs/en/mcp
- Claude Desktop Code tab: https://claude.com/docs/third-party/claude-desktop/code
- Sampling feature request: https://github.com/anthropics/claude-code/issues/1785
- MCP sampling spec (2025-11-25): https://modelcontextprotocol.io/specification/2025-11-25/client/sampling
