import { Badge, Card, GatewayError, PageHeader } from "@/components/ui";
import { gw } from "@/lib/gateway";

type Tool = { name: string; description: string; read_only: boolean; input_schema: { properties?: Record<string, { description?: string; type?: string }>; required?: string[] } };

export default async function ToolsPage() {
  const tools = await gw<Tool[]>("/api/mcp/tools");
  return (
    <>
      <PageHeader title="MCP tools" description="Everything Claude can call. Claude Code asks you before each call unless you allow a tool; the gateway additionally enforces its own permission settings and audits every change." />
      {!tools.ok ? <GatewayError error={tools.error} /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {tools.data.map((t) => (
            <Card key={t.name} className="p-4">
              <div className="flex items-center justify-between gap-2">
                <code className="font-mono text-sm font-semibold text-fg">{t.name}</code>
                <Badge tone={t.read_only ? "ok" : "warn"}>{t.read_only ? "read-only" : "changes state"}</Badge>
              </div>
              <p className="mt-2 text-sm text-muted">{t.description}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {Object.keys(t.input_schema.properties ?? {}).map((p) => (
                  <span key={p} className="rounded bg-fg/5 px-1.5 py-0.5 font-mono text-xs text-fg">
                    {p}{t.input_schema.required?.includes(p) ? "*" : ""}
                  </span>
                ))}
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
