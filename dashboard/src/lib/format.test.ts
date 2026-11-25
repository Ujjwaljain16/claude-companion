import { describe, expect, it } from "vitest";
import { ago, fmtMs, fmtPct, fmtUsd, mcpState, shortTarget, statusTone } from "./format";

describe("format helpers", () => {
  it("formats durations, money and percentages", () => {
    expect(fmtMs(null)).toBe("–");
    expect(fmtMs(412.4)).toBe("412 ms");
    expect(fmtMs(2345)).toBe("2.35 s");
    expect(fmtUsd(null)).toBe("unknown");
    expect(fmtUsd(0)).toBe("$0");
    expect(fmtUsd(0.00014)).toBe("$0.00014");
    expect(fmtUsd(1.5)).toBe("$1.50");
    expect(fmtPct(0.2)).toBe("20%");
  });

  it("maps execution status to tone", () => {
    expect(statusTone("succeeded")).toBe("ok");
    expect(statusTone("waiting")).toBe("warn");
    expect(statusTone("failed")).toBe("bad");
    expect(statusTone("cancelled")).toBe("idle");
  });

  it("describes relative time", () => {
    const now = new Date("2026-01-01T12:00:00Z");
    expect(ago("2026-01-01T11:59:30Z", now)).toBe("30s ago");
    expect(ago("2026-01-01T09:00:00Z", now)).toBe("3h ago");
  });

  it("derives MCP connection state from the last tool call", () => {
    expect(mcpState([]).label).toBe("Never seen");
    const base = { client: "claude-code", version: "2.1", tool_calls: 3, last_tool: "run_evaluation", last_seen: "", protocol_version: "" };
    expect(mcpState([{ ...base, seconds_since_last_call: 30 }]).tone).toBe("ok");
    expect(mcpState([{ ...base, seconds_since_last_call: 7200 }]).label).toBe("Idle");
  });

  it("labels session targets honestly", () => {
    expect(shortTarget({ provider: "session" })).toBe("Claude session");
    expect(shortTarget({ provider: "openai", model: "gpt-x" })).toBe("openai/gpt-x");
  });
});
