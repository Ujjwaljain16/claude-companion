import Link from "next/link";
import { Badge, Card, GatewayError, PageHeader, Table, Td } from "@/components/ui";
import { ago, fmtMs, fmtPct, fmtUsd, shortTarget, statusTone } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { Run } from "@/lib/types";

export default async function EvaluationsPage() {
  const runs = await gw<Run[]>("/api/evaluations?limit=100");
  return (
    <>
      <PageHeader title="Evaluations" description="Batch runs over versioned datasets: deterministic scorers, LLM or session judges, latency, tokens and cost." />
      {!runs.ok ? <GatewayError error={runs.error} /> : (
        <Card>
          <Table head={["Run", "Target", "Status", "Scores (mean / pass rate)", "Latency p50", "Tokens", "Cost", "Created"]} empty="No runs yet.">
            {runs.data.map((r) => (
              <tr key={r.run_id}>
                <Td><Link className="font-mono text-xs text-accent hover:underline" href={`/evaluations/${r.run_id}`}>{r.run_id}</Link></Td>
                <Td>{shortTarget(r.target)}</Td>
                <Td><Badge tone={statusTone(r.status)}>{r.status}</Badge></Td>
                <Td className="text-xs">
                  {r.summary ? Object.entries(r.summary.scores).map(([k, v]) => (
                    <div key={k}><span className="text-muted">{k}</span> {v.mean.toFixed(3)} / {fmtPct(v.pass_rate)}</div>
                  )) : "–"}
                </Td>
                <Td className="tabular-nums">{fmtMs(r.summary?.latency_ms?.p50)}</Td>
                <Td className="tabular-nums text-muted">{r.summary ? r.summary.tokens.input + r.summary.tokens.output : "–"}</Td>
                <Td className="tabular-nums">{r.summary ? fmtUsd(r.summary.cost_usd) : "–"}</Td>
                <Td className="whitespace-nowrap text-muted">{ago(r.created_at)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </>
  );
}
