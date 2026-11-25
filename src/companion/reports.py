"""Run and comparison exports: JSON, CSV and a self-contained HTML report."""

from __future__ import annotations

import csv
import io
import json
from html import escape
from typing import Any


def _target_label(t: dict) -> str:
    if t.get("provider") == "agent":
        return f"agent {t.get('agent')} / {t.get('variant', 'default')}"
    if t.get("provider") == "session":
        return "Claude session"
    return f"{t.get('provider')}/{t.get('model')}"


def to_csv(report: dict) -> str:
    items = report.get("items", [])
    scorer_names = sorted({k for it in items for k in (it.get("scores") or {})})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["item_id", "status", "latency_ms", "steps", "tool_calls", "tool_errors", "model_calls", "error", "output",
                *[f"{s}_score" for s in scorer_names], *[f"{s}_passed" for s in scorer_names]])
    for it in items:
        traj = it.get("trajectory") or {}
        calls = traj.get("tool_calls", [])
        scores = it.get("scores") or {}
        w.writerow([it["item_id"], it["status"], it.get("latency_ms"), traj.get("steps"), len(calls),
                    sum(not c.get("ok", True) for c in calls), (it.get("agent_meta") or {}).get("model_calls"),
                    it.get("error") or "", (it.get("output") or "")[:2000],
                    *[scores.get(s, {}).get("score") for s in scorer_names],
                    *[scores.get(s, {}).get("passed") for s in scorer_names]])
    return buf.getvalue()


CSS = """
:root{--bg:#f7f7f8;--card:#fff;--fg:#18181b;--muted:#6b6b76;--line:#e4e4e9;--accent:#4f46e5;--ok:#15803d;--bad:#c2410c;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0f0f12;--card:#17171b;--fg:#ececf1;--muted:#9a9aa6;--line:#2a2a31;--accent:#818cf8;--ok:#4ade80;--bad:#fb923c;--warn:#fbbf24}}
body{font:14px/1.5 system-ui,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:32px;max-width:1200px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:0 0 12px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
.stat .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}.stat .l{font-size:11px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;color:var(--muted)}td.num{font-variant-numeric:tabular-nums}
.chip{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;margin:1px;border:1px solid var(--line)}
.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,transparent)}.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,transparent)}
pre{white-space:pre-wrap;background:color-mix(in srgb,var(--fg) 5%,transparent);padding:8px;border-radius:6px;font-size:12px;max-height:240px;overflow:auto}
details summary{cursor:pointer}.arrow{color:var(--muted);margin:0 2px}
"""


def _bar_chart(scores: dict[str, dict]) -> str:
    if not scores:
        return "<p class='muted'>No scores.</p>"
    rows, h = [], 26
    for i, (name, v) in enumerate(scores.items()):
        y = i * h
        rows.append(
            f"<text x='0' y='{y + 17}' fill='currentColor' font-size='12'>{escape(name)}</text>"
            f"<rect x='150' y='{y + 5}' width='400' height='14' rx='3' fill='var(--line)'/>"
            f"<rect x='150' y='{y + 5}' width='{400 * v['mean']:.1f}' height='14' rx='3' fill='var(--accent)'/>"
            f"<text x='560' y='{y + 17}' fill='currentColor' font-size='12'>{v['mean']:.3f} · pass {v['pass_rate'] * 100:.0f}%</text>")
    return f"<svg viewBox='0 0 700 {len(scores) * h}' width='100%' role='img' aria-label='Mean score per scorer'>{''.join(rows)}</svg>"


def _trajectory(traj: dict | None, meta: dict | None) -> str:
    if not traj:
        return ""
    chips = []
    for c in traj.get("tool_calls", [])[:60]:
        cls = "ok" if c.get("ok", True) else "bad"
        title = escape(json.dumps(c.get("args", {}))[:300] + (f" | error: {c.get('error')}" if c.get("error") else ""))
        chips.append(f"<span class='chip {cls}' title='{title}'>{escape(c['name'])}</span>")
    seq = "<span class='arrow'>→</span>".join(chips) or "<span class='muted'>no tool calls</span>"
    extra = ""
    if meta and meta.get("stderr_tail"):
        extra = f"<details><summary class='muted'>stderr</summary><pre>{escape(meta['stderr_tail'][-2000:])}</pre></details>"
    return f"<div>{seq}</div><div class='muted'>{traj.get('steps', 0)} steps · {(meta or {}).get('model_calls', 0)} model calls</div>{extra}"


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "–"
    return f"{v * 100:.1f}%" if pct else str(v)


