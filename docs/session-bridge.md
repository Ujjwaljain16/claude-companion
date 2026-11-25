# Session model bridge

Run an agent you are building on Claude, through your signed-in Claude Desktop Code session, with no
API key.

## How it works

```
your agent ──POST /v1/chat/completions or /v1/messages──▶ gateway ──session task──▶ Claude in the Code tab
           ◀────────────── normal API response ────────── gateway ◀─answer_model_request─┘
```

1. Your agent sends a normal OpenAI- or Anthropic-format request to the gateway.
2. The gateway parks it and holds the HTTP request open.
3. Claude, asked to "serve my agent", calls `wait_for_model_request`, sees the system prompt, messages
   and tools in a normalized form, and replies with `answer_model_request`: text, tool calls, or both.
4. The gateway turns that into a standard API response, including tool-call IDs and the stop reason,
   and your agent continues.

Nothing here touches Claude credentials. The gateway never calls Anthropic; it only relays what Claude
writes in your chat.

## Connect an agent

| | OpenAI format | Anthropic format |
|---|---|---|
| Base URL | `http://127.0.0.1:8765/v1` | `http://127.0.0.1:8765` |
| Endpoint | `POST /chat/completions`, `GET /models` | `POST /v1/messages` |
| API key | contents of `~/.claude-companion/gateway.token` | same, as `x-api-key` or `Authorization: Bearer` |
| Model | `claude-session` (any id is accepted) | same |

Optional headers: `X-Agent-Name` labels the requests in traces; `X-Companion-Tag` links calls to an
agent-runner item (the helper in `sdk/` sets it for you).

```python
from pathlib import Path
from openai import OpenAI

token = (Path.home() / ".claude-companion" / "gateway.token").read_text().strip()
client = OpenAI(base_url="http://127.0.0.1:8765/v1", api_key=token,
                default_headers={"X-Agent-Name": "my-agent"}, timeout=900, max_retries=0)
```

```python
import anthropic
client = anthropic.Anthropic(base_url="http://127.0.0.1:8765", api_key=token, timeout=900, max_retries=0)
```

With LangChain: `ChatAnthropic(model="claude-session", base_url="http://127.0.0.1:8765", api_key=token)`,
or `ChatOpenAI(model="claude-session", base_url="http://127.0.0.1:8765/v1", api_key=token)`. Any
framework that lets you set a base URL works the same way.

Set a long timeout (the default wait is 900 s) and turn off client retries. A retry sends a second,
duplicate request that Claude would also have to answer.

## Serve it

In the Code tab: *"Serve my agent."* Claude loops `wait_for_model_request` → `answer_model_request`
until no request arrives. Then run your agent. Try the examples:

```powershell
uv run python examples/agents/trip_agent.py "How far is Paris from Berlin by road, in miles?"
```

```powershell
uv run python examples/agents/anthropic_sdk_agent.py
```

## What's supported

| Feature | Status |
|---|---|
| Text replies | yes |
| Tool calling, including several calls in one reply | yes; the gateway rejects tool names the request didn't offer |
| Tool results / multi-turn history | yes; normalized for Claude as `role: tool` messages |
| Streaming (`stream: true`) | yes, as valid SSE in both formats, but the whole answer arrives at once when Claude replies |
| Structured output (`response_format`, forced `tool_choice`) | passed to Claude, who follows it; **not validated** by the gateway |
| Images | replaced with `[image omitted]` |
| Token usage | returned, but **estimated** (characters / 4); no cache fields |
| Cost | recorded as $0 (runs on your Claude plan) |
| Prompt caching, `count_tokens`, batches | not supported |

## Limits

- **Speed:** each call is one turn in the chat, typically a few seconds, served one at a time even if
  your agent sends several in parallel.
- **Someone has to serve:** with no session serving, a request waits up to `COMPANION_BRIDGE_TIMEOUT_S`
  (900 s), then gets a 504.
- **One model:** every answer comes from the model running the serving session, whatever model id you
  send, so model routing or tiers have no effect.
- **Size:** a request's messages plus tools are capped at 200,000 characters (HTTP 413 above that).
- **Disconnects:** if your agent gives up on a request, the gateway expires it and Claude never sees
  it.

Good uses: building and debugging an agent, recording test cassettes, small agent-runner evaluations.
For bulk runs, use a provider key or a local model (Ollama) through the same agent code.

## Where to see it

Each bridge call is an execution of kind `model_request`, with a `bridge.model_request` span. It shows
up in the dashboard's Traces and Usage pages, with provider `claude-session`.
