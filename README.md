# Domain Router

A thin routing front-end: **one task → one skill → one Claude subagent
dispatch → one audit line.** Just a router. No planning DAGs, no rollback,
no meta-learning — that stays in `skill-orchestration-os`.

> **Folded into skill-orchestration-os.** The canonical implementation now
> lives there as its routing front-end (`runtime/domain_router.py`), sharing
> the OS's DeepSeek transport (`runtime/deepseek.py`) with the planner. This
> folder is the product home: spec, dashboard note, factory-gate suite,
> tests, and audit deliverable. `router.py` / `build_registry.py` here are
> thin forwarding shims — one copy of the logic, two entry points
> (`skill-os route "<task>"` and `router.py "<task>"`).

Spec: `docs/superpowers/specs/2026-08-09-domain-router-design.md` ·
Dashboard: `~/Documents/Vault/10_Projects/Domain-Router.md`

## Files

| file | purpose |
|---|---|
| `build_registry.py` | scan `~/.hermes/skills/**/SKILL.md` → `domains.json` (362 skills, 46 containers) |
| `domains.json` | routing table (generated, committed) |
| `router.py` | CLI: classify → validate → dispatch → audit |
| `audit.jsonl` | one line per route `{ts, task, skill_id, container, reason, exit_code, result_summary}` |
| `test_router.py` | unit tests — zero API/subagent spend |

## Usage

```bash
python build_registry.py --rebuild        # regenerate domains.json
python router.py "<task>"                 # classify -> dispatch -> record
python router.py --dry-run "<task>"       # classify + print command, no spend
python router.py --domain <skill_id> "<task>"   # skip classification
python router.py --domain <container> "<task>"  # unambiguous single-leaf container
python router.py --list [container]       # browse the registry
```

## How it routes

1. **Classify** — DeepSeek (`DEEPSEEK_API_KEY`, `deepseek-chat`, temp 0.2)
   picks exactly one `skill_id` from the registry. The transport is the SAME
   shared `runtime/deepseek.py` the orchestrator planner uses — one copy, not
   two (the fold's point). Unlike the planner, the router **fails loud** (no
   silent fallback, spec §9).
2. **Validate** — unknown `skill_id`s are rejected with closest matches,
   never dispatched.
3. **Dispatch** — `cd <skill.dir> && claude -p --permission-mode acceptEdits
   --allowedTools Read Glob Grep Bash Write Edit --output-format json`
   "Load and follow ./SKILL.md …". Directory-based: relative script paths
   resolve, no reliance on skill-name discovery. Also available as the
   `route` skill inside the orchestrator's executor (a DAG step).
4. **Record** — append to the route audit log (`logs/route_audit.jsonl`
   canonically; this project's `audit.jsonl` via the shim).

## Factory gate (verified on rebuild)

The domain-router is onboarded into the engineering-hygiene factory
(`~/.hermes/skills/engineering/engineering-hygiene-factory`), so the routing
table and unit suite are re-verified by the same weakest-verdict gate as the
other onboarded projects:

| experiment | skill | what it verifies |
|---|---|---|
| `d01_registry_integrity` | registry-hygiene | `build_registry.py --rebuild` against the **live** skill tree: all SKILL.md present, zero blank descriptions, zero duplicate skill_ids, hidden dirs excluded, deterministic rebuild, disk == build |
| `d02_test_suite` | regression-hygiene | `pytest test_router.py -q` (23 unit tests) at repo root |

```bash
# run the full gate (writes artifacts/hygiene/factory_gate.json)
python ~/.hermes/skills/engineering/engineering-hygiene-factory/scripts/run_factory.py \
    --project ~/.hermes/domain-router

# or the daily-gate script (alerts on anything not PASS; launchd-ready)
bash scripts/factory_gate_domain_router.sh
```

The suite declares `pytest_target: test_router.py` (tests live at the root,
not `tests/`) and `live_auth: false` (pure CLI — the factory's auth probe is
scoped out, recorded as not-applicable, not an unknown).

## YAGNI (explicitly out)

Meta-learning, multi-skill fan-out, approval gates, rollback, circuit
breakers. One task → one skill → one dispatch → one log line.
