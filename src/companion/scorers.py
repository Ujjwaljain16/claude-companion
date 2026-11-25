"""Deterministic scorers. Each takes the model output (a string), the dataset
item, and scorer params, and returns a score in [0, 1] with a reason.

Agent-trajectory scorers expect the output to be JSON like
{"tool_calls": [{"name": "search", "args": {...}, "ok": true}], "answer": "..."};
retrieval scorers expect {"retrieved_ids": [...]}. The expectations live on
the item: expected_tools, expected_trajectory, relevant_ids.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass
class Score:
    score: float
    passed: bool
    reason: str = ""

    def dict(self) -> dict[str, Any]:
        return asdict(self)


MAX_PATTERN = 200
MAX_REGEX_INPUT = 20_000


def _norm(s: Any) -> str:
    return " ".join(str(s).strip().lower().split())


def _expected_list(item: dict) -> list[str]:
    exp = item.get("expected")
    if exp is None:
        return []
    return [str(e) for e in exp] if isinstance(exp, list) else [str(exp)]


def _parse_json(output: str) -> Any:
    text = output.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    return json.loads(text)


def exact_match(output: str, item: dict, params: dict) -> Score:
    options = _expected_list(item)
    hit = any(_norm(output) == _norm(o) for o in options)
    return Score(float(hit), hit, "matched" if hit else f"expected one of {options[:3]}")


def contains(output: str, item: dict, params: dict) -> Score:
    needles = params.get("values") or _expected_list(item)
    if not needles:
        return Score(0.0, False, "no expected values")
    found = [n for n in needles if _norm(n) in _norm(output)]
    mode_all = params.get("mode", "any") == "all"
    score = len(found) / len(needles) if mode_all else float(bool(found))
    return Score(score, score == 1.0, f"found {len(found)}/{len(needles)}")


def regex(output: str, item: dict, params: dict) -> Score:
    pattern = params.get("pattern") or item.get("pattern")
    if not pattern:
        return Score(0.0, False, "no pattern")
    if len(pattern) > MAX_PATTERN:
        return Score(0.0, False, "pattern too long")
    hit = re.search(pattern, output[:MAX_REGEX_INPUT]) is not None
    return Score(float(hit), hit, "matched" if hit else "no match")


def json_valid(output: str, item: dict, params: dict) -> Score:
    try:
        _parse_json(output)
        return Score(1.0, True, "valid JSON")
    except (ValueError, TypeError) as e:
        return Score(0.0, False, f"invalid JSON: {e}")


def json_fields(output: str, item: dict, params: dict) -> Score:
    """Fraction of expected key/value pairs present in the output object."""
    expected = item.get("expected")
    if not isinstance(expected, dict) or not expected:
        return Score(0.0, False, "expected must be a non-empty object")
    try:
        got = _parse_json(output)
    except (ValueError, TypeError):
        return Score(0.0, False, "output is not JSON")
    if not isinstance(got, dict):
        return Score(0.0, False, "output is not an object")
    ok = [k for k, v in expected.items() if got.get(k) == v]
    score = len(ok) / len(expected)
    return Score(score, score == 1.0, f"{len(ok)}/{len(expected)} fields match")


def numeric_close(output: str, item: dict, params: dict) -> Score:
    tol = float(params.get("tolerance", 1e-6))
    m = re.search(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", output, re.I)
    try:
        expected = float(item.get("expected"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return Score(0.0, False, "expected is not a number")
    if not m:
        return Score(0.0, False, "no number in output")
    hit = abs(float(m.group()) - expected) <= tol
    return Score(float(hit), hit, f"got {m.group()}, expected {expected}")


def _tool_calls(output: str) -> list[dict]:
    data = _parse_json(output)
    calls = data.get("tool_calls", []) if isinstance(data, dict) else []
    return [c for c in calls if isinstance(c, dict) and "name" in c]


def tool_selection(output: str, item: dict, params: dict) -> Score:
    """F1 between the set of tools called and item['expected_tools']."""
    expected = set(item.get("expected_tools") or [])
    try:
        called = {c["name"] for c in _tool_calls(output)}
    except (ValueError, TypeError):
        return Score(0.0, False, "output is not a trajectory JSON")
    if not expected and not called:
        return Score(1.0, True, "no tools expected or called")
    tp = len(expected & called)
    precision = tp / len(called) if called else 0.0
    recall = tp / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    missing, extra = sorted(expected - called), sorted(called - expected)
    return Score(round(f1, 4), f1 == 1.0, f"missing={missing} extra={extra}")


def tool_success(output: str, item: dict, params: dict) -> Score:
    try:
        calls = _tool_calls(output)
    except (ValueError, TypeError):
        return Score(0.0, False, "output is not a trajectory JSON")
    if not calls:
        return Score(0.0, False, "no tool calls")
    ok = sum(1 for c in calls if c.get("ok", True) and not c.get("error"))
    return Score(ok / len(calls), ok == len(calls), f"{ok}/{len(calls)} calls succeeded")


def trajectory_match(output: str, item: dict, params: dict) -> Score:
    """Order-aware: longest common subsequence of tool names vs expected_trajectory."""
    expected = list(item.get("expected_trajectory") or [])
    try:
        got = [c["name"] for c in _tool_calls(output)]
    except (ValueError, TypeError):
        return Score(0.0, False, "output is not a trajectory JSON")
    if not expected:
        return Score(0.0, False, "no expected_trajectory")
    dp = [[0] * (len(got) + 1) for _ in range(len(expected) + 1)]
    for i, a in enumerate(expected):
        for j, b in enumerate(got):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a == b else max(dp[i][j + 1], dp[i + 1][j])
    lcs = dp[-1][-1]
    score = lcs / max(len(expected), len(got))
    return Score(round(score, 4), got == expected, f"got {got}")


def _retrieved(output: str) -> list[str]:
    data = _parse_json(output)
    return [str(x) for x in (data.get("retrieved_ids", []) if isinstance(data, dict) else data)]


def recall_at_k(output: str, item: dict, params: dict) -> Score:
    k = int(params.get("k", 5))
    relevant = {str(x) for x in item.get("relevant_ids") or []}
    try:
        got = _retrieved(output)[:k]
    except (ValueError, TypeError, AttributeError):
        return Score(0.0, False, "output is not retrieval JSON")
    if not relevant:
        return Score(0.0, False, "no relevant_ids")
    hits = len(relevant & set(got))
    score = hits / len(relevant)
    return Score(score, score == 1.0, f"{hits}/{len(relevant)} relevant in top {k}")


def mrr(output: str, item: dict, params: dict) -> Score:
    relevant = {str(x) for x in item.get("relevant_ids") or []}
    try:
        got = _retrieved(output)
    except (ValueError, TypeError, AttributeError):
        return Score(0.0, False, "output is not retrieval JSON")
    for rank, doc in enumerate(got, 1):
        if doc in relevant:
            return Score(1 / rank, rank == 1, f"first relevant at rank {rank}")
    return Score(0.0, False, "no relevant result")


SCORERS: dict[str, tuple[Callable[[str, dict, dict], Score], str]] = {
    "exact_match": (exact_match, "Normalized string equality with item.expected (string or list of options)."),
    "contains": (contains, "Output contains expected value(s); params: values, mode=any|all."),
    "regex": (regex, "Output matches params.pattern or item.pattern (max 200 chars)."),
    "json_valid": (json_valid, "Output parses as JSON."),
    "json_fields": (json_fields, "Fraction of item.expected key/values present in the JSON output."),
    "numeric_close": (numeric_close, "First number in output within params.tolerance of item.expected."),
    "tool_selection": (tool_selection, "F1 of tools called vs item.expected_tools (trajectory JSON output)."),
    "tool_success": (tool_success, "Fraction of tool calls with ok=true and no error."),
    "trajectory_match": (trajectory_match, "Order-aware match of tool sequence vs item.expected_trajectory."),
    "recall_at_k": (recall_at_k, "Recall of item.relevant_ids in output retrieved_ids top params.k."),
    "mrr": (mrr, "Reciprocal rank of the first relevant id."),
}


TRAJECTORY_SCORERS = {"tool_selection", "tool_success", "trajectory_match", "recall_at_k", "mrr"}


def run_scorer(name: str, output: str, item: dict, params: dict | None = None, trajectory: dict | None = None) -> Score:
    fn, _ = SCORERS[name]
    if trajectory is not None and name in TRAJECTORY_SCORERS:
        # Agent runs report their trajectory separately from the final answer.
        output = json.dumps(trajectory)
    try:
        return fn(output, item, params or {})
    except Exception as e:  # a broken item must not abort the run
        return Score(0.0, False, f"scorer error: {e}")
