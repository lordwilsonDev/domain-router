#!/usr/bin/env python3
"""build_registry.py — forwarding shim.

The registry builder was folded into skill-orchestration-os (the canonical
copy now lives at ~/.hermes/skills/skill-orchestration-os/build_registry.py,
which is the single source of truth for the §4 description-fallback rules).
This shim keeps the standalone entry point, its unit tests, and the
standalone domains.json deliverable working unchanged. `build()`/`main()`
forward this module's SKILLS_ROOT into the canonical module first, so tests
that monkeypatch the shim's SKILLS_ROOT still intercept.

The canonical module is loaded via importlib under a distinct module name —
a plain `import build_registry` here would resolve to THIS partially
initialized module (same file name) and fail with a circular-import error.

Canonical spec: ~/.hermes/domain-router/docs/superpowers/specs/2026-08-09-domain-router-design.md
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ORCH = Path.home() / ".hermes" / "skills" / "skill-orchestration-os"
_canonical_path = _ORCH / "build_registry.py"

_spec = importlib.util.spec_from_file_location("orchestration_build_registry", _canonical_path)
_canonical = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_canonical)  # type: ignore[union-attr]

# Re-export the pure parser (tests call it directly; reads only its args).
parse_skill = _canonical.parse_skill

# Module-level name the tests monkeypatch; forwarded into the canonical module.
SKILLS_ROOT = _canonical.SKILLS_ROOT

OUT_PATH = Path(__file__).resolve().parent / "domains.json"


def build() -> dict:
    """Canonical build with THIS module's SKILLS_ROOT (test interception)."""
    _canonical.SKILLS_ROOT = SKILLS_ROOT
    return _canonical.build()


def main(argv: list[str] | None = None) -> int:
    """Canonical main, but the standalone domains.json deliverable is written
    to the product home (OUT_PATH above), as it always was."""
    _canonical.SKILLS_ROOT = SKILLS_ROOT
    data = _canonical.build()
    args = argv if argv is not None else sys.argv[1:]
    if "--rebuild" in args:
        OUT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT_PATH} ({data['count']} skills, {len(data['containers'])} containers)")
    else:
        blank = [e["skill_id"] for e in data["skills"] if not e["description"].strip()]
        print(f"{data['count']} skills across {len(data['containers'])} containers")
        if blank:
            print(f"WARNING: {len(blank)} entries with blank descriptions: {blank[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
