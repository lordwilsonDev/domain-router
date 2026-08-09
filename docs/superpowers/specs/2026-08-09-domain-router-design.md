# Domain Router — Design Spec

- **Date:** 2026-08-09
- **Status:** for review (implementation blocked until approved)
- **Dashboard:** `~/Documents/Vault/10_Projects/Domain-Router.md`

## 1. Purpose (unchanged)

A thin, standalone, autonomous CLI. One task → one skill → dispatch a Claude
subagent on that skill → record. **Just a router.** No planning DAGs, no
rollback, no meta-learning — that stays in `skill-orchestration-os`.

## 2. What changed after contact with the disk

The skeleton survived: **classification approach, audit, and CLI surface all
stand.** Three corrections are baked into this spec:

1. **Registry is hierarchical, not flat.** ~46 containers / **362 skills** at
   varying depths — not 50 flat folders. (§4)
2. **Dispatch is directory-based, and simpler.** Point `claude -p` at the
   skill's directory / `SKILL.md` path instead of resolving a skill *name*.
   This eliminates the earlier "is `~/.hermes/skills` on the skill search
   path?" risk — a path always resolves. (§6)
3. **Everything else confirmed** — DeepSeek-only single classify, `audit.jsonl`,
   the `router.py` CLI surface.

Two details recon surfaced that the spec must handle:

- **Container count is definitional, not 49/50.** Of 50 top-level dirs, 2 are
  hidden support dirs (`.hub`, `.curator_backups`) and 2 hold zero skills
  (`powerup`, `buh-axiom-library`). The registry rule (§4) defines the exact
  routable set; the number is whatever the scan yields (~46).
- **44 skills have no `description`.** All 362 have `name`/`id`; only 319 carry
  a `description`. The classifier routes on descriptions, so the builder needs
  a fallback (§4) — no skill may appear to the classifier as a blank line.

## 3. Ground truth (skill tree)

```
~/.hermes/skills/
  <container>/SKILL.md                         # 18 skills: container IS a skill
  <container>/<skill>/SKILL.md                 # 259 skills: the common case
  <container>/<skill>/skills/<sub>/SKILL.md    # 85 skills: nested 3–4 deep
```

362 `SKILL.md` leaves total. Depths: 18 @1, 259 @2, 42 @3, 43 @4 (relative to
`skills/`).

## 4. Registry — `build_registry.py` → `domains.json`

**Routable unit = a leaf skill** = any directory containing a `SKILL.md`,
identified by its path relative to `~/.hermes/skills/` (`skill_id`).

Scan `~/.hermes/skills/**/SKILL.md` and, per file, emit:

```json
{
  "skill_id":   "engineering/engineering-hygiene-factory",   // relpath, unique
  "name":       "engineering-hygiene-factory",               // from frontmatter name/id
  "description":"Transform software projects into ...",       // see fallback
  "container":  "engineering",                                // first path segment
  "dir":        "/Users/lordwilson/.hermes/skills/engineering/engineering-hygiene-factory",
  "skill_md":   ".../engineering-hygiene-factory/SKILL.md"
}
```

Rules:
- **Exclude hidden dirs** (any path segment starting with `.`) → drops
  `.hub`, `.curator_backups`.
- **Container** = first path segment of `skill_id`.
- **Description fallback** (for the 44 without one), in order:
  1. `description:` frontmatter (YAML), else
  2. first non-heading, non-blank paragraph of the `SKILL.md` body, trimmed to
     ~200 chars, else
  3. the `name`.
- Output shape:
  `{ "generated_at", "count", "containers": [<name>...], "skills": [<entry>...] }`.
  Committed to the repo.
- `--rebuild` regenerates it.

## 5. Classification (confirmed — DeepSeek, single-stage)

