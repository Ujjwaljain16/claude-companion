---
description: Act as the model for agents using the companion session bridge (one request at a time)
argument-hint: "[idle minutes before stopping, default 5]"
---

Serve model calls that agents send to the claude-companion session bridge (model `claude-session`).

Loop:
1. Call `wait_for_model_request` with `wait_seconds` 45.
2. If a request came back, answer it with `answer_model_request` exactly as that agent's model would:
   follow its `system` prompt, use only tools listed in `tools` with schema-valid arguments, honour
   `response_format` and `tool_choice`. No commentary for the user inside the answer.
3. Repeat.

Stop after $ARGUMENTS minutes with no request (default 5 if no number was given). Then report how
many requests you answered and from which agents.

Answers in this mode share one context, so they are not independent samples. For independent or
parallel answers, use /serve-agent-parallel.
