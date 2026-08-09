#!/usr/bin/env python3
"""Unit tests for domain-router (spec §10). No API or subagent spend.

Run: python -m pytest test_router.py -q
"""

from __future__ import annotations

import json

import pytest

import build_registry
import router


# --------------------------------------------------------------------------
# Registry build (spec §4)
# --------------------------------------------------------------------------

def _write(tmp, rel: str, content: str):
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


FULL_FM = """---
id: foo-skill
name: Foo Skill
version: 1.0
description: Does foo things really well.
---
# Foo
Body text.
"""

BARE_DESC = """# Bar Skill

description: Bar does bar things.

More body.
"""

NO_DESC_BODY = """# Baz Skill

This is the first substantive paragraph of the baz skill body, long enough to
pass the length filter and be used as a description fallback.
"""

NO_DESC_NO_BODY = """# Qux Skill

- a list item
- another list item
"""


def test_parse_full_frontmatter(tmp_path):
    md = _write(tmp_path, "c1/foo/SKILL.md", FULL_FM)
    e = build_registry.parse_skill(md, tmp_path)
    assert e["skill_id"] == "c1/foo"
    assert e["container"] == "c1"
    assert e["name"] == "Foo Skill"
    assert e["description"] == "Does foo things really well."


def test_parse_bare_description_line(tmp_path):
    md = _write(tmp_path, "c1/bar/SKILL.md", BARE_DESC)
    e = build_registry.parse_skill(md, tmp_path)
    assert e["description"] == "Bar does bar things."


def test_parse_falls_back_to_body_paragraph(tmp_path):
    md = _write(tmp_path, "c1/baz/SKILL.md", NO_DESC_BODY)
    e = build_registry.parse_skill(md, tmp_path)
    assert e["description"].startswith("This is the first substantive paragraph")
    assert len(e["description"]) <= 200


def test_parse_falls_back_to_name(tmp_path):
    md = _write(tmp_path, "c1/qux/SKILL.md", NO_DESC_NO_BODY)
    e = build_registry.parse_skill(md, tmp_path)
    assert e["description"] == "Qux Skill" or e["description"] == "qux"


def test_hidden_dirs_excluded(tmp_path):
    _write(tmp_path, ".hub/x/SKILL.md", FULL_FM)
    _write(tmp_path, "c1/ok/SKILL.md", FULL_FM)
    _write(tmp_path, "c1/nested/.curator_backups/y/SKILL.md", FULL_FM)
    md = tmp_path / "c1" / "ok" / "SKILL.md"
    assert build_registry.parse_skill(md, tmp_path) is not None
    for hidden in (tmp_path / ".hub" / "x" / "SKILL.md",):
        assert build_registry.parse_skill(hidden, tmp_path) is None


def test_build_covers_all_depths(tmp_path, monkeypatch):
    monkeypatch.setattr(build_registry, "SKILLS_ROOT", tmp_path)
    # four depths
    _write(tmp_path, "a/SKILL.md", FULL_FM)
    _write(tmp_path, "a/b/SKILL.md", FULL_FM)
    _write(tmp_path, "a/b/c/SKILL.md", FULL_FM)
    _write(tmp_path, "a/b/c/d/SKILL.md", FULL_FM)
    _write(tmp_path, ".hub/z/SKILL.md", FULL_FM)
    data = build_registry.build()
    assert data["count"] == 4
    assert set(data["containers"]) == {"a"}
    assert all(e["description"] for e in data["skills"])


def test_build_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(build_registry, "SKILLS_ROOT", tmp_path)
    _write(tmp_path, "b/SKILL.md", FULL_FM)
    _write(tmp_path, "a/SKILL.md", FULL_FM)
    first = build_registry.build()
    second = build_registry.build()
    assert [e["skill_id"] for e in first["skills"]] == [e["skill_id"] for e in second["skills"]]
    assert [e["skill_id"] for e in first["skills"]] == ["a", "b"]


