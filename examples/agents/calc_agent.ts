// A TypeScript agent for the runner (no model): answers "What is A op B?" with a calculator tool.
// Runs on Node 22+: node --experimental-strip-types examples/agents/calc_agent.ts
import { run } from "../../sdk/typescript/companionAgent.ts";

await run((task, traj) => {
  const m = String(task.input).match(/(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)/);
  traj.step("parse");
  if (!m) {
    traj.tool("calculate", { expression: String(task.input) }, { error: "no arithmetic found" });
    return "I can only do arithmetic.";
  }
  const [a, op, b] = [Number(m[1]), m[2], Number(m[3])];
  const value = op === "+" ? a + b : op === "-" ? a - b : op === "*" ? a * b : a / b;
  traj.tool("calculate", { expression: `${a} ${op} ${b}` }, { result: value });
  traj.step("answer");
  return String(value);
});
