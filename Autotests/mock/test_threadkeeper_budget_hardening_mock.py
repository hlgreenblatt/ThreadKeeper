"""Focused checks for ThreadKeeper budget/accounting file hardening."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import threadkeeper_budget as tb  # noqa: E402


def test_budget_config_read_rejects_symlink_source(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    config_path = tmp_path / "threadkeeper.config.yaml"
    outside = tmp_path / "outside-config.yaml"
    outside.write_text(
        "budget:\n  thread_token_ceiling: 7\n  min_local_iterations_before_escalation: 0\n",
        encoding="utf-8",
    )
    config_path.symlink_to(outside)

    tracker = tb.BudgetTracker(
        config_path=str(config_path),
        usage_log=str(tmp_path / "memory" / "usage.jsonl"),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )

    assert tracker.summary("default")["ceiling_tokens"] == 2_000_000


def test_budget_config_read_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "_MAX_BUDGET_CONFIG_BYTES", 64)
    config_path = tmp_path / "threadkeeper.config.yaml"
    config_path.write_text(
        "budget:\n  thread_token_ceiling: 7\n  min_local_iterations_before_escalation: 0\n"
        + ("#" * 128),
        encoding="utf-8",
    )

    tracker = tb.BudgetTracker(
        config_path=str(config_path),
        usage_log=str(tmp_path / "memory" / "usage.jsonl"),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )

    assert tracker.summary("default")["ceiling_tokens"] == 2_000_000


def test_budget_config_read_rechecks_size_after_open(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "_MAX_BUDGET_CONFIG_BYTES", 64)
    config_path = tmp_path / "threadkeeper.config.yaml"
    config_path.write_text(
        "budget:\n  thread_token_ceiling: 7\n  min_local_iterations_before_escalation: 0\n"
        + ("#" * 128),
        encoding="utf-8",
    )
    real_lstat = tb.os.lstat

    def small_lstat(path):
        st = real_lstat(path)
        if Path(path) == config_path:
            values = list(st)
            values[6] = 1
            return os.stat_result(values)
        return st

    monkeypatch.setattr(tb.os, "lstat", small_lstat)

    tracker = tb.BudgetTracker(
        config_path=str(config_path),
        usage_log=str(tmp_path / "memory" / "usage.jsonl"),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )

    assert tracker.summary("default")["ceiling_tokens"] == 2_000_000

def test_budget_metta_policy_rejects_symlink_source(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    policy_path = tmp_path / "escalation.metta"
    outside = tmp_path / "outside-escalation.metta"
    outside.write_text("; policy\n", encoding="utf-8")
    policy_path.symlink_to(outside)
    policy = tb._MettaPolicy(str(policy_path))
    monkeypatch.setattr(policy, "_import_petta", lambda: object())

    assert policy._ensure() is None


def test_budget_usage_log_rejects_symlink_target(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    usage_log = tmp_path / "memory" / "usage.jsonl"
    usage_log.parent.mkdir()
    outside = tmp_path / "outside-usage.jsonl"
    outside.write_text("", encoding="utf-8")
    usage_log.symlink_to(outside)

    tracker = tb.BudgetTracker(
        usage_log=str(usage_log),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )
    tracker.record("worker_loop", "unit", 3, 5)

    assert outside.read_text(encoding="utf-8") == ""
    assert usage_log.is_symlink()


def test_budget_escalation_log_rejects_symlink_target(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    escalation_log = tmp_path / "memory" / "escalations.jsonl"
    escalation_log.parent.mkdir()
    outside = tmp_path / "outside-escalations.jsonl"
    outside.write_text("", encoding="utf-8")
    escalation_log.symlink_to(outside)

    tracker = tb.BudgetTracker(
        usage_log=str(tmp_path / "memory" / "usage.jsonl"),
        escalation_log=str(escalation_log),
    )
    tracker.should_escalate(thread_id="default", subproblem_is_hard=False)

    assert outside.read_text(encoding="utf-8") == ""
    assert escalation_log.is_symlink()


def test_budget_usage_log_rejects_symlink_parent(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside_dir = tmp_path / "outside-memory"
    outside_dir.mkdir()
    memory_link = tmp_path / "memory"
    memory_link.symlink_to(outside_dir, target_is_directory=True)

    tracker = tb.BudgetTracker(
        usage_log=str(memory_link / "usage.jsonl"),
        escalation_log=str(tmp_path / "safe-memory" / "escalations.jsonl"),
    )
    tracker.record("worker_loop", "unit", 3, 5)

    assert memory_link.is_symlink()
    assert not (outside_dir / "usage.jsonl").exists()


def test_budget_escalation_log_rejects_symlink_parent(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside_dir = tmp_path / "outside-memory"
    outside_dir.mkdir()
    memory_link = tmp_path / "memory"
    memory_link.symlink_to(outside_dir, target_is_directory=True)

    tracker = tb.BudgetTracker(
        usage_log=str(tmp_path / "safe-memory" / "usage.jsonl"),
        escalation_log=str(memory_link / "escalations.jsonl"),
    )
    tracker.should_escalate(thread_id="default", subproblem_is_hard=False)

    assert memory_link.is_symlink()
    assert not (outside_dir / "escalations.jsonl").exists()


def test_budget_log_parent_creation_rejects_symlink_ancestor(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside_dir = tmp_path / "outside-memory"
    outside_dir.mkdir()
    memory_link = tmp_path / "memory-link"
    memory_link.symlink_to(outside_dir, target_is_directory=True)

    tracker = tb.BudgetTracker(
        usage_log=str(memory_link / "nested" / "usage.jsonl"),
        escalation_log=str(tmp_path / "safe-memory" / "escalations.jsonl"),
    )
    tracker.record("worker_loop", "unit", 3, 5)

    assert memory_link.is_symlink()
    assert not (outside_dir / "nested" / "usage.jsonl").exists()


def test_budget_usage_log_read_rejects_symlink_source(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    usage_log = tmp_path / "memory" / "usage.jsonl"
    usage_log.parent.mkdir()
    outside = tmp_path / "outside-usage.jsonl"
    outside.write_text(
        json.dumps({"thread_id": "default", "input_tokens": 100, "output_tokens": 23}) + "\n",
        encoding="utf-8",
    )
    usage_log.symlink_to(outside)

    tracker = tb.BudgetTracker(
        usage_log=str(usage_log),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )

    assert tracker.spent_tokens("default") == 0


def test_budget_usage_log_read_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "_MAX_BUDGET_LOG_BYTES", 64)
    usage_log = tmp_path / "memory" / "usage.jsonl"
    usage_log.parent.mkdir()
    usage_log.write_text(
        json.dumps({"thread_id": "default", "input_tokens": 100, "output_tokens": 23})
        + "\n"
        + ("x" * 128),
        encoding="utf-8",
    )

    tracker = tb.BudgetTracker(
        usage_log=str(usage_log),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )

    assert tracker.spent_tokens("default") == 0


def test_budget_usage_log_read_rechecks_size_after_open(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "_MAX_BUDGET_LOG_BYTES", 64)
    usage_log = tmp_path / "memory" / "usage.jsonl"
    usage_log.parent.mkdir()
    usage_log.write_text(
        json.dumps({"thread_id": "default", "input_tokens": 100, "output_tokens": 23})
        + "\n"
        + ("x" * 128),
        encoding="utf-8",
    )
    real_lstat = tb.os.lstat

    def small_lstat(path):
        st = real_lstat(path)
        if Path(path) == usage_log:
            values = list(st)
            values[6] = 1
            return os.stat_result(values)
        return st

    monkeypatch.setattr(tb.os, "lstat", small_lstat)

    tracker = tb.BudgetTracker(
        usage_log=str(usage_log),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )

    assert tracker.spent_tokens("default") == 0

def test_budget_usage_and_escalation_appends_are_fsynced(tmp_path, monkeypatch):
    fsync_calls = []
    real_fsync = tb.os.fsync

    def recording_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(tb.os, "fsync", recording_fsync)
    tracker = tb.BudgetTracker(
        usage_log=str(tmp_path / "memory" / "usage.jsonl"),
        escalation_log=str(tmp_path / "memory" / "escalations.jsonl"),
    )

    tracker.record("worker_loop", "unit", 3, 5)
    tracker.should_escalate(thread_id="default", subproblem_is_hard=False)

    assert (tmp_path / "memory" / "usage.jsonl").exists()
    assert (tmp_path / "memory" / "escalations.jsonl").exists()
    assert len(fsync_calls) >= 2
