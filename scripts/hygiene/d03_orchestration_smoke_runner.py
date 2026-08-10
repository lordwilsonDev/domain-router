#!/usr/bin/env python3
"""d03_orchestration_smoke_runner.py — the folded front-end's own smoke suite.

The domain-router was folded into skill-orchestration-os as its routing
front-end; the OS's smoke suite (scripts/smoke_test.py, 25 tests) proves the
whole OS still works — registry, planner (incl. route-aware prompt + wrapped-
steps normalization + replanning), executor (incl. the `route` DAG step +
unknown-domain rejection + per-step verification), audit, meta-learner, the
`skill-os route --dry-run` CLI, the reality feedback loop (09 -> 02 -> 04)
with its TASK_FAILED event contract, the dispatch-failure feedback link, and
the wired replay-consumer gate leg (malformed stream -> non-zero, absent ->
exit 0, REGRESSED flip).

Zero-spend by design: the smoke suite uses --domain + dry-run only, and the
subprocess env is scrubbed of the canonical paid-API credential set (kept in
sync with engineering-hygiene-factory/scripts/run_factory.py ZERO_SPEND_ENV_VARS)
so a leaked key can never turn the gate into a paid API call.

Artifact: <repo>/artifacts/hygiene/d03_orchestration_smoke_<ts>.json
Exit code: 0 = pass, 1 = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ORCH = Path(os.environ.get("ORCH_OS", str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os")))
PY = sys.executable
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

SMOKE = ORCH / "scripts" / "smoke_test.py"

# Keys that would turn the "zero-spend" smoke suite into a paid call. The
# suite uses --domain + dry-run only, but test_deepseek_plan makes a real
# DeepSeek request whenever DEEPSEEK_API_KEY is present in the environment —
# so strip the full canonical paid-API set from the subprocess. MUST stay in
# lockstep with engineering-hygiene-factory run_factory.py ZERO_SPEND_ENV_VARS.
ZERO_SPEND_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "TAVILY_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
)


def _zero_spend_env() -> dict:
    env = os.environ.copy()
    for k in ZERO_SPEND_ENV_VARS:
        env.pop(k, None)
    return env


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    started = time.perf_counter()
    out = EVIDENCE_DIR / f"d03_orchestration_smoke_{_now()}.json"
    if not SMOKE.exists():
        artifact = {
            "experiment_id": "d03_orchestration_smoke",
            "artifact": str(out),
            "skill": "regression-hygiene",
            "input": f"orchestration-os scripts/smoke_test.py ({SMOKE})",
            "environment": f"domain-router repo @ {REPO}",
            "failure_injected": "none — full OS smoke suite is the regression probe",
            "expected_behavior": "all 25 OS smoke tests pass (registry, planner incl. route + replan, executor incl. route skill + verification, audit, meta-learner, CLI dry-run, stubbed dispatch, reality loop, feedback events, replay-consumer gate leg, vault-check-first, sovereign-verification, registry sync)",
            "actual_behavior": f"smoke_test.py not found: {SMOKE}",
            "latency_ms": 0,
            "errors": [f"missing {SMOKE} — orchestration-os smoke suite not present"],
            "state_before": {"orchestration_os": str(ORCH), "smoke_tests": 25},
            "state_after": {"passed": False, "reason": "blocked"},
            "recovery": "install skill-orchestration-os at ~/.hermes/skills or point ORCH_OS at it",
            "false_repair": False,
            "evidence": [f"missing {SMOKE}"],
            "verdict": "blocked",  # same semantics as d04: runtime unavailable, gate cannot run
        }
        out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 2

    try:
        proc = subprocess.run(
            [PY, str(SMOKE)],
            capture_output=True, text=True, timeout=600, check=False,
            env=_zero_spend_env(),
        )
    except subprocess.TimeoutExpired:
        proc = type("P", (), {"returncode": 124, "stdout": "", "stderr": "timed out after 600s"})()
    latency_ms = int((time.perf_counter() - started) * 1000)

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    passed = proc.returncode == 0
    # Evidence line: the smoke suite's "N/25 passed" footer.
    evidence = [line.strip() for line in combined.splitlines() if re.search(r"\d+/\d+ passed", line)]
    summary = evidence[-1] if evidence else f"smoke exit {proc.returncode}"

    verdict = "pass" if passed else "fail"
    artifact = {
        "experiment_id": "d03_orchestration_smoke",
        "artifact": str(out),
        "skill": "regression-hygiene",
        "input": "orchestration-os scripts/smoke_test.py (25 tests: registry, planner, executor incl. route skill + verification, audit, meta-learner, CLI dry-run, stubbed dispatch, reality loop, feedback events, replay-consumer gate leg, vault-check-first, sovereign-verification, registry sync)",
        "environment": f"domain-router repo @ {REPO}",
        "failure_injected": "none — full OS smoke suite is the regression probe",
        "expected_behavior": "all 25 OS smoke tests pass (0-spend; uses --domain + dry-run only)",
        "actual_behavior": f"exit={proc.returncode} {summary}",
        "latency_ms": latency_ms,
        "errors": [] if passed else [(proc.stderr or "")[-300:]],
        "state_before": {"orchestration_os": str(ORCH), "smoke_tests": 25, "zero_spend": True, "env_scrubbed": list(ZERO_SPEND_ENV_VARS)},
        "state_after": {"exit": proc.returncode, "passed": passed, "summary": summary},
        "recovery": "n/a — read-only verification",
        "false_repair": False,
        "evidence": evidence or [f"smoke exit {proc.returncode}"],
        "verdict": verdict,
    }

    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
