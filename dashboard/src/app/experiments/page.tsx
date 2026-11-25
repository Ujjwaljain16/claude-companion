import Link from "next/link";
import { Badge, Card, CardTitle, GatewayError, PageHeader } from "@/components/ui";
import { ago, shortTarget, statusTone } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { RunSummary } from "@/lib/types";

type Exp = { id: string; name: string; description: string; tags: string[]; created_at: string; runs: { run_id: string; status: string; target: { provider: string; model?: string | null }; summary: RunSummary | null; created_at: string }[] };

export default async function ExperimentsPage({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const q = (await searchParams).q ?? "";
  const res = await gw<Exp[]>(`/api/experiments?limit=100&q=${encodeURIComponent(q)}`);
  return (
    <>
      <PageHeader title="Experiments" description="Groups of related evaluation runs, e.g. one experiment per prompt change." />
      <form className="mb-4"><input name="q" defaultValue={q} placeholder="Search experiments" className="w-80 rounded-md border border-line bg-card px-3 py-1.5 text-sm text-fg" /></form>
      {!res.ok ? <GatewayError error={res.error} /> : res.data.length === 0 ? <p className="text-sm text-muted">No experiments{q ? " match" : " yet"}.</p> : (
        <div className="space-y-4">
          {res.data.map((e) => (
            <Card key={e.id}>
              <CardTitle hint={ago(e.created_at)}>{e.name} {e.tags.map((t) => <Badge key={t}>{t}</Badge>)}</CardTitle>
              {e.description ? <p className="mb-3 text-sm text-muted">{e.description}</p> : null}
              <ul className="space-y-1 text-sm">
                {e.runs.map((r) => (
                  <li key={r.run_id} className="flex flex-wrap items-center gap-2">
                    <Link className="font-mono text-xs text-accent hover:underline" href={`/evaluations/${r.run_id}`}>{r.run_id}</Link>
                    <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                    <span>{shortTarget(r.target)}</span>
                    <span className="text-xs text-muted">{r.summary ? Object.entries(r.summary.scores).map(([k, v]) => `${k} ${v.mean.toFixed(2)}`).join(" · ") : ""}</span>
                  </li>
                ))}
                {e.runs.length === 0 ? <li className="text-muted">No runs yet.</li> : null}
              </ul>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