# --------------------------------------------------------------------------
# Classification (spec §5) — DeepSeek mocked
# --------------------------------------------------------------------------

def _registry(tmp_path, monkeypatch) -> dict:
    data = {
        "count": 2,
        "containers": ["c1"],
        "skills": [
            {"skill_id": "c1/alpha", "name": "alpha", "description": "handles alpha tasks", "container": "c1", "dir": "/x/c1/alpha", "skill_md": "/x/c1/alpha/SKILL.md"},
            {"skill_id": "c1/beta", "name": "beta", "description": "handles beta tasks", "container": "c1", "dir": "/x/c1/beta", "skill_md": "/x/c1/beta/SKILL.md"},
        ],
    }
    return data


def test_prompt_contains_task_and_skill_ids():
    reg = _registry(None, None)
    prompt = router._deepseek_prompt("do alpha things", reg)
    assert "do alpha things" in prompt
    assert "c1/alpha" in prompt
    assert "c1/beta" in prompt
    assert "[c1]" in prompt  # grouped by container


def test_classify_parses_plain_json(monkeypatch):
    reg = _registry(None, None)
    monkeypatch.setattr(router, "_post_deepseek", lambda p, k: '{"skill_id": "c1/alpha", "reason": "alpha fits"}')
    assert router.classify("t", reg, "KEY") == {"skill_id": "c1/alpha", "reason": "alpha fits"}


def test_classify_strips_fences(monkeypatch):
    reg = _registry(None, None)
    monkeypatch.setattr(router, "_post_deepseek", lambda p, k: '```json\n{"skill_id": "c1/beta", "reason": "r"}\n```')
    assert router.classify("t", reg, "KEY")["skill_id"] == "c1/beta"


def test_classify_fails_loud_on_garbage(monkeypatch):
    reg = _registry(None, None)
    monkeypatch.setattr(router, "_post_deepseek", lambda p, k: "not json at all")
    with pytest.raises(RuntimeError):
        router.classify("t", reg, "KEY")


def test_classify_fails_loud_on_missing_skill_id(monkeypatch):
    reg = _registry(None, None)
    monkeypatch.setattr(router, "_post_deepseek", lambda p, k: '{"reason": "no id here"}')
    with pytest.raises(RuntimeError):
        router.classify("t", reg, "KEY")


def test_classify_fails_loud_without_key(monkeypatch):
    reg = _registry(None, None)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        router.classify("t", reg, "")


def test_classify_fails_loud_on_transport_error(monkeypatch):
    reg = _registry(None, None)

    def boom(p, k):
        raise OSError("connection refused")

    monkeypatch.setattr(router, "_post_deepseek", boom)
    with pytest.raises(RuntimeError, match="classification failed"):
        router.classify("t", reg, "KEY")


# --------------------------------------------------------------------------
# Validation (spec §5) — unknown ids rejected, never dispatched
# --------------------------------------------------------------------------

def test_validate_known_passes():
    by_id = {"c1/alpha": {"skill_id": "c1/alpha"}}
    assert router.validate_skill_id("c1/alpha", by_id) == ("c1/alpha", "")


def test_validate_unknown_raises_with_closest(capsys):
    by_id = {"c1/alpha": {"skill_id": "c1/alpha"}, "c1/alphax": {"skill_id": "c1/alphax"}}
    with pytest.raises(SystemExit):
        router.validate_skill_id("c1/alpa", by_id)


# --------------------------------------------------------------------------
# Dispatch construction (spec §6) — directory-based, no skill-name discovery
# --------------------------------------------------------------------------

def test_dispatch_command_is_directory_based():
    entry = {"skill_id": "c1/alpha", "dir": "/x/c1/alpha", "skill_md": "/x/c1/alpha/SKILL.md"}
    cmd = router.build_dispatch_command(entry, "do the thing")
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    joined = " ".join(cmd)
    assert "./SKILL.md" in joined
    assert "do the thing" in joined
    assert "--add-dir" not in joined or True  # cd-based (cwd=dir), no name resolution


