# Workflows

Multi-agent workflows run inside the gateway on LangGraph. Orchestration (graph, routing, retries,
checkpoints, approvals) is separate from inference, which is either a configured provider or the
Claude session.

## draft_review

```
START → supervisor ─┬─▶ drafter ──▶ supervisor
                    ├─▶ critic  ──▶ supervisor
                    ├─▶ human_approval ─▶ finalize → END
                    └─▶ finalize → END
```

- The **supervisor** routes by rule: no draft → drafter; a draft not yet reviewed → critic; the critic
  said `revise` and revisions are left → drafter; otherwise → human approval (if required) or finalize.
- The **drafter** writes or revises the deliverable from the task and the critic's feedback.
- The **critic** replies `{"verdict": "approve"|"revise", "feedback": ...}`.
- **human_approval** pauses until a person approves or rejects. A rejected result finishes with
  `final: null`.
- Drafter and critic retry up to 3 times on retryable provider errors.

Start it with `execute_agent_workflow` (`task`, `provider`, `model`, `require_approval`,
`max_revisions` 0–5).

## Model calls

- `provider: session` (default): each drafter or critic step becomes a `generate` session task with the
  prompt. Claude answers with `{"text": "..."}` via `submit_session_task`, and the graph resumes.
- `provider: <name>` + `model`: the gateway calls the provider directly, and records usage and cost.

## Human approval

Approvals belong to you. By default (`COMPANION_MCP_CAN_APPROVE=false`) the gateway refuses approval
submissions from MCP clients, so Claude can't approve its own work. Approve or reject on the
dashboard's **Agent Workflows** page, which shows the task, the draft, and a comment box.

## Checkpoints

State is stored with LangGraph's SQLite checkpointer in `~/.claude-companion/checkpoints.db`, keyed by
execution id. A workflow that is waiting on a session task or an approval survives a gateway restart
and resumes when the task is answered. Workflows that were mid-step when the gateway stopped are
marked failed.

## Tracing

Each step produces `workflow.step` and `agent.<role>` spans (with token counts for provider calls),
visible with `inspect_agent_trace` or on the Traces page. For LangSmith, set `LANGSMITH_TRACING=true`
and `LANGSMITH_API_KEY` in the gateway's environment.
