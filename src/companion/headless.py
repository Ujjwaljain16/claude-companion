"""Headless backend for the model bridge: answers each request by running
Claude Code non-interactively (`claude -p --output-format json`).

Every request is its own process with a fresh context, so answers are
independent, several can run at once, and nobody has to be at the chat.
It is Claude Code's documented headless mode: auth is whatever that CLI is
signed in with, or a long-lived token from `claude setup-token` passed as
CLAUDE_CODE_OAUTH_TOKEN. On subscription plans Anthropic bills this usage
separately from interactive chat; see docs/model-backends.md.

The CLI runs isolated: no built-in tools, no MCP servers, no settings or
CLAUDE.md from any project, our own system prompt, nothing saved. The agent's
tools are described in the prompt and the reply is forced into a JSON schema,
so tool calling is emulated, then validated against the offered tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from companion.config import Settings

ALIASES = {"sonnet", "opus", "haiku", "fable", "default"}

INSTRUCTIONS = """You are the language model behind an API endpoint used by a software agent.
Continue the conversation in the user message as the assistant would, following the agent's own
instructions below. Reply only through the structured output:
- "content": the assistant's text (may be empty when you only call tools)
- "tool_calls": tools to call now, each {"name", "arguments"}; use only the listed tools and give
  arguments that match their JSON schema; [] when no tool is needed.