def to_html(report: dict) -> str:
    s = report["summary"]
    agent = s.get("agent") or {}
    lat = s.get("latency_ms") or {}
    stats = [("Items", s["items"]), ("Errors", s["errors"]), ("Latency p50", f"{lat.get('p50', '–')} ms"),
             ("Latency p95", f"{lat.get('p95', '–')} ms"), ("Tokens", s["tokens"]["input"] + s["tokens"]["output"]),
             ("Cost", "–" if s.get("cost_usd") is None else f"${s['cost_usd']}"),
             ("Throughput", f"{s.get('throughput_items_per_min') or '–'} items/min")]
    if agent:
        stats = [("Success rate", _fmt(agent["success_rate"], True)), ("Completion", _fmt(agent["completion_rate"], True)),
                 ("Partial score", agent["partial_score"]), ("Avg steps", agent["avg_steps"]),
                 ("Avg tool calls", agent["avg_tool_calls"]), ("Tool success", _fmt(agent["tool_success_rate"], True)),
                 ("Failure recovery", _fmt(agent["failure_recovery_rate"], True)), ("Model calls", agent["model_calls"]), *stats]
    stat_html = "".join(f"<div class='card stat'><div class='l'>{escape(k)}</div><div class='v'>{escape(str(v))}</div></div>" for k, v in stats)
    repro = (report["target"].get("reproducibility") or {})
    rows = []
    for it in report.get("items", []):
        scores = "".join(f"<span class='chip {'ok' if v['passed'] else 'bad'}' title='{escape(v.get('reason', ''))}'>"
                         f"{escape(k)} {v['score']:.2f}</span>" for k, v in (it.get("scores") or {}).items())
        out = escape(it.get("error") or it.get("output") or "")
        rows.append(f"<tr><td><code>{escape(it['item_id'])}</code></td><td>{escape(it['status'])}</td>"
                    f"<td><pre>{out[:1500]}</pre></td><td class='num'>{_fmt(it.get('latency_ms'))}</td>"
                    f"<td>{scores}</td><td>{_trajectory(it.get('trajectory'), it.get('agent_meta'))}</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Run {escape(report['run_id'])}</title>
<style>{CSS}</style></head><body>
<h1>Run {escape(report['run_id'])}</h1>
<div class="muted">{escape(_target_label(report['target']))} · status {escape(report['status'])} · {escape(report['created_at'])}</div>
<div class="grid">{stat_html}</div>
<div class="card"><h2>Scores</h2>{_bar_chart(s.get('scores', {}))}</div>
{f"<div class='card'><h2>Reproducibility</h2><pre>{escape(json.dumps(repro, indent=2))}</pre></div>" if repro else ""}
<div class="card"><h2>Items</h2><table><thead><tr><th>Item</th><th>Status</th><th>Output</th><th>Latency</th><th>Scores</th><th>Trajectory</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
</body></html>"""


def comparison_html(cmp: dict, labels: dict[str, str]) -> str:
    blocks = []
    for c in cmp["comparisons"]:
        rows = "".join(
            f"<tr><td>{escape(k)}</td><td class='num'>{v['baseline_mean']:.3f}</td><td class='num'>{v['candidate_mean']:.3f}</td>"
            f"<td class='num {'bad' if v['regressed'] else 'ok'}'>{v['delta']:+.3f}</td><td class='num'>{v['wins']}/{v['losses']}/{v['ties']}</td>"
            f"<td>{escape(', '.join(v['newly_failing_items'][:10]))}</td></tr>" for k, v in c["scorers"].items())
        verdict = "<span class='chip bad'>regression</span>" if c["regression"] else "<span class='chip ok'>no regression</span>"
        blocks.append(f"<div class='card'><h2>{escape(labels.get(c['candidate_run_id'], c['candidate_run_id']))} {verdict}</h2>"
                      f"<table><thead><tr><th>Scorer</th><th>Baseline</th><th>Candidate</th><th>Δ</th><th>W/L/T</th><th>Newly failing</th></tr></thead>"
                      f"<tbody>{rows}</tbody></table>"
                      f"<p class='muted'>Latency p50 Δ {_fmt(c.get('latency_p50_delta_ms'))} ms · cost Δ {_fmt(c.get('cost_delta_usd'))}</p></div>")
    base = labels.get(cmp["baseline_run_id"], cmp["baseline_run_id"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Comparison</title><style>{CSS}</style></head><body>
<h1>Comparison against {escape(base)}</h1><div class="muted">Regression threshold {cmp['regression_threshold']}</div>{''.join(blocks)}</body></html>"""
