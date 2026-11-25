import Link from "next/link";
import { Card, CardTitle, GatewayError, PageHeader, Table, Td } from "@/components/ui";
import { ago } from "@/lib/format";
import { gw } from "@/lib/gateway";

type Dataset = {
  name: string;
  description: string;
  version: number;
  items: { id: string; input: unknown; expected?: unknown; metadata?: unknown }[];
  versions: { version: number; item_count: number; note: string; created_at: string }[];
};

const show = (v: unknown) => (v == null ? "–" : typeof v === "string" ? v : JSON.stringify(v));

export default async function DatasetPage({ params, searchParams }: { params: Promise<{ name: string }>; searchParams: Promise<{ v?: string }> }) {
  const { name } = await params;
  const v = (await searchParams).v;
  const res = await gw<Dataset>(`/api/datasets/${encodeURIComponent(name)}${v && /^\d+$/.test(v) ? `?version=${v}` : ""}`);
  if (!res.ok) return (<><PageHeader title={decodeURIComponent(name)} /><GatewayError error={res.error} /></>);
  const d = res.data;
  return (
    <>
      <PageHeader title={`${d.name} · v${d.version}`} description={d.description} />
      <div className="grid gap-6 xl:grid-cols-[1fr_18rem]">
        <Card>
          <CardTitle hint={`${d.items.length} items`}>Items</CardTitle>
          <Table head={["Id", "Input", "Expected"]}>
            {d.items.map((it) => (
              <tr key={it.id}>
                <Td className="font-mono text-xs">{it.id}</Td>
                <Td className="max-w-lg whitespace-pre-wrap text-xs">{show(it.input)}</Td>
                <Td className="max-w-xs text-xs text-muted">{show(it.expected)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card>
          <CardTitle>Versions</CardTitle>
          <ul className="space-y-2 text-sm">
            {d.versions.map((ver) => (
              <li key={ver.version}>
                <Link href={`?v=${ver.version}`} className={ver.version === d.version ? "font-semibold text-accent" : "text-fg hover:underline"}>v{ver.version}</Link>
                <span className="text-muted"> · {ver.item_count} items · {ago(ver.created_at)}</span>
                {ver.note ? <div className="text-xs text-muted">{ver.note}</div> : null}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