Do not mention this wrapper."""


class HeadlessError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def find_claude(settings: Settings) -> str | None:
    if settings.claude_bin:
        return settings.claude_bin if Path(settings.claude_bin).exists() or shutil.which(settings.claude_bin) else None
    return shutil.which("claude")


def reply_schema(tool_names: list[str]) -> dict:
    call = {"type": "object", "properties": {"name": {"type": "string", "enum": tool_names} if tool_names else {"type": "string"},
                                             "arguments": {"type": "object"}},
            "required": ["name", "arguments"]}
    calls: dict[str, Any] = {"type": "array", "items": call}
    if not tool_names:
        calls["maxItems"] = 0
    return {"type": "object", "properties": {"content": {"type": "string"}, "tool_calls": calls},
            "required": ["content", "tool_calls"]}


def render_prompt(req: dict) -> str:
    parts = []
    if req.get("tools"):
        parts.append("<tools>\n" + json.dumps(req["tools"], indent=1) + "\n</tools>")
    choice = req.get("tool_choice")
    if choice in ("required", "any") or (isinstance(choice, dict) and choice.get("type") in ("any", "tool", "function")):
        name = (choice.get("function") or {}).get("name") or choice.get("name") if isinstance(choice, dict) else None
        parts.append(f"You must call {'the tool ' + name if name else 'at least one tool'} in this reply.")
    elif choice == "none" or (isinstance(choice, dict) and choice.get("type") == "none"):
        parts.append("Do not call any tool in this reply.")
    fmt = req.get("response_format") or {}
    if fmt.get("type") == "json_object":
        parts.append('"content" must be a single valid JSON object and nothing else.')
    elif fmt.get("type") == "json_schema":
        schema = (fmt.get("json_schema") or {}).get("schema")
        parts.append('"content" must be valid JSON matching this schema and nothing else: ' + json.dumps(schema))
    lines = []
    for m in req.get("messages", []):
        if m["role"] == "tool":
            flag = " (error)" if m.get("is_error") else ""
            lines.append(f"[tool result{flag} for {m.get('tool_call_id')}]\n{m.get('content', '')}")
            continue
        text = f"[{m['role']}]\n{m.get('content') or ''}"
        for c in m.get("tool_calls") or []:
            text += f"\n(called tool {c['name']} id={c.get('id')} with {json.dumps(c.get('arguments', {}))})"
        lines.append(text)
    parts.append("<conversation>\n" + "\n\n".join(lines) + "\n</conversation>\nWrite the next assistant turn.")
    return "\n\n".join(parts)


class HeadlessBackend:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.command_prefix: list[str] | None = None  # tests swap in a stand-in CLI
        self.sem = asyncio.Semaphore(settings.headless_concurrency)
        self.workdir = settings.home / "headless"

    def status(self) -> dict:
        exe = self.command_prefix or find_claude(self.settings)
        token = bool(self.settings.claude_oauth_token) or bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
        return {"cli": " ".join(exe) if isinstance(exe, list) else exe, "available": bool(exe),
                "oauth_token_configured": token, "model": self.settings.headless_model or "CLI default",
                "concurrency": self.settings.headless_concurrency, "timeout_s": self.settings.headless_timeout_s}

    def model_for(self, requested: str | None) -> str | None:
        if requested and requested.startswith("claude-headless/"):
            return requested.split("/", 1)[1] or None
        if requested and (requested in ALIASES or (requested.startswith("claude-") and requested != "claude-headless")):
            return None if requested == "default" else requested
        return self.settings.headless_model

    def _env(self) -> dict[str, str]:
        drop = {"CLAUDE_CODE_SIMPLE", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}
        env = {k: v for k, v in os.environ.items() if k not in drop and not k.upper().startswith("COMPANION_")}
        if self.settings.claude_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.settings.claude_oauth_token.get_secret_value()
        return env

    async def _run_once(self, req: dict, extra_note: str = "") -> dict:
        exe = self.command_prefix or ([find_claude(self.settings)] if find_claude(self.settings) else None)
        if not exe:
            raise HeadlessError("claude CLI not found; install Claude Code or set COMPANION_CLAUDE_BIN", 503)
        tool_names = [t["name"] for t in req.get("tools", [])]
        system = INSTRUCTIONS + ("\n\nAgent instructions:\n" + req["system"] if req.get("system") else "")
        args = [*exe, "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--setting-sources", "",
                "--no-session-persistence", "--system-prompt", system, "--json-schema", json.dumps(reply_schema(tool_names))]
        model = self.model_for(req.get("model"))
        if model:
            args += ["--model", model]
        self.workdir.mkdir(parents=True, exist_ok=True)
        from companion.agents import _kill_tree  # same process-tree kill as the agent runner

        proc = await asyncio.create_subprocess_exec(*args, cwd=self.workdir, env=self._env(), stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    start_new_session=sys.platform != "win32")
        try:
            out, err = await asyncio.wait_for(proc.communicate((render_prompt(req) + extra_note).encode("utf-8")),
                                              self.settings.headless_timeout_s)
        except asyncio.TimeoutError:
            await _kill_tree(proc)
            raise HeadlessError(f"claude -p took longer than {self.settings.headless_timeout_s:.0f}s", 504) from None
        except asyncio.CancelledError:
            await _kill_tree(proc)
            raise
        try:
            data = json.loads(out.decode("utf-8", errors="replace"))
        except ValueError:
            raise HeadlessError(f"claude -p exited {proc.returncode} without JSON: {err.decode(errors='replace')[-400:]}") from None
        if data.get("is_error"):
            msg = str(data.get("result") or data.get("terminal_reason") or "error")
            if "not logged in" in msg.lower() or "/login" in msg:
                raise HeadlessError(f"claude CLI is not signed in ({msg}). Run `claude setup-token` and set "
                                    "COMPANION_CLAUDE_OAUTH_TOKEN, or sign the CLI in; see docs/model-backends.md", 503)
            raise HeadlessError(f"claude -p error: {msg[:400]}")
        return data

    async def answer(self, req: dict):
        from companion.bridge import AnswerIn

        async with self.sem:
            note = ""
            for attempt in (1, 2):
                data = await self._run_once(req, note)
                reply = data.get("structured_output")
                if reply is None:
                    try:
                        reply = json.loads(str(data.get("result", "")).strip().removeprefix("```json").removesuffix("```"))
                    except ValueError:
                        reply = None
                try:
                    ans = AnswerIn.model_validate(reply)
                    offered = {t["name"] for t in req.get("tools", [])}
                    unknown = [c.name for c in ans.tool_calls if c.name not in offered]
                    if unknown:
                        raise ValueError(f"unknown tools {unknown}")
                    if not ans.content and not ans.tool_calls:
                        raise ValueError("empty reply")
                    break
                except (ValidationError, ValueError, TypeError) as e:
                    if attempt == 2:
                        raise HeadlessError(f"claude -p gave an unusable reply twice: {e}") from None
                    note = f"\n\nYour previous reply was rejected ({e}). Reply again following the schema exactly."
        u = data.get("usage") or {}
        cached = int(u.get("cache_read_input_tokens") or 0)
        usage = {"input": int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + cached,
                 "output": int(u.get("output_tokens") or 0), "cached": cached, "estimated": False,
                 "api_equivalent_usd": data.get("total_cost_usd")}
        return ans, usage
