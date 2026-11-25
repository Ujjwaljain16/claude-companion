import { Badge, Card, CardTitle, GatewayError, PageHeader, Stat, Table, Td } from "@/components/ui";
import { fmtMs, fmtPct, fmtUsd, shortTarget, statusTone } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { RunSummary } from "@/lib/types";

type Report = {
  run_id: string;
  status: string;
  message: string;
  target: { provider: string; model?: string | null };
  judge: { provider: string; model?: string; rubric: string } | null;
  summary: RunSummary;
  items: { item_id: string; status: string; output: string; expected: unknown; scores: Record<string, { score: number; passed: boolean; reason: string }>; latency_ms: number | null; error: string | null }[];
};

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await gw<Report>(`/api/evaluations/${encodeURIComponent(id)}`);
  if (!res.ok) return (<><PageHeader title="Evaluation run" /><GatewayError error={res.error} /></>);
  const r = res.data;
  const s = r.summary;
  return (
    <>
      <PageHeader title={`Run ${r.run_id}`} description={`${shortTarget(r.target)}${r.judge ? ` · judged by ${shortTarget(r.judge)} (${r.judge.rubric})` : ""} · ${r.message}`}
        actions={
          <div className="flex items-center gap-3 text-sm">
            <a className="text-accent hover:underline" href={`/reports/${r.run_id}?format=html`} target="_blank">HTML report</a>
            <a className="text-accent hover:underline" href={`/reports/${r.run_id}?format=csv`}>CSV</a>
            <a className="text-accent hover:underline" href={`/reports/${r.run_id}?format=json`}>JSON</a>
            <Badge tone={statusTone(r.status)}>{r.status}</Badge>
          </div>
        } />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Object.entries(s.scores).map(([k, v]) => <Stat key={k} label={k} value={v.mean.toFixed(3)} sub={`pass rate ${fmtPct(v.pass_rate)} · n=${v.n}`} />)}
        <Stat label="Latency p50 / p95" value={fmtMs(s.latency_ms?.p50)} sub={`p95 ${fmtMs(s.latency_ms?.p95)}`} />
        <Stat label="Tokens" value={s.tokens.input + s.tokens.output} sub={`${s.tokens.input} in · ${s.tokens.output} out`} />
        <Stat label="Cost" value={fmtUsd(s.cost_usd)} sub={`${s.errors} errors of ${s.items} items`} tone={s.errors ? "bad" : undefined} />
      </div>
      <Card className="mt-6">
        <CardTitle>Items</CardTitle>
        <Table head={["Item", "Status", "Output", "Expected", "Scores"]}>
          {r.items.map((it) => (
            <tr key={it.item_id}>
              <Td className="font-mono text-xs">{it.item_id}</Td>
              <Td><Badge tone={it.status === "error" ? "bad" : "idle"}>{it.status}</Badge></Td>
              <Td className="max-w-sm whitespace-pre-wrap text-xs">{it.error ?? it.output}</Td>
              <Td className="max-w-xs text-xs text-muted">{it.expected == null ? "–" : typeof it.expected === "string" ? it.expected : JSON.stringify(it.expected)}</Td>
              <Td className="text-xs">
                {Object.entries(it.scores ?? {}).map(([k, v]) => (
                  <div key={k} title={v.reason}><Badge tone={v.passed ? "ok" : "bad"}>{k} {v.score.toFixed(2)}</Badge></div>
                ))}
              </Td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}
