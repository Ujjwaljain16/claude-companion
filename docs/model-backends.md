# Model backends: how your agent's model calls get answered

Your agent always talks to the same local endpoint, in OpenAI format at `http://127.0.0.1:8765/v1` or
Anthropic format at `http://127.0.0.1:8765/v1/messages`, with the gateway token as its API key. The
**model id** it sends decides who answers:

| Model id | Backend | Who answers |
|---|---|---|
| `claude-session` | **Session** (path B) | Claude in your Claude Code chat, one request at a time or in parallel helpers |
| `claude-headless`, `claude-headless/<model>` | **Headless** (path A) | A fresh `claude -p` process per request |
| anything else | `COMPANION_BRIDGE_DEFAULT_BACKEND` (default `session`) | so frameworks that insist on their own model names still work |

Separately, the gateway's own evaluations and workflows can call real provider APIs with your keys
(Anthropic, OpenAI, Gemini, OpenRouter, Ollama, any OpenAI-compatible API). See
[configuration](configuration.md#providers).

## Which one should I use?

| | Session, one at a time | Session, parallel helpers | Headless `claude -p` | Provider API key | Local model (Ollama) |
|---|---|---|---|---|---|
| Extra setup | none | none | sign the CLI in once (`claude setup-token`) | API key | install Ollama + a model |
| Speed per call | a chat turn, a few seconds | same, several at once | a few seconds, no human | fastest | depends on your GPU |
| Parallel calls | no | yes (number of helpers) | yes (`COMPANION_HEADLESS_CONCURRENCY`) | yes | limited by GPU |
| Independent samples | **no**, one shared context | yes, fresh helper per request | yes, fresh process per request | yes | yes |
| Unattended | no, a chat must keep serving | no, a chat must keep coordinating | **yes** | yes | yes |
| Real token counts | estimated | estimated | yes, including cache reads | yes | yes |
| Billing | your Claude plan's chat usage | your Claude plan's chat usage, one helper per call | your Claude plan's Agent SDK / `claude -p` usage, see below | API pricing | free |
| Native tool calling | emulated by Claude in chat | emulated | emulated, schema-validated | native | native |
| Best for | debugging an agent step by step | small evals that need independent answers | long or unattended runs on your subscription | production, bulk, CI | offline, free bulk runs |

Rules of thumb:
- **Building or debugging an agent:** session, one at a time. You see every call in the chat.
- **Small eval where independence matters** (majority voting, self-consistency, forking on
  disagreement): parallel helpers, or headless.
- **Anything unattended, long or large:** headless, a provider key, or a local model.
- **Anything you'll share with other people or run as a service:** a provider API key. The
  subscription paths are for your own personal use.

---

## Path B1: session, one at a time

**What:** each model call becomes a request that Claude, in your Claude Code chat, claims with
`wait_for_model_request` and answers with `answer_model_request`.

**Why:** zero setup and zero extra login, and you watch every step.

**How:**
1. Open a Claude Code session in this repository (or one whose `.mcp.json` lists `companion`).
2. Run `/serve-agent`, or say "serve my agent".
3. Run your agent with model `claude-session`.

**Limits:** one call at a time; every answer shares one context, so they aren't independent samples;
someone has to keep the chat open; tokens are estimated.

## Path B2: session, parallel helpers

**What:** a coordinator in your chat watches the queue (`watch_model_requests`, counts only) and
starts one `model-responder` subagent per waiting request. Each helper claims one request, answers
it in its own fresh context, and exits. Requests are claimed atomically, so two helpers never answer
the same one, and a claim that isn't answered within `COMPANION_BRIDGE_CLAIM_TTL_S` (180 s) goes back
to the queue.

**Why:** independent answers and parallelism with no new login.

**How:**
1. Same session setup as B1. The helper is defined in
   [.claude/agents/model-responder.md](../.claude/agents/model-responder.md) and loads with the project.
2. Run `/serve-agent-parallel 3 10` (up to 3 helpers at once, stop after 10 idle minutes).
3. Run your agent with model `claude-session`.

**Limits:** the coordinating chat still has to run. Every helper is a full subagent: in a test, three
helpers each used about 65k tokens of their own context to answer one short request, so this costs
far more of your plan per call than B1 or headless. Keep helpers to what your machine handles (each
is a separate process). Tokens reported to your agent are estimates.

**Verified:** three identical concurrent requests were each claimed by a different fresh helper (two
running at once) and answered with valid JSON, as `response_format` asked; the claim logic is also
covered by tests.

## Path A: headless `claude -p`

**What:** for each request, the gateway runs Claude Code in its documented non-interactive mode:

```
claude -p --output-format json --tools "" --strict-mcp-config --setting-sources "" \
  --no-session-persistence --system-prompt <wrapper + your agent's system prompt> \
  --json-schema <{content, tool_calls} with your tool names> [--model <model>]
```

- The conversation and tool definitions go in on stdin.
- The reply is forced into a JSON schema, then checked against the tools your agent offered. An
  invalid reply is retried once with the error, and fails with HTTP 502 after that.
- The CLI runs isolated: no built-in tools, no MCP servers, no project or user settings, an empty
  working folder (`~/.claude-companion/headless`), nothing saved, and `CLAUDE_CODE_SIMPLE`,
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` removed from its environment.
  The result is that it runs on your subscription login, not on an API key that happens to be set.
- The real usage from the CLI (input, output and cache-read tokens) is returned to your agent and
  recorded. The CLI's `total_cost_usd` (what the call would cost on the API) is kept as
  `api_equivalent_usd`; recorded cost is $0.

**Why:** fast, parallel, independent and unattended on your Claude subscription, with no API key and
no change to your agent's code.

**How:**
1. Install Claude Code so a `claude` command exists, or point `COMPANION_CLAUDE_BIN` at the
   executable.
2. Sign it in for non-interactive use, once, from a normal terminal (not from inside Claude Desktop):

   ```powershell
   claude setup-token
   ```

   It prints a long-lived token. Put it in `~/.claude-companion/.env`:

   ```
   COMPANION_CLAUDE_OAUTH_TOKEN=<token>
   ```

   The gateway passes it to the CLI as `CLAUDE_CODE_OAUTH_TOKEN`. If your `claude` CLI is already
   signed in for headless use, you can skip this.
3. Restart the gateway, then check one real round trip:

   ```powershell
   uv run companion-headless-check
   ```

   You should see `OK in N s: tool_calls=[{'name': 'ping', ...}]`.
4. Run your agent with model `claude-headless`, or `claude-headless/sonnet`, `claude-headless/opus`,
   or a full model id.

Settings: `COMPANION_HEADLESS_MODEL` (default model), `COMPANION_HEADLESS_CONCURRENCY` (default 3,
max 16), `COMPANION_HEADLESS_TIMEOUT_S` (default 300), `COMPANION_CLAUDE_BIN`.

**Things to know before relying on it:**
- **It needs a login.** This path uses Claude Code's token login (`claude setup-token`). That's an
  official command, but it is a CLI login step, unlike the session paths.
- **Billing.** Anthropic's [authentication docs](https://code.claude.com/docs/en/authentication)
  state that, from June 15, 2026, `claude -p` and Agent SDK usage on subscription plans draws from a
  separate monthly credit, not your interactive limits. Check the current terms for your plan before
  a long run.
- **Personal use only.** Use it for your own agents on your own machine. Don't offer your
  subscription to other people or a product through it; use an API key for that.
- **Inside Claude Desktop sessions** the `CLAUDE_CODE_SIMPLE=1` variable makes child `claude`
  processes ignore OAuth. The gateway strips it, but if you run `claude -p` yourself from such a
  session, it will say "Not logged in".
- **Known issue:** some users report `setup-token` tokens being rejected with 401 by `claude -p`.
  `companion-headless-check` tells you quickly whether yours works.
- **Tool calling is emulated.** It's reliable for normal tool use because the reply is
  schema-constrained and validated, but it isn't the API's native tool calling.
- **Startup time:** each call starts a CLI process (a second or two) before the model runs. There's no
  prompt caching across calls, beyond what the CLI does itself.

**Verified so far:** flag parsing and the result format against Claude Code 2.1.330, the "not logged
in" error, and isolation, retries, parallelism, routing and usage, with a stand-in CLI in the test
suite. A full round trip on a real subscription token needs your token: run
`companion-headless-check`.

## Provider API keys and local models

For production-like or shared use, use a provider key (`COMPANION_ANTHROPIC_API_KEY` and others).
Point your agent straight at the provider, or use the gateway's evaluations with a provider target.
For free, offline bulk runs, use Ollama (`COMPANION_OLLAMA_URL`) and point your agent at
`http://127.0.0.1:11434/v1`.
