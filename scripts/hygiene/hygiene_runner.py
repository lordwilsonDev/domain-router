#!/usr/bin/env python3

"""Engineering hygiene runner — single CLI index for the domain-router.

Thin delegating index (same contract as msb-v3/SOE): every experiment lives
in its own standalone runner; this file runs them and aggregates the verdicts
into a factory-style weakest-verdict gate. The hermes factory invokes this
with `--all --json`.

Experiments (serverless — this is a pure CLI tool, no daemon to probe):
  d01_registry_integrity  rebuild domains.json from the live skill tree and
                          assert §11-1 invariants (no blanks/dups/hidden,
                          deterministic)
  d02_test_suite          pytest test_router.py (23 unit tests) at repo root
  d03_orchestration_smoke orchestration-os scripts/smoke_test.py (12 tests,
                          zero-spend) — proves the folded front-end + OS still work

Usage:
    python hygiene_runner.py --all                 # run every experiment
    python hygiene_runner.py d01                   # run one experiment
    python hygiene_runner.py --only d01 d02        # run a subset
    python hygiene_runner.py --list                # list experiments
    python hygiene_runner.py --json                # machine-readable aggregate

Exit code: 0 if no experiment FAILED, 1 if any reports `fail`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(os.environ.get("MSB_REPO", Path(__file__).resolve().parents[2]))
HERE = Path(__file__).resolve().parent
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

PY = os.environ.get("MSB_PYTHON", sys.executable)

EXPERIMENTS: dict[str, Path] = {
    "d01_registry_integrity": HERE / "d01_registry_integrity_runner.py",
    "d02_test_suite": HERE / "d02_test_suite_runner.py",
    "d03_orchestration_smoke": HERE / "d03_orchestration_smoke_runner.py",
}

_WEIGHT = {"fail": 0, "partial": 1, "blocked": 2, "pass": 3, "unknown": 4}


def resolve_name(name: str) -> str:
    if name in EXPERIMENTS:
        return name
    for full in EXPERIMENTS:
        if full.startswith(name + "_") or full == name:
            return full
    return name


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run_experiment(name: str, runner: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [PY, str(runner)],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"experiment": name, "verdict": "fail", "artifact": None,
                "error": "runner timed out after 300s"}
    summary: dict[str, Any] = {"experiment": name, "verdict": "unknown",
                               "artifact": None, "returncode": proc.returncode}
    parsed = _extract_json_object(proc.stdout)
    if isinstance(parsed, dict) and "verdict" in parsed:
        for k in ("experiment", "verdict", "artifact"):
            value = parsed.get(k) or parsed.get("experiment_id")
            if value is not None:
                summary[k] = value
    if summary.get("verdict") == "unknown" and proc.returncode != 0:
        summary["verdict"] = "fail"
        summary["error"] = (proc.stderr or proc.stdout or "").strip()[-300:]
    return summary


def _extract_json_object(text: str, key: str | None = "verdict") -> dict[str, Any] | None:
    """Parse the TOP-LEVEL JSON object in text that contains `key`."""
    raw = text.strip()
    if not raw:
        return None
    candidates: list[dict[str, Any]] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] == "{":
            depth = 0
            for j in range(i, n):
                if raw[j] == "{":
                    depth += 1
                elif raw[j] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(raw[i:j + 1])
                        except json.JSONDecodeError:
                            obj = None
                        if isinstance(obj, dict):
                            candidates.append(obj)
                        i = j + 1
                        break
            else:
                break
        else:
            i += 1
    if key is not None:
        for obj in candidates:
            if key in obj:
                return obj
    return candidates[-1] if candidates else None


def weakest_verdict(results: list[dict[str, Any]]) -> str:
    verdicts = [r.get("verdict", "unknown") for r in results]
    return min(verdicts, key=lambda v: _WEIGHT.get(v, 4))


def build_aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    gate = weakest_verdict(results)
    return {
        "timestamp": now(),
        "environment": {"repo": str(REPO), "python": PY, "experiments": len(results)},
        "results": results,
        "factory_verdict": gate,
        "factory_gate": {
            "any_fail": any(r.get("verdict") == "fail" for r in results),
            "any_unknown": any(r.get("verdict") == "unknown" for r in results),
            "all_pass": all(r.get("verdict") == "pass" for r in results),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="domain-router hygiene runner (delegating index)")
    parser.add_argument("experiments", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--only", nargs="+", default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.list:
        for name, runner in EXPERIMENTS.items():
            print(f"{name:20s} {runner.name}")
        return 0

    if args.only:
        names = args.only
    elif args.all:
        names = list(EXPERIMENTS)
    elif args.experiments:
        names = args.experiments
    else:
        parser.error("provide --all, --only, or experiment names")

    names = [resolve_name(n) for n in names]
    unknown = [n for n in names if n not in EXPERIMENTS]
    if unknown:
        parser.error(f"unknown experiment(s): {', '.join(unknown)}")

    results: list[dict[str, Any]] = []
    for name in names:
        print(f"=== {name} ===", flush=True)
        summary = run_experiment(name, EXPERIMENTS[name])
        results.append(summary)
        print(json.dumps(summary, indent=2), flush=True)

    aggregate = build_aggregate(results)
    out = EVIDENCE_DIR / "hygiene_aggregate.json"
    out.write_text(json.dumps(aggregate, indent=2, default=str), encoding="utf-8")
    if args.json:
        print(json.dumps(aggregate, indent=2, default=str))
    else:
        print(f"\n=== aggregate === verdict={aggregate['factory_verdict']} "
              f"experiments={len(results)}")
        for r in results:
            print(f"  {r.get('experiment'):20s} {r.get('verdict'):8s} "
                  f"{r.get('artifact') or ''}")
        print(f"Aggregate: {out}")
    return 1 if aggregate["factory_verdict"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
