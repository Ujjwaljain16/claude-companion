export type Tone = "ok" | "warn" | "bad" | "idle" | "info";

export function statusTone(status: string): Tone {
  switch (status) {
    case "succeeded":
      return "ok";
    case "running":
    case "queued":
      return "info";
    case "waiting":
      return "warn";
    case "failed":
      return "bad";
    default:
      return "idle";
  }
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "unknown";
  if (v === 0) return "$0";
  return v < 0.01 ? `$${v.toFixed(5)}` : `$${v.toFixed(2)}`;
}

export function fmtPct(v: number | null | undefined): string {
  return v == null ? "–" : `${Math.round(v * 1000) / 10}%`;
}

export function ago(iso: string, now: Date = new Date()): string {
  const s = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export type McpClient = { client: string; version: string; seconds_since_last_call: number; tool_calls: number; last_tool: string; last_seen: string; protocol_version: string };

// The gateway only learns about an MCP client when it calls a tool, so
// "connected" means a recent call, not an open socket.
export function mcpState(clients: McpClient[]): { tone: Tone; label: string; detail: string } {
  if (!clients.length) return { tone: "idle", label: "Never seen", detail: "No MCP client has called a tool yet." };
  const c = clients[0];
  if (c.seconds_since_last_call < 600)
    return { tone: "ok", label: "Active", detail: `${c.client} ${c.version} called ${c.last_tool}` };
  return { tone: "warn", label: "Idle", detail: `${c.client} ${c.version}, last call ${Math.round(c.seconds_since_last_call / 60)} min ago` };
}

export function shortTarget(t: { provider: string; model?: string | null }): string {
  return t.provider === "session" ? "Claude session" : `${t.provider}/${t.model ?? "?"}`;
}
