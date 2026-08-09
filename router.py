#!/usr/bin/env python3
"""domain-router CLI — forwarding shim.

The domain-router was folded into skill-orchestration-os as its routing
front-end; the canonical implementation now lives at
~/.hermes/skills/skill-orchestration-os/runtime/domain_router.py and shares
its DeepSeek transport with the Orchestrator planner (runtime/deepseek.py).

This shim keeps the standalone entry point, its unit tests, and its audit
deliverable (~/.hermes/domain-router/audit.jsonl) working unchanged. It
re-exports the canonical building blocks and composes them here — through the
SAME module-level names the tests monkeypatch — so interception still works.

Canonical spec: ~/.hermes/domain-router/docs/superpowers/specs/2026-08-09-domain-router-design.md
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ORCH = Path.home() / ".hermes" / "skills" / "skill-orchestration-os"
if str(_ORCH) not in sys.path:
    sys.path.insert(0, str(_ORCH))

from runtime import domain_router as _canonical  # noqa: E402

# Re-export pure helpers (tests call these directly; no interception needed).
_deepseek_prompt = _canonical._deepseek_prompt
_strip_fences = _canonical.strip_fences
_summarize_output = _canonical._summarize_output
build_dispatch_command = _canonical.build_dispatch_command
validate_skill_id = _canonical.validate_skill_id
registry_by_id = _canonical.registry_by_id

# Module-level names the tests monkeypatch (documented contract of this shim).
AUDIT_PATH = Path(__file__).resolve().parent / "audit.jsonl"
REGISTRY_PATH = _canonical.REGISTRY_PATH
_post_deepseek = _canonical._post_deepseek
dispatch = _canonical.dispatch


def classify(task: str, registry: dict, api_key: str) -> dict:
    """Fail-loud classification composed through THIS module's names, so the
    unit tests' monkeypatches on `_post_deepseek` still intercept. The
    canonical logic (runtime/domain_router.classify) is the source of truth;
    this wrapper preserves the shim's interception contract."""
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set — set it, or bypass classification with --domain <skill_id>"
        )
    try:
        content = _strip_fences(_post_deepseek(_deepseek_prompt(task, registry), api_key))
    except Exception as exc:  # network / HTTP / parse — fail loud, never guess
        raise RuntimeError(f"DeepSeek classification failed: {exc}") from exc
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DeepSeek returned non-JSON: {content[:200]!r}") from exc
    skill_id = str(result.get("skill_id", "")).strip()
    reason = str(result.get("reason", "")).strip()
    if not skill_id:
        raise RuntimeError(f"DeepSeek response missing skill_id: {result!r}")
    return {"skill_id": skill_id, "reason": reason}


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        raise SystemExit(f"no {REGISTRY_PATH} — run: python build_registry.py --rebuild")
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def append_audit(record: dict) -> None:
    """Write the product-home audit deliverable (kept where it always was)."""
    _canonical.append_audit(record, audit_path=AUDIT_PATH)


def main(argv: list[str] | None = None) -> int:
    """Same CLI as before — composed through this shim's own names so the
    unit tests' monkeypatches keep intercepting."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="router.py",
        description="Classify a task to exactly one skill and dispatch a Claude subagent.",
    )
    parser.add_argument("task", nargs="?", help="the task to route")
    parser.add_argument("--domain", metavar="SKILL_ID", help="skip classification; dispatch this skill directly")
    parser.add_argument("--dry-run", action="store_true", help="classify + print the claude command, do not execute")
    parser.add_argument("--rebuild", action="store_true", help="regenerate domains.json (needs build_registry.py)")
    parser.add_argument("--list", nargs="?", const="__all__", metavar="CONTAINER", help="browse the registry")
    args = parser.parse_args(argv)

    if args.rebuild:
        import build_registry
        return build_registry.main(["--rebuild"])
    if args.list:
        from runtime.domain_router import cmd_list
        args.container = None if args.list == "__all__" else args.list
        return cmd_list(args)
    if not args.task:
        parser.print_help()
        return 2

    registry = load_registry()
    by_id = registry_by_id(registry)

    if args.domain:
        skill_id = args.domain
        reason = "manual --domain override"
        if skill_id not in by_id:
            leaves = [sid for sid, e in by_id.items() if e["container"] == skill_id]
            if len(leaves) == 1:
                skill_id = leaves[0]
                reason = "manual --domain (container resolved unambiguously)"
            else:
                if leaves:
                    print(f"container {skill_id!r} holds {len(leaves)} skills:")
                    for sid in sorted(leaves):
                        print(f"  {sid}")
                else:
                    validate_skill_id(skill_id, by_id)  # raises with closest matches
                return 1
    else:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        result = classify(args.task, registry, api_key)
        skill_id = result["skill_id"]
        reason = result.get("reason", "")
        skill_id, warn = validate_skill_id(skill_id, by_id)
        if warn:
            print(warn)

    entry = by_id[skill_id]
    exit_code, summary = dispatch(entry, args.task, args.dry_run)

    append_audit({
        "task": args.task,
        "skill_id": entry["skill_id"],
        "container": entry["container"],
        "reason": reason,
        "exit_code": exit_code,
        "result_summary": summary,
    })

    if not args.dry_run:
        print(f"dispatched: {entry['skill_id']} (exit {exit_code})")
    return 0 if exit_code == 0 else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
