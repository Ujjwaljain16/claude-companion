import json

import pytest

from companion.scorers import run_scorer

traj = json.dumps({"tool_calls": [{"name": "search", "ok": True}, {"name": "fetch", "ok": False, "error": "404"}]})


@pytest.mark.parametrize("name,output,item,params,score", [
    ("exact_match", "  Paris ", {"expected": "paris"}, {}, 1.0),
    ("exact_match", "Lyon", {"expected": ["paris", "Paris, France"]}, {}, 0.0),
    ("contains", "The capital is Paris.", {"expected": "paris"}, {}, 1.0),
    ("contains", "a b", {}, {"values": ["a", "c"], "mode": "all"}, 0.5),
    ("regex", "order #1234", {}, {"pattern": r"#\d{4}"}, 1.0),
    ("regex", "x", {}, {"pattern": "a" * 300}, 0.0),
    ("json_valid", '```json\n{"a": 1}\n```', {}, {}, 1.0),
    ("json_valid", "{nope", {}, {}, 0.0),
    ("json_fields", '{"a": 1, "b": 2}', {"expected": {"a": 1, "b": 3}}, {}, 0.5),
    ("numeric_close", "about 3.1416", {"expected": 3.14159}, {"tolerance": 0.001}, 1.0),
    ("tool_selection", traj, {"expected_tools": ["search", "fetch"]}, {}, 1.0),
    ("tool_selection", traj, {"expected_tools": ["search"]}, {}, 0.6667),
    ("tool_success", traj, {}, {}, 0.5),
    ("trajectory_match", traj, {"expected_trajectory": ["search", "fetch"]}, {}, 1.0),
    ("trajectory_match", traj, {"expected_trajectory": ["fetch", "search"]}, {}, 0.5),
    ("recall_at_k", '{"retrieved_ids": ["d1", "d7", "d3"]}', {"relevant_ids": ["d3", "d9"]}, {"k": 3}, 0.5),
    ("mrr", '{"retrieved_ids": ["d1", "d7", "d3"]}', {"relevant_ids": ["d3"]}, {}, 1 / 3),
])
def test_scorers(name, output, item, params, score):
    assert run_scorer(name, output, item, params).score == pytest.approx(score, abs=1e-3)


def test_scorer_never_raises_on_bad_item():
    assert run_scorer("numeric_close", "5", {"expected": "five"}).passed is False
    assert run_scorer("tool_selection", "not json", {"expected_tools": ["x"]}).score == 0.0
