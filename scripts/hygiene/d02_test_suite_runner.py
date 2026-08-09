#!/usr/bin/env python3
"""d02_test_suite_runner.py — the 23 unit tests are re-run by the gate.

Runs `pytest test_router.py -q` at the repo root (the tests live at the root,
not tests/) and records the pass/fail with the pytest evidence line.

Artifact: <repo>/artifacts/hygiene/d02_test_suite_<ts>.json
Exit code: 0 = pass, 1 = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "test_router.py", "-q"],
            cwd=str(REPO), capture_output=True, text=True,
            timeout=600, check=False,
        )
    except subprocess.TimeoutExpired:
        proc = type("P", (), {"returncode": 124, "stdout": "", "stderr": "timed out after 600s"})()
    latency_ms = int((time.perf_counter() - started) * 1000)

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    passed = proc.returncode == 0
    # Evidence line: the pytest "N passed in Xs" tail.
    evidence = [line.strip() for line in combined.splitlines() if re.search(r"\d+\s+passed", line)]
    summary = evidence[-1] if evidence else f"pytest exit {proc.returncode}"

    verdict = "pass" if passed else "fail"
    out = EVIDENCE_DIR / f"d02_test_suite_{_now()}.json"

    artifact = {
        "experiment_id": "d02_test_suite",
        "artifact": str(out),
        "skill": "regression-hygiene",
        "input": "pytest test_router.py -q at repo root",
        "environment": f"domain-router repo @ {REPO}",
        "failure_injected": "none \u2014 full unit suite is the regression probe",
        "expected_behavior": "all unit tests pass (pytest exit 0)",
        "actual_behavior": f"exit={proc.returncode} {summary}",
        "latency_ms": latency_ms,
        "errors": [] if passed else [(proc.stderr or "")[-300:]],
        "state_before": {"suite": "test_router.py (registry parse, classify, dispatch, audit, CLI)"},
        "state_after": {"pytest_exit": proc.returncode, "passed": passed, "summary": summary},
        "recovery": "n/a \u2014 read-only verification",
        "false_repair": False,
        "evidence": evidence or [f"pytest exit {proc.returncode}"],
        "verdict": verdict,
    }

    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
