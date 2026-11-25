// Helper for TypeScript/JavaScript agents run by the claude-companion agent runner.
// Dependency-free; same file contract as sdk/python/companion_agent.py.
//
//   import { run, openaiSettings } from "./companionAgent.ts";
//   await run(async (task, traj) => { traj.tool("search", { q: "x" }); return "answer"; });
import { readFileSync, writeFileSync } from "node:fs";

export type Task = { item_id: string; input: unknown; metadata: Record<string, unknown>; config: Record<string, unknown>; run_id: string };
type ToolCall = { name: string; args: Record<string, unknown>; ok: boolean; error?: string; result?: unknown };

export class Trajectory {
  toolCalls: ToolCall[] = [];
  steps = 0;
  events: Record<string, unknown>[] = [];
  usage: { input_tokens?: number; output_tokens?: number; model_calls?: number; cost_usd?: number } = {};
  private t0 = performance.now();

  private ms() {
    return Math.round((performance.now() - this.t0) * 10) / 10;
  }
  step(note = "") {
    this.steps += 1;
    this.events.push({ type: "step", n: this.steps, note: note.slice(0, 500), t_ms: this.ms() });
  }
  tool(name: string, args: Record<string, unknown> = {}, opts: { ok?: boolean; error?: string; result?: unknown } = {}) {
    const call: ToolCall = { name, args, ok: (opts.ok ?? true) && !opts.error };
    if (opts.error) call.error = String(opts.error).slice(0, 1000);
    if (opts.result !== undefined) call.result = opts.result;
    this.toolCalls.push(call);
    this.events.push({ type: "tool_call", name, ok: call.ok, t_ms: this.ms() });
  }
}

export function task(): Task {
  const path = process.env.COMPANION_TASK_FILE;
  if (!path) throw new Error("COMPANION_TASK_FILE is not set; run this agent through the companion agent runner");
  return JSON.parse(readFileSync(path, "utf8"));
}

function headers(agentName?: string): Record<string, string> {
  return { "X-Companion-Tag": process.env.COMPANION_TAG ?? "", ...(agentName ? { "X-Agent-Name": agentName } : {}) };
}

/** Options for `new OpenAI(...)` pointed at the gateway. */
export function openaiSettings(agentName?: string) {
  return { baseURL: process.env.COMPANION_OPENAI_BASE_URL!, apiKey: process.env.COMPANION_GATEWAY_TOKEN!, defaultHeaders: headers(agentName), timeout: 900_000, maxRetries: 0 };
}

/** Options for `new Anthropic(...)` pointed at the gateway. */
export function anthropicSettings(agentName?: string) {
  return { baseURL: process.env.COMPANION_ANTHROPIC_BASE_URL!, apiKey: process.env.COMPANION_GATEWAY_TOKEN!, defaultHeaders: headers(agentName), timeout: 900_000, maxRetries: 0 };
}

export function finish(output: unknown, traj = new Trajectory(), error: string | null = null, metadata: Record<string, unknown> = {}) {
  const result = {
    output: typeof output === "string" || output == null ? output : JSON.stringify(output),
    error,
    steps: traj.steps,
    tool_calls: traj.toolCalls,
    events: traj.events.slice(-500),
    usage: traj.usage,
    metadata,
  };
  writeFileSync(process.env.COMPANION_RESULT_FILE!, JSON.stringify(result), "utf8");
}

/** Calls solve(task, trajectory) and always writes a result, even if it throws. */
export async function run(solve: (task: Task, traj: Trajectory) => Promise<unknown> | unknown) {
  const traj = new Trajectory();
  try {
    finish(await solve(task(), traj), traj);
  } catch (e) {
    const err = e as Error;
    finish(null, traj, `${err.name}: ${err.message}`, { stack: String(err.stack).slice(-4000) });
    process.exitCode = 1;
  }
}
