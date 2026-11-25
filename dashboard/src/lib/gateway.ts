// Server-only gateway client. The token is read from disk on the server, so
// it never reaches the browser; mutations go through server actions.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { connection } from "next/server";

const HOME = process.env.COMPANION_HOME ?? path.join(homedir(), ".claude-companion");
export const GATEWAY_URL =
  process.env.COMPANION_GATEWAY_URL ?? `http://127.0.0.1:${process.env.COMPANION_PORT ?? "8765"}`;

export type GwResult<T> = { ok: true; data: T } | { ok: false; error: string; status?: number };

function token(): string | null {
  try {
    return readFileSync(path.join(HOME, "gateway.token"), "utf8").trim();
  } catch {
    return null;
  }
}

export async function gw<T>(p: string, init?: { method?: string; body?: unknown }): Promise<GwResult<T>> {
  await connection();
  const t = token();
  if (!t) return { ok: false, error: `No gateway token in ${HOME}. Start the gateway once to create it.` };
  try {
    const res = await fetch(`${GATEWAY_URL}${p}`, {
      method: init?.method ?? "GET",
      headers: {
        Authorization: `Bearer ${t}`,
        "X-Companion-Actor": "dashboard",
        ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: json.detail ?? res.statusText, status: res.status };
    return { ok: true, data: json as T };
  } catch (e) {
    return { ok: false, error: `Gateway not reachable at ${GATEWAY_URL} (${(e as Error).message})` };
  }
}
