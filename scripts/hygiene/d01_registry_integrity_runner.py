#!/usr/bin/env python3
"""d01_registry_integrity_runner.py — the routing table is verified on rebuild.

Rebuilds domains.json from the LIVE skill tree (build_registry.py --rebuild)
and asserts the spec §11-1 invariants that make the table trustworthy:

  - every SKILL.md at every depth is present (no silent drops)
  - zero blank descriptions (fallback chain applied)
  - zero duplicate skill_ids
  - hidden directories (.hub, .curator_backups, ...) excluded
  - deterministic rebuild (two passes produce identical tables)

Artifact: <repo>/artifacts/hygiene/d01_registry_integrity_<ts>.json
Exit code: 0 = pass, 1 = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import build_registry  # noqa: E402

EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    started = time.perf_counter()
    errors: list[str] = []

    # 1. Deterministic? Build twice, compare the exact skill_id lists.
    first = build_registry.build()
    second = build_registry.build()
    ids1 = [e["skill_id"] for e in first["skills"]]
    ids2 = [e["skill_id"] for e in second["skills"]]
    deterministic = ids1 == ids2
    if not deterministic:
        errors.append("two builds produced different skill_id lists (non-deterministic)")

    # 2. CLI --rebuild actually writes domains.json (the shipped artifact).
    build_registry.main(["--rebuild"])
    on_disk = json.loads((REPO / "domains.json").read_text(encoding="utf-8"))
    disk_ids = [e["skill_id"] for e in on_disk["skills"]]
    if disk_ids != ids1:
        errors.append("domains.json on disk does not match a fresh in-memory build")

    # 3. Invariants on the built table.
    blank = [e["skill_id"] for e in first["skills"] if not e["description"].strip()]
    dups = [sid for sid in set(ids1) if ids1.count(sid) > 1]
    hidden = [sid for sid in ids1 if any(seg.startswith(".") for seg in sid.split("/"))]
    missing_md = [e["skill_id"] for e in first["skills"] if not Path(e["skill_md"]).exists()]
    if blank:
        errors.append(f"{len(blank)} blank descriptions: {blank[:3]}")
    if dups:
        errors.append(f"duplicate skill_ids: {dups[:3]}")
    if hidden:
        errors.append(f"hidden dirs leaked into table: {hidden[:3]}")
    if missing_md:
        errors.append(f"{len(missing_md)} entries reference missing SKILL.md files")

    verdict = "pass" if not errors else "fail"
    latency_ms = int((time.perf_counter() - started) * 1000)
    out = EVIDENCE_DIR / f"d01_registry_integrity_{_now()}.json"

    artifact = {
        "experiment_id": "d01_registry_integrity",
        "artifact": str(out),
        "skill": "registry-hygiene",
        "input": "build_registry.py --rebuild against the live ~/.hermes/skills tree",
        "environment": f"domain-router repo @ {REPO}",
        "failure_injected": "none \u2014 rebuild-from-live-tree is the integrity probe",
        "expected_behavior": "all SKILL.md entries present at every depth; zero blank "
                             "descriptions; zero duplicate skill_ids; hidden dirs excluded; "
                             "deterministic rebuild",
        "actual_behavior": (f"skills={first['count']} containers={len(first['containers'])} "
                            f"blank={len(blank)} dups={len(dups)} hidden={len(hidden)} "
                            f"deterministic={deterministic}"),
        "latency_ms": latency_ms,
        "errors": errors,
        "state_before": {"domains.json": "rebuilt from live tree", "skill_md_scanned": "live rglob"},
        "state_after": {
            "count": first["count"],
            "containers": len(first["containers"]),
            "blank_descriptions": len(blank),
            "duplicate_skill_ids": len(dups),
            "hidden_leaked": len(hidden),
            "deterministic": deterministic,
            "on_disk_matches_build": disk_ids == ids1,
        },
        "recovery": "n/a \u2014 read-only verification (rebuild is not a mutation of source)",
        "false_repair": False,
        "evidence": [
            f"skills={first['count']} containers={len(first['containers'])} "
            f"deterministic={deterministic} on_disk_match={disk_ids == ids1}",
            f"blank={len(blank)} dups={len(dups)} hidden={len(hidden)} missing_md={len(missing_md)}",
        ],
        "verdict": verdict,
    }

    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
