#!/usr/bin/env python3
"""d06_vault_search_mcp_runner.py — the vault-search MCP contract gate.

The vault semantic search surface is duplicated across three places that must
never drift apart:

  ~/vault-search-mcp/server.py      the MCP server (search_vault, reindex_vault)
  ~/vault-search-mcp/tool_manifest.json  its tool manifest
  ~/bin/vault-reindex.py            the detached full-reindexer
  ~/bin/vault-check.py              the freshness gate (points-vs-chunks math)

All of them hardcode the same chunking contract (CHUNK_SIZE=3000,
CHUNK_OVERLAP=200, the same excluded dirs). If one copy drifts, the freshness
gate compares points against the wrong chunk estimate and the index silently
misrepresents coverage -- the exact silent-lie this factory exists to kill.

This gate checks, zero-spend and without importing fastmcp (whose pydantic
pin is broken in the base python):
  1. server.py parses.
  2. The manifest's tool list matches the server's actual @mcp.tool() surface
     (search_vault, reindex_vault) -- a stale manifest describing old OpenAPI
     endpoints is a contract lie.
  3. Chunking constants (3000 / 200 / excludes) agree across server.py,
     vault-reindex.py, and vault-check.py.

Verdicts: pass / fail (missing file or any mismatch = fail -- loud, never
blocked: there is no "service down" state for a file-contract check).

Artifact: <repo>/artifacts/hygiene/d06_vault_search_mcp_<ts>.json
Exit code: 0 = pass, 1 = fail.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

SERVER = Path(os.environ.get("MCP_SERVER", str(Path.home() / "vault-search-mcp" / "server.py")))
MANIFEST = Path(os.environ.get("MCP_MANIFEST", str(Path.home() / "vault-search-mcp" / "tool_manifest.json")))
REINDEX = Path(os.environ.get("VAULT_REINDEX", str(Path.home() / "bin" / "vault-reindex.py")))
VAULT_CHECK = Path(os.environ.get("VAULT_CHECK", str(Path.home() / "bin" / "vault-check.py")))

EXPECTED_TOOLS = {"search_vault", "reindex_vault"}
EXPECTED_CHUNK_SIZE = 3000
EXPECTED_CHUNK_OVERLAP = 200
EXPECTED_EXCLUDES = {"/Claude-Conversations/", "/.obsidian/", "/.git/"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def _assign_const(tree: ast.Module, name: str):
    """First top-level assignment constant for `name` (int, str, or tuple of str)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    v = node.value
                    if isinstance(v, ast.Constant):
                        return v.value
                    if isinstance(v, (ast.Tuple, ast.List)):
                        return tuple(e.value for e in v.elts if isinstance(e, ast.Constant))
    return None


def _mcp_tool_names(tree: ast.Module) -> set[str]:
    tools: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "tool"):
                    tools.add(node.name)
    return tools


def _int_literals(tree: ast.Module) -> set[int]:
    vals: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            vals.add(node.value)
    return vals


def _check_files(files: dict[str, Path]) -> list[str]:
    missing = [label for label, p in files.items() if not p.exists()]
    if missing:
        return [f"missing file(s): {', '.join(missing)} — the gate cannot verify the contract"]
    return []


