import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { GATEWAY_URL } from "@/lib/gateway";

const HOME = process.env.COMPANION_HOME ?? path.join(homedir(), ".claude-companion");
const TYPES: Record<string, string> = { html: "text/html; charset=utf-8", csv: "text/csv; charset=utf-8", json: "application/json" };

// Streams a run export from the gateway; the token stays on the server.
export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const format = new URL(request.url).searchParams.get("format") ?? "html";
  if (!/^[a-f0-9]{16}$/.test(id) || !(format in TYPES)) return new Response("bad request", { status: 400 });
  let token: string;
  try {
    token = readFileSync(path.join(HOME, "gateway.token"), "utf8").trim();
  } catch {
    return new Response("gateway token not found", { status: 503 });
  }
  const res = await fetch(`${GATEWAY_URL}/api/evaluations/${id}/export?format=${format}`, {
    headers: { Authorization: `Bearer ${token}`, "X-Companion-Actor": "dashboard" },
    cache: "no-store",
  });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  const disposition = format === "html" ? "inline" : `attachment; filename="run-${id}.${format}"`;
  return new Response(await res.text(), { headers: { "Content-Type": TYPES[format], "Content-Disposition": disposition } });
}
