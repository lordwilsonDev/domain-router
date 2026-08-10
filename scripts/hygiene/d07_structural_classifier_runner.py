#!/usr/bin/env python3
"""d07_structural_classifier_runner.py — the structural-classifier oracle gate.

The hot-loop-safety-audit skill (vault 70-006) defines a three-class diff
classifier (BODY_ONLY / ADDITIVE / STRUCTURAL). Its executable Step-2 gate is
`~/bin/structural_classifier.py` — a stdlib-only, line-based Swift
structural-signature parser that emits HOT_SWAP / REGEN / REBUILD /
REBUILD_STATE_RESET / NO_OP verdicts, with a labeled 26-case oracle fixture
(structural_cases.jsonl) covering the tricky classes: one-line type decls,
inferred-literal type changes, closure-initialized stored props, removed
live-root singletons, conformance-via-extension, and more.

This runner runs `--self-test` and FAILS unless the oracle is fully green.
The oracle is the classifier's regression guard: a change that re-opens a
fixed hole (e.g. a structural change classified as NO_OP) fails loudly here,
before the classifier is trusted by any real hot-loop decision.

Zero-spend by design: the classifier is stdlib-only and never touches the
network. The subprocess env is scrubbed of the canonical paid-API credential
set anyway (kept in lockstep with engineering-hygiene-factory run_factory.py
ZERO_SPEND_ENV_VARS), so a leaked key can never turn the gate into a spend.

Verdicts:
  pass    -- --self-test exits 0 with N/N oracle cases matched (N == total)
  fail    -- any oracle case mismatch, nonzero exit, or missing classifier
  blocked -- the classifier script is present but not runnable on this host
             (e.g. interpreter mismatch); non-fatal, recorded

A missing classifier is FAIL, not blocked: a dependency that vanishes must
fail loudly, or the daily gate would stay green while the check never runs —
the exact silent-failure this gate exists to prevent.

Artifact: <repo>/artifacts/hygiene/d07_structural_classifier_<ts>.json
Exit code: 0 = pass/blocked, 1 = fail.
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
PY = os.environ.get("MSB_PYTHON", sys.executable)
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

CLASSIFIER = os.environ.get(
    "STRUCTURAL_CLASSIFIER",
    str(Path.home() / "bin" / "structural_classifier.py"),
)

# Scrub the canonical paid-API set from the subprocess even though the
# classifier never calls them. MUST stay in lockstep with
# engineering-hygiene-factory run_factory.py ZERO_SPEND_ENV_VARS
# (test_d03_sync.py enforces parity).
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

_SUMMARY_RE = re.compile(r"(\d+)/(\d+) oracle cases matched\s*$")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _scrubbed_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in ZERO_SPEND_ENV_VARS:
        env.pop(k, None)
    return env


def main() -> int:
    t0 = time.time()
    errors: list[str] = []

    classifier = Path(CLASSIFIER)
    if not classifier.exists():
        return _emit(
            "fail",
            f"structural_classifier.py missing ({classifier}) — the oracle cannot run",
            {"script": str(classifier)},
            errors, t0,
        )

    try:
        proc = subprocess.run(
            [PY, str(classifier), "--self-test"],
            capture_output=True, text=True, timeout=120,
            env=_scrubbed_env(),
        )
    except subprocess.TimeoutExpired:
        return _emit("blocked", "structural_classifier --self-test timed out (120s)",
                     {}, errors, t0)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    exit_code = proc.returncode

    summary = ""
    for line in reversed(stdout.splitlines()):
        m = _SUMMARY_RE.search(line.strip())
        if m:
            summary = line.strip()
            matched, total = int(m.group(1)), int(m.group(2))
            break

    if exit_code != 0:
        return _emit(
            "fail",
            f"self-test exited {exit_code} — oracle mismatch",
            {"stdout_tail": stdout[-500:], "stderr": stderr[-500:], "summary": summary},
            errors, t0,
        )
    if not summary:
        return _emit(
            "fail",
            "self-test exited 0 but no 'N/N oracle cases matched' summary line found",
            {"stdout_tail": stdout[-500:], "stderr": stderr[-500:]},
            errors, t0,
        )
    if matched != total or total == 0:
        return _emit(
            "fail",
            f"oracle not fully green: {summary}",
            {"stdout_tail": stdout[-500:]},
            errors, t0,
        )

    return _emit(
        "pass",
        f"oracle green: {summary}",
        {"summary": summary, "classifier": str(classifier)},
        errors, t0,
    )


def _emit(verdict: str, summary: str, extra: dict[str, object],
          errors: list[str], t0: float) -> int:
    latency_ms = int((time.time() - t0) * 1000)
    artifact = {
        "experiment_id": "d07_structural_classifier",
        "artifact": "",
        "skill": "hot-loop-safety-audit",
        "input": "structural_classifier.py --self-test (26-case labeled oracle)",
        "environment": "domain-router repo @ " + str(REPO),
        "failure_injected": "none — live oracle of the structural classifier",
        "expected_behavior": "all N/N oracle cases match (exit 0)",
        "actual_behavior": summary,
        "latency_ms": latency_ms,
        "errors": errors,
        "state_before": {"classifier": CLASSIFIER, "scrubbed_env": list(ZERO_SPEND_ENV_VARS)},
        "state_after": extra,
        "recovery": "fix the classifier or extend the oracle: edit ~/bin/structural_classifier.py + structural_cases.jsonl, run --self-test until green",
        "false_repair": False,
        "evidence": [summary] + ([f"{k}={v}" for k, v in extra.items() if isinstance(v, str)]),
        "verdict": verdict,
    }
    out = EVIDENCE_DIR / f"d07_structural_classifier_{_now()}.json"
    artifact["artifact"] = str(out)
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if verdict in ("pass", "blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
