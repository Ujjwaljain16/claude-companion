---
description: Serve bridge model calls with one fresh model-responder helper per request, in parallel
argument-hint: "[max helpers at once, default 3] [idle minutes before stopping, default 5]"
---

Coordinate serving of model calls sent to the claude-companion session bridge (model
`claude-session`). You do not answer requests yourself and you never read their contents. Each
request gets its own `model-responder` helper, so answers are independent and run in parallel.

Arguments: $ARGUMENTS. The first number is the maximum helpers at once (default 3); the second is
idle minutes before stopping (default 5).

Loop:
1. Call `watch_model_requests` with `wait_seconds` 45. It returns counts only.
2. If `unclaimed` is above 0, start `min(unclaimed, max helpers)` `model-responder` subagents in a
   single message, so they run in parallel. Give each the prompt "Serve one request." Wait for them
   to finish.
3. Repeat. A helper that replies `none` found nothing to claim; that's fine.

Stop after the idle time with `unclaimed` and `claimed` both 0. Then report how many requests were
served. Keep your own messages short; this loop can run for a long time.
