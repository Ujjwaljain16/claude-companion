# Claude Companion dashboard

Next.js 16 app (server components, server actions, Tailwind 4) for the local gateway. It reads the
gateway token from `~/.claude-companion/gateway.token` on the server; the browser never sees it.

```powershell
pnpm install
pnpm build
pnpm start        # http://127.0.0.1:3765
```

`pnpm dev` runs the dev server on the same address. The gateway must be running
(`uv run companion-gateway` in the repository root). Set `COMPANION_HOME`, `COMPANION_PORT` or
`COMPANION_GATEWAY_URL` if you changed them for the gateway.

Pages: Overview, Claude Desktop, MCP Tools, Providers, Models, Agents, Agent Workflows (human
approvals), Evaluations (with HTML/CSV/JSON exports), Datasets, Experiments, Traces, Usage, Settings
(permissions and audit log).

Checks: `pnpm test` (Vitest), `pnpm lint`, `pnpm build`.

Layout: `src/app/` pages and the `/reports/[id]` export route, `src/app/actions.ts` server actions
(cancel, approve/reject), `src/lib/gateway.ts` the server-only gateway client, `src/components/` UI.
See the repository's [docs](../docs) for everything else.
