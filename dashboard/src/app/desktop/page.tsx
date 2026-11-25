import { Badge, Card, CardTitle, Code, GatewayError, PageHeader, Table, Td } from "@/components/ui";
import { ago, mcpState } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { System } from "@/lib/types";

const SNIPPET = `{
  "mcpServers": {
    "companion": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--frozen", "--quiet", "companion-mcp"]
    }
  }
}`;

export default async function DesktopPage() {
  const sys = await gw<System>("/api/system");
  if (!sys.ok) return (<><PageHeader title="Claude Desktop connection" /><GatewayError error={sys.error} /></>);
  const { desktop, mcp_clients } = sys.data;
  const state = mcpState(mcp_clients);
  return (
    <>
      <PageHeader
        title="Claude Desktop connection"
        description="Claude Desktop stays the model client. Its Code tab starts this project's MCP server from .mcp.json inside your existing signed-in session; no extra login, CLI or API key is involved."
      />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardTitle>Installation</CardTitle>
          <Badge tone={desktop.installed ? "ok" : "warn"}>{desktop.installed ? "Installed" : "Not detected"}</Badge>
          <p className="mt-3 text-xs text-muted">Checked by install location only ({desktop.platform}). The gateway never reads the app&apos;s files or credentials.</p>
          <ul className="mt-2 space-y-1">{desktop.locations.map((l) => <li key={l} className="break-all font-mono text-xs text-muted">{l}</li>)}</ul>
        </Card>
        <Card>
          <CardTitle>MCP connection</CardTitle>
          <Badge tone={state.tone}>{state.label}</Badge>
          <p className="mt-3 text-sm text-muted">{state.detail}</p>
          <p className="mt-2 text-xs text-muted">Based on the last tool call the gateway received, not an open socket.</p>
        </Card>
        <Card className="border-warn/40">
          <CardTitle>What this connection is not</CardTitle>
          <p className="text-sm text-muted">
            MCP lets Claude call these tools. It does not let the gateway call Claude: the Code tab does not support MCP sampling, so
            gateway jobs cannot borrow the Desktop subscription for inference. Use target <Code>session</Code> to have Claude answer
            in the chat, or configure a provider key.
          </p>
        </Card>
      </div>

      <Card className="mt-6">
        <CardTitle>Clients seen</CardTitle>
        <Table head={["Client", "Version", "Protocol", "Tool calls", "Last tool", "Last seen"]} empty="No client yet. Open a Code tab session in this project and approve the 'companion' server.">
          {mcp_clients.map((c) => (
            <tr key={`${c.client}${c.version}`}>
              <Td>{c.client}</Td><Td>{c.version}</Td><Td>{c.protocol_version || "–"}</Td>
              <Td className="tabular-nums">{c.tool_calls}</Td><Td>{c.last_tool}</Td><Td className="text-muted">{ago(c.last_seen)}</Td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card className="mt-6">
        <CardTitle>Setup</CardTitle>
        <ol className="list-decimal space-y-2 pl-5 text-sm text-fg">
          <li>In Claude Desktop, open the Code tab and start a session in the <Code>claude-companion</Code> folder.</li>
          <li>Claude Code reads the project&apos;s <Code>.mcp.json</Code> and asks once whether to allow the <Code>companion</Code> server. Approve it.</li>
          <li>Ask Claude to run <Code>inspect_system_health</Code>. The gateway starts automatically if it is not already running.</li>
        </ol>
        <pre className="mt-4 rounded-md bg-fg/5 p-3 font-mono text-xs text-fg">{SNIPPET}</pre>
      </Card>
    </>
  );
}
