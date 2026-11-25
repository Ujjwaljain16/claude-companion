import Link from "next/link";
import { Badge, Card, CardTitle, Code, GatewayError, PageHeader, Table, Td } from "@/components/ui";
import { ago, fmtMs, fmtPct, statusTone } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { Run } from "@/lib/types";

type Agents = {
  registry: string;
  exists?: boolean;
  error?: string;
  agents: { name: string; description: string; command: string[]; cwd: string | null; timeout_s: number; env_keys: string[] }[];
};

type AgentRun = Run & { target: Run["target"] & { agent?: string; variant?: string } };

export default async function AgentsPage() {
  const [agents, runs] = await Promise.all([gw<Agents>("/api/agents"), gw<AgentRun[]>("/api/evaluations?limit=200")]);
  if (!agents.ok) return (<><PageHeader title="Agents" /><GatewayError error={agents.error} /></>);
  const agentRuns = runs.ok ? runs.data.filter((r) => r.target.provider === "agent") : [];
  return (
    <>
      <PageHeader
        title="Agents"
        description={<>Your own agents, run over datasets one process per item and scored. Register them by editing <Code>{agents.data.registry}</Code>; tools can run agents but never add them.</>}
      />
      {agents.data.error ? <Card className="mb-6 border-bad/40"><CardTitle>Registry error</CardTitle><p className="text-sm text-bad">{agents.data.error}</p></Card> : null}
      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        {agents.data.agents.map((a) => (
          <Card key={a.name}>
            <CardTitle hint={`timeout ${a.timeout_s}s`}>{a.name}</CardTitle>
            <p className="text-sm text-muted">{a.description || "No description."}</p>
            <pre className="mt-3 overflow-x-auto rounded-md bg-fg/5 p-2 font-mono text-xs text-fg">{a.command.join(" ")}</pre>
            {a.cwd ? <p className="mt-1 font-mono text-xs text-muted">in {a.cwd}</p> : null}
          </Card>
        ))}
        {agents.data.agents.length === 0 ? (
          <Card><p className="text-sm text-muted">No agents registered yet. Copy <Code>examples/agents.toml</Code> to the registry path above and edit it.</p></Card>
        ) : null}
      </div>
      <Card>
        <CardTitle>Agent runs</CardTitle>
        <Table head={["Run", "Agent / variant", "Status", "Success", "Completion", "Avg steps", "Tool success", "Recovery", "Model calls", "Latency p50", "Report", "Created"]} empty="No agent runs yet.">
          {agentRuns.map((r) => {
            const a = r.summary?.agent;
            return (
              <tr key={r.run_id}>
                <Td><Link className="font-mono text-xs text-accent hover:underline" href={`/evaluations/${r.run_id}`}>{r.run_id}</Link></Td>
                <Td>{r.target.agent} <span className="text-muted">/ {r.target.variant}</span></Td>
                <Td><Badge tone={statusTone(r.status)}>{r.status}</Badge></Td>
                <Td className="tabular-nums">{fmtPct(a?.success_rate)}</Td>
                <Td className="tabular-nums">{fmtPct(a?.completion_rate)}</Td>
                <Td className="tabular-nums">{a?.avg_steps ?? "–"}</Td>
                <Td className="tabular-nums">{fmtPct(a?.tool_success_rate)}</Td>
                <Td className="tabular-nums">{fmtPct(a?.failure_recovery_rate)}</Td>
                <Td className="tabular-nums">{a?.model_calls ?? "–"}</Td>
                <Td className="tabular-nums">{fmtMs(r.summary?.latency_ms?.p50)}</Td>
                <Td className="whitespace-nowrap text-xs">
                  <a className="text-accent hover:underline" href={`/reports/${r.run_id}?format=html`} target="_blank">HTML</a>{" · "}
                  <a className="text-accent hover:underline" href={`/reports/${r.run_id}?format=csv`}>CSV</a>
                </Td>
                <Td className="whitespace-nowrap text-muted">{ago(r.created_at)}</Td>
              </tr>
            );
          })}
        </Table>
      </Card>
    </>
  );
}