- Reuse the exact urllib client from
  `skill-orchestration-os/runtime/orchestrator.py`
  (`DEEPSEEK_API_KEY`, `deepseek-chat`, temp 0.2, strip ``` fences, `json.loads`).
- Prompt = the task + a compact registry listing (`skill_id — description`,
  grouped by container for readability). Model returns **exactly one**
  `{ "skill_id", "reason" }`.
- **Validate** `skill_id ∈ registry`. If not → reject (non-zero exit, show
  closest matches). Never dispatch an unvalidated id.
- `--domain <skill_id | name | container>` bypasses DeepSeek (manual override /
  DeepSeek-down escape). A bare container resolves only if unambiguous, else the
  CLI lists that container's skills and exits.

> **OPEN DECISION for review — routing granularity.** Single-stage classify over
> all 362 leaf skills (recommended: the router owns the whole decision → *no
> ambiguity*, one skill out), **vs** two-stage container→skill (smaller prompts,
> but two model calls). Recommendation: **single-stage**; fall back to two-stage
> only if a 362-item prompt proves unreliable in practice. Flagged because it is
> the one materially reversible choice left.

## 6. Dispatch (CHANGED — directory-based, simpler)

- `claude -p "<prompt>"` headless, pointed **at the skill's directory**:
  - grant the subagent the skill dir (e.g. `--add-dir <entry.dir>`) and name the
    exact file in the prompt: *"Load and follow the skill at `<entry.skill_md>`.
    Use it to accomplish: `<task>`."*
  - No dependency on skill-**name** discovery or on `~/.hermes/skills` being a
    search path — an absolute path always resolves. This is the simplification.
- Capture the subagent's stdout + exit code.
- `--dry-run` builds and prints this exact command **without** executing it.

## 7. Audit (confirmed)

Append one line per route to `audit.jsonl`:
`{ ts, task, skill_id, container, reason, exit_code, result_summary }`.

## 8. CLI surface

- `router.py "<task>"` → classify → dispatch → print skill + result
- `--domain <id|name|container>` → skip classification
- `--dry-run` → classify + print the `claude -p` command, no execution, no spend
- `--rebuild` → regenerate `domains.json`
- `--list [container]` → browse the registry (sanity/debug)

## 9. Failure handling (no guessing)

| condition | behavior |
|---|---|
| no `DEEPSEEK_API_KEY` / DeepSeek down | fail loudly; suggest `--domain` |
| classifier returns unknown `skill_id` | reject, show closest, non-zero exit |
| `claude -p` missing / errors | record + non-zero exit |
| skill has no `description` | registry fallback (§4) — never a blank entry |

## 10. Testing

- **Unit:** registry build across all four depths; description fallback; hidden/
  empty exclusion; classify prompt-build + response-parse (mocked DeepSeek);
  unknown-`skill_id` rejection; **directory-based** dispatch-command construction;
  audit write.
- **`--dry-run` integration:** asserts exactly one valid `skill_id` and a
  well-formed directory-based `claude -p` command, with **zero** API/subagent
  spend (DeepSeek mocked).

## 11. Acceptance criteria

- [ ] `build_registry.py --rebuild` emits `domains.json` with **all 362** skills
      (every `SKILL.md`, all depths), each carrying a non-empty description
      (fallback applied), grouped under ~46 containers; hidden/empty dirs excluded.
- [ ] `router.py --dry-run "<task>"` yields **exactly one** valid `skill_id` and a
      well-formed directory-based `claude -p` command — **zero** spend when mocked.
- [ ] Unknown classifier output is **rejected** (non-zero), never dispatched.
- [ ] `--domain <id>` bypasses DeepSeek and dispatches directly.
- [ ] A real route dispatches the subagent and writes a complete `audit.jsonl`
      line (`skill_id`, `container`, `reason`, `exit_code`, `result_summary`).
- [ ] Dispatch is **directory-based** (absolute `SKILL.md` path), not name-based.
- [ ] DeepSeek client is **reused** from `orchestrator.py`, not reinvented.
- [ ] `test_router.py` passes.
- [ ] YAGNI held: no meta-learning / DAG / approval gates / circuit breakers.

## 12. Out of scope (YAGNI)

Meta-learning, multi-skill fan-out, approval gates, rollback, circuit breakers.
One task → one skill → one dispatch → one log line.
