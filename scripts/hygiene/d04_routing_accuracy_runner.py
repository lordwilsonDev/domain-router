#!/usr/bin/env python3
"""d04_routing_accuracy_runner.py — routing-accuracy eval gate.

Turns "5/5 sensible picks once, by hand" into a standing regression guard.
A hand-labeled fixture (routing_cases.jsonl — 25 tasks -> expected
skill_id/container, including deliberately tricky near-duplicates) is run
through the REAL classifier pipeline from skill-orchestration-os (the
canonical runtime the domain-router folds into), producing a top-1 accuracy
that gates at a threshold.

Modes:
  replay (default, zero-spend)  Recorded DeepSeek responses
      (recorded_responses.jsonl) are replayed through the current
      prompt-builder + parser. Each recording carries the sha256 of the
      exact prompt it answered; if the current prompt differs (ANY skill
      description/registry change), the recording is STALE and the case
      fails with "re-run --live" — so replay genuinely catches description
      drift at zero spend, not just parse regressions. NEVER calls DeepSeek.
  --live                        Calls the REAL DeepSeek classifier for each
      fixture case and re-records raw responses + prompt hashes (idempotent
      re-seed; previous recordings are kept for any case that fails). The
      ONLY mode that spends; explicit, and requires DEEPSEEK_API_KEY (env or
      ~/.hermes/.env). Run directly, NOT via hygiene_runner --all (the index
      times out at 300s; a live run is slower).

Fixture drift is a failure: every expected_skill_id must still exist in the
current registry, so renaming/removing a skill fails the gate loudly.

Artifact: <repo>/artifacts/hygiene/d04_routing_accuracy_<ts>.json
Exit code: 0 = pass, 1 = fail, 2 = blocked (canonical runtime unavailable).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ORCH = Path(os.environ.get("ORCH_OS", str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os")))
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

CASES = HERE / "routing_cases.jsonl"
RECORDED = HERE / "recorded_responses.jsonl"

# Gate: top-1 skill accuracy must be >= this to PASS. Chosen with margin
# below the measured --live accuracy (see recorded_responses + artifact) so
# small drift doesn't break CI but real regression does. --threshold overrides.
DEFAULT_THRESHOLD = 0.8


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _deepseek_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    envp = Path.home() / ".hermes" / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def _load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _load_recorded(path: Path) -> dict[str, dict]:
    """id -> {prompt_hash, response}."""
    recorded: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entry = json.loads(line)
                recorded[entry["id"]] = {
                    "prompt_hash": entry.get("prompt_hash", ""),
                    "response": entry["response"],
                }
    return recorded


def _parse_response(raw: str, strip_fences_fn) -> dict:
    """Mirror runtime.domain_router.classify's parse path (strip_fences +
    json.loads + skill_id extraction). Raises on malformed output."""
    content = strip_fences_fn(raw)
    result = json.loads(content)
    skill_id = str(result.get("skill_id", "")).strip()
    reason = str(result.get("reason", "")).strip()
    if not skill_id:
        raise ValueError(f"response missing skill_id: {result!r}")
    return {"skill_id": skill_id, "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser(description="d04 routing-accuracy eval gate")
    parser.add_argument("--live", action="store_true",
                        help="call real DeepSeek and re-record responses (the only spending mode)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"minimum top-1 accuracy to pass (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--cases", type=Path, default=CASES, help="fixture path")
    parser.add_argument("--recorded", type=Path, default=RECORDED, help="recorded responses path")
    args = parser.parse_args()

    started = time.perf_counter()
    out = EVIDENCE_DIR / f"d04_routing_accuracy_{_now()}.json"

    # Canonical runtime must be present to build the real prompt / registry.
    try:
        sys.path.insert(0, str(ORCH))
        from runtime.domain_router import _deepseek_prompt, _post_deepseek, strip_fences  # noqa: PLC0415
        registry = json.loads((ORCH / "domains.json").read_text(encoding="utf-8"))
        by_id = {e["skill_id"]: e for e in registry["skills"]}
    except Exception as exc:  # noqa: BLE001
        artifact = {
            "experiment_id": "d04_routing_accuracy",
            "artifact": str(out),
            "skill": "regression-hygiene",
            "input": f"routing_cases.jsonl ({args.cases}) vs canonical registry ({ORCH}/domains.json)",
            "environment": f"domain-router repo @ {REPO}",
            "mode": "blocked",
            "expected_behavior": "top-1 routing accuracy >= threshold on the real classifier pipeline",
            "actual_behavior": f"cannot load canonical runtime: {exc}",
            "latency_ms": 0,
            "errors": [f"skill-orchestration-os runtime unavailable ({ORCH}) — install or set ORCH_OS"],
            "state_before": {"orchestration_os": str(ORCH), "cases": 0},
            "state_after": {"passed": False, "reason": "blocked"},
            "recovery": "install skill-orchestration-os at ~/.hermes/skills or point ORCH_OS at it",
            "false_repair": False,
            "evidence": [str(exc)],
            "verdict": "blocked",
        }
        out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 2

    cases = _load_cases(args.cases)
    recorded = _load_recorded(args.recorded)

    # Fixture drift guard: every expected id must exist in the current table.
    drift_errors = []
    for case in cases:
        expected = case["expected_skill_id"]
        if expected not in by_id:
            drift_errors.append(f"fixture {case['id']} expects unknown skill {expected!r} "
                                f"(renamed or removed from registry)")

    live = args.live
    if live and not _deepseek_key():
        artifact = {
            "experiment_id": "d04_routing_accuracy",
            "artifact": str(out),
            "skill": "regression-hygiene",
            "input": f"--live over {len(cases)} cases",
            "environment": f"domain-router repo @ {REPO}",
            "mode": "live",
            "expected_behavior": "re-record raw responses and gate accuracy",
            "actual_behavior": "DEEPSEEK_API_KEY not found (env or ~/.hermes/.env)",
            "latency_ms": 0,
            "errors": ["--live requires DEEPSEEK_API_KEY"],
            "state_before": {"cases": len(cases)},
            "state_after": {"passed": False},
            "recovery": "export DEEPSEEK_API_KEY or run replay (default, zero-spend)",
            "false_repair": False,
            "evidence": [],
            "verdict": "fail",
        }
        out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 1

    per_case = []
    new_recorded: dict[str, str] = {}
    for case in cases:
        cid = case["id"]
        task = case["task"]
        prompt = _deepseek_prompt(task, registry)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        try:
            if live:
                raw = None
                last_err = None
                for _attempt in range(2):  # one retry absorbs transient network blips
                    try:
                        raw = strip_fences(_post_deepseek(prompt, _deepseek_key()))
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
                if raw is None:
                    raise last_err or RuntimeError("live call failed")
                new_recorded[cid] = {"prompt_hash": prompt_hash, "response": raw}
                parsed = _parse_response(raw, strip_fences)
            else:
                if cid not in recorded:
                    raise ValueError(f"no recorded response for {cid} — seed with --live first")
                entry = recorded[cid]
                if entry.get("prompt_hash") != prompt_hash:
                    raise ValueError("prompt changed since recording (description/skill drift) — re-run --live to re-record")
                parsed = _parse_response(entry["response"], strip_fences)
        except Exception as exc:  # noqa: BLE001
            per_case.append({
                "id": cid, "task": task, "expected": case["expected_skill_id"],
                "predicted": None, "container_ok": False, "ok": False,
                "error": str(exc)[:200], "tricky": case.get("tricky", False),
            })
            continue
        skill_id = parsed["skill_id"]
        container_ok = by_id.get(skill_id, {}).get("container") == case["expected_container"]
        per_case.append({
            "id": cid, "task": task, "expected": case["expected_skill_id"],
            "predicted": skill_id, "container_ok": bool(container_ok), "ok": skill_id == case["expected_skill_id"],
            "reason": parsed.get("reason", ""), "tricky": case.get("tricky", False),
        })

    total = len(cases)
    correct = sum(1 for p in per_case if p["ok"])
    container_ok = sum(1 for p in per_case if p["container_ok"])
    accuracy = correct / total if total else 0.0
    container_acc = container_ok / total if total else 0.0
    tricky = [p for p in per_case if p["tricky"]]
    tricky_acc = (sum(1 for p in tricky if p["ok"]) / len(tricky)) if tricky else None

    if live:
        # Merge: keep the previous recording for any case that failed this
        # run, so a transient blip can't destroy the reference set.
        merged = dict(recorded)
        for cid, entry in new_recorded.items():
            merged[cid] = entry
        with args.recorded.open("w", encoding="utf-8") as fh:
            for cid in sorted(merged):
                fh.write(json.dumps({"id": cid, **merged[cid]}) + "\n")

    passed = (not drift_errors) and accuracy >= args.threshold
    latency_ms = int((time.perf_counter() - started) * 1000)
    verdict = "pass" if passed else "fail"
    summary = f"top-1 accuracy {accuracy:.2f} ({correct}/{total}) >= {args.threshold} | " \
              f"container {container_acc:.2f} ({container_ok}/{total}) | tricky {tricky_acc or 0:.2f}"

    artifact = {
        "experiment_id": "d04_routing_accuracy",
        "artifact": str(out),
        "skill": "regression-hygiene",
        "input": f"routing_cases.jsonl ({total} cases) vs canonical registry ({len(by_id)} skills)",
        "environment": f"domain-router repo @ {REPO}",
        "mode": "live" if live else "replay",
        "expected_behavior": f"top-1 accuracy >= {args.threshold} on the real classifier pipeline; no fixture drift",
        "actual_behavior": summary,
        "latency_ms": latency_ms,
        "errors": drift_errors + [p["error"] for p in per_case if p.get("error")],
        "state_before": {"cases": total, "registry_skills": len(by_id), "threshold": args.threshold,
                          "mode": "live" if live else "replay"},
        "state_after": {"accuracy": accuracy, "correct": correct, "total": total,
                         "container_accuracy": container_acc, "container_correct": container_ok,
                         "tricky_accuracy": tricky_acc, "passed": passed, "summary": summary,
                         "per_case": per_case},
        "recovery": "re-run with --live after description/skill changes to re-measure and re-record",
        "false_repair": False,
        "evidence": [f"accuracy={accuracy:.2f} ({correct}/{total}) threshold={args.threshold}",
                      f"container_acc={container_acc:.2f} ({container_ok}/{total})",
                      f"tricky_acc={tricky_acc:.2f} ({len(tricky)} tricky cases)",
                      *(f"{p['id']} {p['task'][:40]!r} expected={p['expected']} got={p['predicted']}" for p in per_case if not p["ok"])],
        "verdict": verdict,
    }

    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
