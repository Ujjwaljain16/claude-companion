import { Badge, Card, Code, GatewayError, LinkButton, PageHeader, Table, Td } from "@/components/ui";
import { fmtMs } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { Provider } from "@/lib/types";

const ENV: Record<string, string> = {
  anthropic: "COMPANION_ANTHROPIC_API_KEY",
  openai: "COMPANION_OPENAI_API_KEY",
  gemini: "COMPANION_GEMINI_API_KEY",
  openrouter: "COMPANION_OPENROUTER_API_KEY",
  ollama: "COMPANION_OLLAMA_URL",
  custom: "COMPANION_CUSTOM_BASE_URL",
};

export default async function ProvidersPage({ searchParams }: { searchParams: Promise<{ probe?: string }> }) {
  const probe = (await searchParams).probe === "1";
  const res = await gw<Provider[]>(`/api/providers?probe=${probe}`);
  return (
    <>
      <PageHeader
        title="Providers"
        description="Independent model APIs the gateway calls with its own keys. They are optional; none of them is your Claude Desktop login."
        actions={<LinkButton href={probe ? "/providers" : "/providers?probe=1"}>{probe ? "Hide connectivity" : "Test connectivity"}</LinkButton>}
      />
      {!res.ok ? <GatewayError error={res.error} /> : (
        <Card>
          <Table head={["Provider", "Configured", "Billing", "Connectivity", "Endpoint", "Configure with"]}>
            {res.data.map((p) => (
              <tr key={p.name}>
                <Td><div className="font-medium">{p.label}</div><div className="font-mono text-xs text-muted">{p.name}</div></Td>
                <Td><Badge tone={p.configured ? "ok" : "idle"}>{p.configured ? "yes" : "no"}</Badge></Td>
                <Td className="text-muted">{p.paid ? "paid API" : "local / free"}</Td>
                <Td>
                  {p.probe ? (
                    p.probe.reachable
                      ? <Badge tone="ok">reachable · {fmtMs(p.probe.latency_ms)} · {p.probe.model_count} models</Badge>
                      : <span className="text-xs text-bad">{p.probe.error}</span>
                  ) : <span className="text-xs text-muted">not tested</span>}
                </Td>
                <Td className="font-mono text-xs text-muted">{p.base_url}</Td>
                <Td><Code>{ENV[p.name] ?? "-"}</Code></Td>
              </tr>
            ))}
          </Table>
          <p className="mt-4 text-xs text-muted">
            Keys go in <Code>~/.claude-companion/.env</Code>. They use a <Code>COMPANION_</Code> prefix on purpose: an
            <Code>ANTHROPIC_API_KEY</Code> in your environment would make Claude Code bill the API instead of your subscription.
          </p>
        </Card>
      )}
    </>
  );
}
