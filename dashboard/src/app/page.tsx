import Link from "next/link";
import { ExecutionsTable } from "@/components/executions";
import { Badge, Card, CardTitle, GatewayError, PageHeader, Stat, Table, Td } from "@/components/ui";
import { fmtPct, mcpState, shortTarget, statusTone } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { Execution, Run, System } from "@/lib/types";

export default async function Overview() {
  const [sys, execs, runs] = await Promise.all([
    gw<System>("/api/system"),
    gw<Execution[]>("/api/executions?limit=8"),
    gw<Run[]>("/api/evaluations?limit=6"),
  ]);
  if (!sys.ok) return (<><PageHeader title="Overview" /><GatewayError error={sys.error} /></>);
  const s = sys.data;
  const mcp = mcpState(s.mcp_clients);
  const configured = s.providers.filter((p) => p.configured);
  const active = (s.executions.running ?? 0) + (s.executions.queued ?? 0);
  const calls = s.model_calls_24h;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Desktop connectivity and model API availability are separate: the Code tab talks to this gateway over MCP, while model calls made by the gateway use only the provider keys configured here."
      />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Claude Desktop" value={s.desktop.installed ? "Installed" : "Not found"} tone={s.desktop.installed ? "ok" : "warn"} sub={`MCP: ${mcp.label}`} />
        <Stat label="Model API providers" value={`${configured.length}/${s.providers.length}`} tone={configured.length ? "ok" : "warn"} sub={configured.map((p) => p.name).join(", ") || "none configured; session target still works"} />
        <Stat label="Active executions" value={active} sub={`${s.executions.waiting ?? 0} waiting · ${s.open_session_tasks} open session tasks`} tone={active ? "info" : "idle"} />
        <Stat label="Model calls (24h)" value={calls.total} sub={`${calls.errors} errors · ${calls.total ? fmtPct(calls.errors / calls.total) : "–"} error rate`} tone={calls.errors ? "bad" : "idle"} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card>
          <CardTitle hint={<Link className="text-accent hover:underline" href="/traces">all</Link>}>Recent executions</CardTitle>
          {execs.ok ? <ExecutionsTable rows={execs.data} empty="No executions yet. Ask Claude to run an evaluation." /> : <p className="text-sm text-bad">{execs.error}</p>}
        </Card>
        <Card>
          <CardTitle hint={<Link className="text-accent hover:underline" href="/evaluations">all</Link>}>Latest evaluation scores</CardTitle>
          <Table head={["Run", "Target", "Status", "Scores"]} empty="No evaluation runs yet.">
            {(runs.ok ? runs.data : []).map((r) => (
              <tr key={r.run_id}>
                <Td><Link className="font-mono text-xs text-accent hover:underline" href={`/evaluations/${r.run_id}`}>{r.run_id}</Link></Td>
                <Td>{shortTarget(r.target)}</Td>
                <Td><Badge tone={statusTone(r.status)}>{r.status}</Badge></Td>
                <Td className="text-xs text-muted">
                  {r.summary ? Object.entries(r.summary.scores).map(([k, v]) => `${k} ${v.mean.toFixed(2)}`).join(" · ") : "–"}
                </Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </>
  );
}
