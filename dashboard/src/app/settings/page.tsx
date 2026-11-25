import { Badge, Card, CardTitle, Code, GatewayError, PageHeader, Table, Td } from "@/components/ui";
import { ago } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { System } from "@/lib/types";

type Audit = { at: string; actor: string; action: string; target: string; outcome: string; details: Record<string, unknown> };

const PERMS: Record<string, [string, string]> = {
  allow_write: ["COMPANION_ALLOW_WRITE", "Create datasets and experiments"],
  allow_execute: ["COMPANION_ALLOW_EXECUTE", "Start, cancel and resume evaluations and workflows"],
  allow_paid_calls: ["COMPANION_ALLOW_PAID_CALLS", "Call paid provider APIs"],
  max_run_cost_usd: ["COMPANION_MAX_RUN_COST_USD", "Stop an evaluation's paid calls past this spend"],
  mcp_can_approve: ["COMPANION_MCP_CAN_APPROVE", "Let MCP clients submit human approvals (off: dashboard only)"],
};

export default async function SettingsPage() {
  const [sys, audit] = await Promise.all([gw<System>("/api/system"), gw<Audit[]>("/api/audit?limit=100")]);
  if (!sys.ok) return (<><PageHeader title="System settings" /><GatewayError error={sys.error} /></>);
  const g = sys.data.gateway;
  return (
    <>
      <PageHeader title="System settings" description={<>Settings come from environment variables or <Code>{g.home}\.env</Code>; restart the gateway after changing them.</>} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardTitle>Gateway</CardTitle>
          <dl className="grid grid-cols-[9rem_1fr] gap-y-2 text-sm">
            <dt className="text-muted">Version</dt><dd>{g.version} (Python {g.python})</dd>
            <dt className="text-muted">Bound to</dt><dd className="font-mono">{g.bind}</dd>
            <dt className="text-muted">Database</dt><dd>{g.database}</dd>
            <dt className="text-muted">Data folder</dt><dd className="break-all font-mono text-xs">{g.home}</dd>
          </dl>
        </Card>
        <Card>
          <CardTitle>Permissions</CardTitle>
          <Table head={["Setting", "Value", "Controls"]}>
            {Object.entries(PERMS).map(([k, [env, what]]) => (
              <tr key={k}>
                <Td><Code>{env}</Code></Td>
                <Td>{typeof g.permissions[k] === "boolean" ? <Badge tone={g.permissions[k] ? "ok" : "idle"}>{String(g.permissions[k])}</Badge> : `$${g.permissions[k]}`}</Td>
                <Td className="text-muted">{what}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
      <Card className="mt-6">
        <CardTitle>Audit log</CardTitle>
        {!audit.ok ? <p className="text-sm text-bad">{audit.error}</p> : (
          <Table head={["When", "Actor", "Action", "Target", "Details"]} empty="No audited actions yet.">
            {audit.data.map((a, i) => (
              <tr key={i}>
                <Td className="whitespace-nowrap text-muted">{ago(a.at)}</Td><Td>{a.actor}</Td><Td className="font-mono text-xs">{a.action}</Td>
                <Td className="font-mono text-xs">{a.target}</Td><Td className="max-w-md truncate text-xs text-muted">{JSON.stringify(a.details)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