def main() -> int:
    t0 = time.time()
    errors: list[str] = _check_files(
        {"server.py": SERVER, "tool_manifest.json": MANIFEST,
         "vault-reindex.py": REINDEX, "vault-check.py": VAULT_CHECK}
    )
    if errors:
        return _emit("fail", errors[0], {}, errors, t0)

    server_tree = _parse(SERVER)
    reindex_tree = _parse(REINDEX)
    check_tree = _parse(VAULT_CHECK)
    if server_tree is None or reindex_tree is None or check_tree is None:
        return _emit("fail", "one or more sources failed to parse (ast.parse)", {}, errors, t0)

    # 1. manifest ↔ server tool surface
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _emit("fail", f"tool_manifest.json is not valid JSON: {e}", {}, errors, t0)
    mtools = {t.get("name", "") for t in manifest.get("tools", []) if isinstance(t, dict)}
    stools = _mcp_tool_names(server_tree)

    checks: list[tuple[str, bool, str]] = []

    ok = mtools == stools == EXPECTED_TOOLS
    checks.append((
        "manifest matches server tool surface",
        ok,
        f"manifest={sorted(mtools)} server={sorted(stools)} expected={sorted(EXPECTED_TOOLS)}",
    ))

    schema_ok = all(
        isinstance(t.get("inputSchema"), dict) and t.get("description")
        for t in manifest.get("tools", []) if isinstance(t, dict)
    )
    checks.append(("manifest entries have description + inputSchema", schema_ok, ""))

    # 2. chunking parity across the three implementations
    server_cfg = {
        "CHUNK_SIZE": _assign_const(server_tree, "CHUNK_SIZE"),
        "CHUNK_OVERLAP": _assign_const(server_tree, "CHUNK_OVERLAP"),
        "EXCLUDE_DIRS": _assign_const(server_tree, "EXCLUDE_DIRS"),
    }
    reindex_cfg = {
        "CHUNK_SIZE": _assign_const(reindex_tree, "CHUNK_SIZE"),
        "CHUNK_OVERLAP": _assign_const(reindex_tree, "CHUNK_OVERLAP"),
        "EXCLUDE_DIRS": _assign_const(reindex_tree, "EXCLUDE_DIRS"),
    }
    check_excludes = _assign_const(check_tree, "EXCLUDE")

    parity_ok = (
        server_cfg["CHUNK_SIZE"] == reindex_cfg["CHUNK_SIZE"] == EXPECTED_CHUNK_SIZE
        and server_cfg["CHUNK_OVERLAP"] == reindex_cfg["CHUNK_OVERLAP"] == EXPECTED_CHUNK_OVERLAP
        and set(server_cfg["EXCLUDE_DIRS"] or ()) == set(reindex_cfg["EXCLUDE_DIRS"] or ()) == EXPECTED_EXCLUDES
        and set(check_excludes or ()) == EXPECTED_EXCLUDES
    )
    checks.append((
        "chunking contract parity (server/reindex/check)",
        parity_ok,
        f"server={server_cfg} reindex={reindex_cfg} check_excludes={check_excludes}",
    ))

    # vault-check's freshness math must mirror the same chunking numbers
    check_ints = _int_literals(check_tree)
    math_ok = {EXPECTED_CHUNK_SIZE, EXPECTED_CHUNK_OVERLAP} <= check_ints
    checks.append((
        "vault-check freshness math uses 3000/200",
        math_ok,
        f"int literals present: {sorted({EXPECTED_CHUNK_SIZE, EXPECTED_CHUNK_OVERLAP} & check_ints)}",
    ))

    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        detail = "; ".join(f"{name}: {msg}" for name, ok, msg in checks if not ok)
        return _emit("fail", f"contract drift: {', '.join(failed)} — {detail}", {}, errors, t0)

    detail = "; ".join(msg for _, _, msg in checks)
    return _emit("pass", f"contract intact ({len(checks)} checks) — {detail}", {"checks": checks}, errors, t0)


def _emit(verdict: str, summary: str, extra: dict[str, object],
          errors: list[str], t0: float) -> int:
    latency_ms = int((time.time() - t0) * 1000)
    artifact = {
        "experiment_id": "d06_vault_search_mcp",
        "artifact": "",
        "skill": "knowledge-hygiene",
        "input": "AST contract check: server.py / tool_manifest.json / vault-reindex.py / vault-check.py",
        "environment": "domain-router repo @ " + str(REPO),
        "failure_injected": "none — live contract check of the vault search surface",
        "expected_behavior": "manifest matches server tools (search_vault, reindex_vault); "
                             "chunking 3000/200/excludes identical across all three sources",
        "actual_behavior": summary,
        "latency_ms": latency_ms,
        "errors": errors,
        "state_before": {"server": str(SERVER), "manifest": str(MANIFEST),
                         "reindex": str(REINDEX), "vault_check": str(VAULT_CHECK)},
        "state_after": extra,
        "recovery": "fix the drifted file: regenerate the manifest from the server's @mcp.tool() "
                    "surface, or restore the chunking constants to 3000/200 with the canonical excludes",
        "false_repair": False,
        "evidence": [summary] + ([f"{k}={v}" for k, v in extra.items() if isinstance(v, str)]),
        "verdict": verdict,
    }
    out = EVIDENCE_DIR / f"d06_vault_search_mcp_{_now()}.json"
    artifact["artifact"] = str(out)
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
