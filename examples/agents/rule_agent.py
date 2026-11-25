"""A deterministic agent with no model, for trying the runner offline and in
tests. It answers "What is the capital of X?" with a lookup tool, retries
once after a flaky tool error, and has a deliberately buggy variant
(config {"buggy": true}) to show regression detection."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
import companion_agent as ca  # noqa: E402

CAPITALS = {"france": "Paris", "japan": "Tokyo", "kenya": "Nairobi", "canada": "Ottawa", "australia": "Canberra"}


def solve(task: dict, traj: ca.Trajectory) -> str:
    question = str(task["input"]).lower()
    cfg = task["config"]
    if "crash" in question:
        raise ValueError("cannot parse question")
    if "hang" in question:
        # Leaves its PID next to the result file so tests can check it gets killed.
        Path(os.environ["COMPANION_RESULT_FILE"]).with_name("pid").write_text(str(os.getpid()))
        time.sleep(60)
    country = next((c for c in CAPITALS if c in question), None)
    traj.step("parse question")
    if country is None:
        traj.tool("lookup_capital", {"country": question[:40]}, ok=False, error="unknown country")
        return "I don't know."
    if country == "kenya":  # the lookup service is flaky for this one; the agent retries
        traj.tool("lookup_capital", {"country": country}, ok=False, error="503 from lookup service")
        traj.step("retry")
    answer = CAPITALS[country]
    if cfg.get("buggy") and country in ("canada", "australia"):
        answer = {"canada": "Toronto", "australia": "Sydney"}[country]
    traj.tool("lookup_capital", {"country": country}, ok=True, result=answer)
    traj.step("answer")
    return answer


ca.run(solve)
