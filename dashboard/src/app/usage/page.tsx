import { Card, CardTitle, GatewayError, LinkButton, PageHeader, Stat, Table, Td } from "@/components/ui";
import { fmtMs, fmtPct, fmtUsd } from "@/lib/format";
import { gw } from "@/lib/gateway";

type Usage = {
  days: number;
  note: string;
  totals: { calls: number; errors: number; cost_usd: number };
  by_model: { provider: string; model: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number | null; avg_latency_ms: number; errors: number }[];
  by_day: Record<string, { calls: number; cost_usd: number; tokens: number }>;
};

export default async function UsagePage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const raw = Number((await searchParams).days ?? 7);
  const days = [1, 7, 30, 90].includes(raw) ? raw : 7;
  const res = await gw<Usage>(`/api/usage?days=${days}`);
  return (
    <>
      <PageHeader title="Usage" description={res.ok ? res.data.note : undefined}
        actions={<div className="flex gap-2">{[1, 7, 30, 90].map((d) => <LinkButton key={d} href={`/usage?days=${d}`}>{d === days ? <b>{d}d</b> : `${d}d`}</LinkButton>)}</div>} />
      {!res.ok ? <GatewayError error={res.error} /> : (() => {
        const u = res.data;
        const daysData = Object.entries(u.by_day);
        const peak = Math.max(1, ...daysData.map(([, d]) => d.calls));
        return (
          <>
            <div className="grid grid-cols-3 gap-4">
              <Stat label="Model calls" value={u.totals.calls} />
              <Stat label="Error rate" value={u.totals.calls ? fmtPct(u.totals.errors / u.totals.calls) : "–"} sub={`${u.totals.errors} errors`} tone={u.totals.errors ? "bad" : undefined} />
              <Stat label="Known cost" value={fmtUsd(u.totals.cost_usd)} sub="models without a price entry count as unknown" />
            </div>
            <Card className="mt-6">
              <CardTitle>Calls per day</CardTitle>
              {daysData.length ? (
                <div className="flex h-40 items-end gap-2">
                  {daysData.map(([day, d]) => (
                    <div key={day} className="flex flex-1 flex-col items-center gap-1" title={`${day}: ${d.calls} calls, ${d.tokens} tokens, ${fmtUsd(d.cost_usd)}`}>
                      <span className="text-xs tabular-nums text-muted">{d.calls}</span>
                      <div className="w-full max-w-12 rounded-t bg-accent" style={{ height: `${(d.calls / peak) * 100}%` }} />
                      <span className="text-[10px] text-muted">{day.slice(5)}</span>
                    </div>
                  ))}
                </div>
              ) : <p className="text-sm text-muted">No calls in this period.</p>}
            </Card>
            <Card className="mt-6">
              <CardTitle>By model</CardTitle>
              <Table head={["Provider", "Model", "Calls", "Input tok", "Output tok", "Avg latency", "Errors", "Cost"]} empty="No calls in this period.">
                {u.by_model.map((m) => (
                  <tr key={`${m.provider}/${m.model}`}>
                    <Td>{m.provider}</Td><Td className="font-mono text-xs">{m.model}</Td><Td className="tabular-nums">{m.calls}</Td>
                    <Td className="tabular-nums">{m.input_tokens}</Td><Td className="tabular-nums">{m.output_tokens}</Td>
                    <Td className="tabular-nums">{fmtMs(m.avg_latency_ms)}</Td><Td className="tabular-nums">{m.errors}</Td><Td className="tabular-nums">{fmtUsd(m.cost_usd)}</Td>
                  </tr>
                ))}
              </Table>
            </Card>
          </>
        );
      })()}
    </>
  );
}
