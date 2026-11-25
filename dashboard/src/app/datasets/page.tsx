import Link from "next/link";
import { Card, GatewayError, PageHeader, Table, Td } from "@/components/ui";
import { ago } from "@/lib/format";
import { gw } from "@/lib/gateway";

type DatasetRow = { id: string; name: string; description: string; latest_version: number | null; versions: number; created_at: string };

export default async function DatasetsPage() {
  const res = await gw<DatasetRow[]>("/api/datasets");
  return (
    <>
      <PageHeader title="Datasets" description="Every change creates a new immutable version, so earlier runs stay reproducible." />
      {!res.ok ? <GatewayError error={res.error} /> : (
        <Card>
          <Table head={["Name", "Description", "Latest version", "Versions", "Created"]} empty="No datasets yet. Ask Claude to create_evaluation_dataset.">
            {res.data.map((d) => (
              <tr key={d.id}>
                <Td><Link className="font-medium text-accent hover:underline" href={`/datasets/${encodeURIComponent(d.name)}`}>{d.name}</Link></Td>
                <Td className="text-muted">{d.description || "–"}</Td>
                <Td className="tabular-nums">v{d.latest_version}</Td>
                <Td className="tabular-nums">{d.versions}</Td>
                <Td className="text-muted">{ago(d.created_at)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </>
  );
}
