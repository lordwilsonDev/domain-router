#!/usr/bin/env python3
"""d05_vault_freshness_runner.py — the wilson-vault semantic index freshness gate.

The vault semantic index (Qdrant tenant `wilson-vault`, ~5400 chunks) is the
knowledge base every vault-grounded answer relies on. It only reflects the
vault as of the last reindex -- and a stale index is a silent lie: searches
return confident-looking results that miss most of the vault. This gate runs
`~/bin/vault-check.py --fresh` (the vault-check-first skill's wrapper) and
FAILS when the index has drifted from the on-disk chunk estimate.

Zero-spend by design: vault-check talks only to local services (msb-v3 on
:8766, Qdrant on :6333, Ollama on :11434) -- no paid API is ever called. The
subprocess env is scrubbed of the canonical paid-API credential set anyway,
kept in lockstep with engineering-hygiene-factory run_factory.py
ZERO_SPEND_ENV_VARS, so a leaked key can never turn the gate into a spend.

Verdicts:
  pass    -- index FRESH (points >= 90% of on-disk chunk estimate)
  fail    -- index STALE (needs `vault-check.py --reindex`, ~10 min)
  blocked -- msb-v3/Qdrant unreachable; cannot verify (non-fatal, recorded)

A missing vault-check.py is FAIL, not blocked: a dependency that vanishes
must fail loudly, or the daily gate would stay green while the check never
runs -- the exact silent-failure this gate exists to prevent.

Artifact: <repo>/artifacts/hygiene/d05_vault_freshness_<ts>.json
Exit code: 0 = pass/blocked, 1 = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = os.environ.get("MSB_PYTHON", sys.executable)
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

VAULT_CHECK = os.environ.get(
    "VAULT_CHECK",
    str(Path.home() / "bin" / "vault-check.py"),
)

# Scrub the canonical paid-API set from the subprocess even though vault-check
# never calls them. MUST stay in lockstep with engineering-hygiene-factory
# run_factory.py ZERO_SPEND_ENV_VARS (test_d03_sync.py enforces parity).
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

    if not Path(VAULT_CHECK).exists():
        return _emit(
            "fail",
            "vault-check.py missing -- the gate cannot verify freshness",
            {"script": VAULT_CHECK},
            errors, t0,
        )

    try:
        proc = subprocess.run(
            [PY, VAULT_CHECK, "--fresh"],
            capture_output=True, text=True, timeout=120,
            env=_scrubbed_env(),
        )
    except subprocess.TimeoutExpired:
        return _emit("blocked", "vault-check --fresh timed out (120s)", {}, errors, t0)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    exit_code = proc.returncode

    # vault-check --fresh exit codes: 0 = FRESH, 1 = STALE, 2 = service down.
    if exit_code == 2:
        return _emit("blocked", f"services unreachable: {stderr or stdout}", {}, errors, t0)
    if exit_code == 1:
        return _emit(
            "fail",
            "vault index is STALE — run `vault-check.py --reindex` (~10 min), then verify with --fresh",
            {"stdout": stdout, "stderr": stderr},
            errors, t0,
        )
    if exit_code == 0:
        return _emit("pass", f"index FRESH: {stdout}", {"stdout": stdout}, errors, t0)

    return _emit(
        "fail",
        f"vault-check --fresh returned unexpected exit {exit_code}",
        {"stdout": stdout, "stderr": stderr},
        errors, t0,
    )


def _emit(verdict: str, summary: str, extra: dict[str, object],
          errors: list[str], t0: float) -> int:
    latency_ms = int((time.time() - t0) * 1000)
    artifact = {
        "experiment_id": "d05_vault_freshness",
        "artifact": "",
        "skill": "knowledge-hygiene",
        "input": "~/bin/vault-check.py --fresh (semantic index vs on-disk chunk estimate)",
        "environment": "domain-router repo @ " + str(REPO),
        "failure_injected": "none — live freshness of the wilson-vault index",
        "expected_behavior": "indexed points >= 90% of on-disk chunk estimate (FRESH)",
        "actual_behavior": summary,
        "latency_ms": latency_ms,
        "errors": errors,
        "state_before": {"vault_check": VAULT_CHECK, "scrubbed_env": list(ZERO_SPEND_ENV_VARS)},
        "state_after": extra,
        "recovery": "reindex: vault-check.py --reindex (detached tmux, ~10 min), then --fresh",
        "false_repair": False,
        "evidence": [summary] + ([f"{k}={v}" for k, v in extra.items() if isinstance(v, str)]),
        "verdict": verdict,
    }
    out = EVIDENCE_DIR / f"d05_vault_freshness_{_now()}.json"
    artifact["artifact"] = str(out)
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if verdict in ("pass", "blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
