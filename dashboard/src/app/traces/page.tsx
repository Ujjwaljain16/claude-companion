import { ExecutionsTable } from "@/components/executions";
import { Badge, Card, CardTitle, GatewayError, Json, PageHeader } from "@/components/ui";
import { fmtMs, statusTone } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { Execution } from "@/lib/types";

type Span = { span_id: string; parent_span_id: string | null; name: string; started_at: string; duration_ms: number; status: string; attributes: Record<string, unknown> };

function Waterfall({ spans }: { spans: Span[] }) {
  if (!spans.length) return <p className="text-sm text-muted">No spans recorded for this execution.</p>;
  const start = Math.min(...spans.map((s) => Date.parse(s.started_at)));
  const end = Math.max(...spans.map((s) => Date.parse(s.started_at) + s.duration_ms));
  const total = Math.max(end - start, 1);
  const depth = (s: Span): number => {
    const parent = spans.find((p) => p.span_id === s.parent_span_id);
    return parent ? 1 + depth(parent) : 0;
  };
  return (
    <div className="space-y-1">
      {spans.map((s) => {
        const left = ((Date.parse(s.started_at) - start) / total) * 100;
        const width = Math.max((s.duration_ms / total) * 100, 0.5);
        const tokens = [s.attributes["gen_ai.usage.input_tokens"], s.attributes["gen_ai.usage.output_tokens"]];
        return (
          <div key={s.span_id} className="grid grid-cols-[16rem_1fr_6rem] items-center gap-3 text-xs" title={JSON.stringify(s.attributes)}>
            <div className="truncate text-fg" style={{ paddingLeft: depth(s) * 12 }}>
              {s.name}
              {tokens[0] != null ? <span className="text-muted"> · {String(tokens[0])}/{String(tokens[1])} tok</span> : null}
            </div>
            <div className="relative h-4 rounded bg-fg/5">
              <div className={`absolute h-4 rounded ${s.status === "error" ? "bg-bad/70" : "bg-accent/70"}`} style={{ left: `${left}%`, width: `${width}%` }} />
            </div>
            <div className="text-right tabular-nums text-muted">{fmtMs(s.duration_ms)}</div>
          </div>
        );
      })}
    </div>
  );
}

export default async function TracesPage({ searchParams }: { searchParams: Promise<{ execution?: string }> }) {
  const id = (await searchParams).execution;
  if (id && /^[a-f0-9]{16}$/.test(id)) {
    const [ex, spans] = await Promise.all([gw<Execution>(`/api/executions/${id}`), gw<Span[]>(`/api/traces?execution_id=${id}`)]);
    if (!ex.ok) return (<><PageHeader title="Trace" /><GatewayError error={ex.error} /></>);
    return (
      <>
        <PageHeader title={`Trace · ${ex.data.kind} ${id}`} description={ex.data.error ?? ex.data.message} actions={<Badge tone={statusTone(ex.data.status)}>{ex.data.status}</Badge>} />
        <Card><CardTitle hint={spans.ok ? `${spans.data.length} spans` : undefined}>Spans</CardTitle>{spans.ok ? <Waterfall spans={spans.data} /> : <p className="text-sm text-bad">{spans.error}</p>}</Card>
        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <Card><CardTitle>Parameters</CardTitle><Json value={ex.data.params} /></Card>
          <Card><CardTitle>Result</CardTitle><Json value={ex.data.result} /></Card>
        </div>
      </>
    );
  }
  const execs = await gw<Execution[]>("/api/executions?limit=100");
  return (
    <>
      <PageHeader title="Traces" description="OpenTelemetry spans for every execution: agent steps, model calls, durations and token counts. Pick an execution." />
      {!execs.ok ? <GatewayError error={execs.error} /> : <Card><ExecutionsTable rows={execs.data} /></Card>}
    </>
  );
}