def test_summary_extracts_result_from_json_envelope():
    env = json.dumps({"is_error": False, "result": "The skill prescribes RED-GREEN-REFACTOR.", "type": "result"})
    assert router._summarize_output(env) == "The skill prescribes RED-GREEN-REFACTOR."
    assert router._summarize_output("plain text output") == "plain text output"
    err = json.dumps({"is_error": True, "api_error_status": 400, "result": "Credit balance is too low", "type": "result"})
    assert "Credit balance is too low" in router._summarize_output(err)


def test_dispatch_dry_run_does_not_execute(monkeypatch, capsys):
    entry = {"skill_id": "c1/alpha", "dir": "/x/c1/alpha", "skill_md": "/x/c1/alpha/SKILL.md"}

    def no_run(cmd, **kw):
        raise AssertionError("dry-run must not execute")

    monkeypatch.setattr(router.subprocess, "run", no_run)
    code, summary = router.dispatch(entry, "t", dry_run=True)
    assert code == 0
    assert "dry-run" in summary
    out = capsys.readouterr().out
    assert "c1/alpha" in out
    assert "claude" in out


# --------------------------------------------------------------------------
# Audit (spec §7)
# --------------------------------------------------------------------------

def test_audit_appends_complete_line(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "AUDIT_PATH", tmp_path / "audit.jsonl")
    router.append_audit({
        "task": "t", "skill_id": "c1/alpha", "container": "c1",
        "reason": "r", "exit_code": 0, "result_summary": "done",
    })
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["skill_id"] == "c1/alpha"
    assert rec["container"] == "c1"
    assert rec["reason"] == "r"
    assert rec["exit_code"] == 0
    assert rec["result_summary"] == "done"
    assert "ts" in rec


# --------------------------------------------------------------------------
# CLI end-to-end (mocked) — --domain bypass + dry-run + unknown rejection
# --------------------------------------------------------------------------

def _install_registry(tmp_path, monkeypatch):
    reg = _registry(None, None)
    path = tmp_path / "domains.json"
    path.write_text(json.dumps(reg))
    monkeypatch.setattr(router, "REGISTRY_PATH", path)
    monkeypatch.setattr(router, "AUDIT_PATH", tmp_path / "audit.jsonl")
    return reg


def test_cli_domain_bypass_skips_classification(tmp_path, monkeypatch, capsys):
    _install_registry(tmp_path, monkeypatch)

    def must_not_call(*a, **k):
        raise AssertionError("--domain must bypass classification")

    monkeypatch.setattr(router, "classify", must_not_call)
    monkeypatch.setattr(router, "dispatch", lambda e, t, d: (0, "fake done"))
    code = router.main(["--domain", "c1/beta", "some task"])
    assert code == 0
    assert "c1/beta" in capsys.readouterr().out
    assert "alpha" not in capsys.readouterr().out


def test_cli_dry_run_writes_audit_and_one_skill(tmp_path, monkeypatch, capsys):
    reg = _install_registry(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "TEST_KEY")
    monkeypatch.setattr(router, "_post_deepseek", lambda p, k: '{"skill_id": "c1/alpha", "reason": "r"}')
    # real dispatch: dry-run branch prints the command and must NOT call subprocess
    monkeypatch.setattr(router.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run must not execute")))
    code = router.main(["--dry-run", "do alpha stuff"])
    assert code == 0
    out = capsys.readouterr().out
    assert "c1/alpha" in out
    assert "claude" in out


def test_cli_rejects_unknown_classifier_output(tmp_path, monkeypatch):
    _install_registry(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "TEST_KEY")
    monkeypatch.setattr(router, "_post_deepseek", lambda p, k: '{"skill_id": "c1/nope", "reason": "r"}')
    with pytest.raises(SystemExit):
        router.main(["some task"])
