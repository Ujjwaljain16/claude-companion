import { Badge, Card, CardTitle, Code, GatewayError, PageHeader } from "@/components/ui";
import { gw } from "@/lib/gateway";

type Models = { session: { note: string }; providers: { provider: string; reachable: boolean; models: string[]; error?: string }[] };

export default async function ModelsPage() {
  const res = await gw<Models>("/api/models");
  return (
    <>
      <PageHeader title="Models" description="Live model lists from each configured provider." />
      {!res.ok ? <GatewayError error={res.error} /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardTitle>Claude session <Badge tone="info">no key needed</Badge></CardTitle>
            <p className="text-sm text-muted">{res.data.session.note} Use provider <Code>session</Code>.</p>
          </Card>
          {res.data.providers.map((p) => (
            <Card key={p.provider}>
              <CardTitle hint={p.reachable ? `${p.models.length} models` : undefined}>{p.provider}</CardTitle>
              {p.reachable ? (
                <div className="flex max-h-64 flex-wrap gap-1.5 overflow-auto">
                  {p.models.map((m) => <span key={m} className="rounded bg-fg/5 px-1.5 py-0.5 font-mono text-xs text-fg">{m}</span>)}
                </div>
              ) : <p className="text-sm text-bad">{p.error}</p>}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
