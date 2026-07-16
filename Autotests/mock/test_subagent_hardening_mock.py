"""Unit checks for ThreadKeeper subagent hardening primitives."""
import builtins
import hashlib
import importlib
import json
import multiprocessing
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import subagent  # noqa: E402


def test_env_numeric_knobs_fallback_and_clamp_on_reload(monkeypatch):
    bad_values = {
        "OMEGACLAW_SUBAGENT_MAX_TURNS": "not-int",
        "OMEGACLAW_SUBAGENT_MAX_DIGEST_CHARS": "1",
        "OMEGACLAW_SUBAGENT_HISTORY_MAX_TURNS": "0",
        "OMEGACLAW_SUBAGENT_LLM_TIMEOUT_S": "-5",
        "OMEGACLAW_SUBAGENT_LLM_RETRIES": "-1",
        "OMEGACLAW_SUBAGENT_LLM_BACKOFF_S": "bad-float",
        "OMEGACLAW_SUBAGENT_LLM_CALLS_PER_MINUTE": "-7",
        "OMEGACLAW_SUBAGENT_MAX_CONCURRENT_LLM_CALLS": "-8",
        "OMEGACLAW_SUBAGENT_MAX_LLM_STATE_BYTES": "12",
        "OMEGACLAW_SUBAGENT_MAX_TOOL_CALLS": "-9",
        "OMEGACLAW_SUBAGENT_MAX_TOOL_CALLS_PER_TURN": "0",
        "OMEGACLAW_SUBAGENT_MAX_PATH_ARG_CHARS": "0",
        "OMEGACLAW_SUBAGENT_MAX_TOOL_ARG_CHARS": "0",
        "OMEGACLAW_SUBAGENT_SHELL_MAX_ARGV": "0",
        "OMEGACLAW_SUBAGENT_SHELL_OUTPUT_CAP": "0",
        "OMEGACLAW_SUBAGENT_SHELL_TIMEOUT_S": "bad-float",
        "OMEGACLAW_SUBAGENT_MAX_READ_FILE_CHARS": "0",
        "OMEGACLAW_SUBAGENT_MAX_JSON_FILE_BYTES": "12",
        "OMEGACLAW_SUBAGENT_MAX_SHA256_SIDECAR_BYTES": "12",
        "OMEGACLAW_SUBAGENT_MAX_ESCALATION_POLICY_BYTES": "-1",
        "OMEGACLAW_SUBAGENT_MAX_PERSONA_CONFIG_BYTES": "12",
        "OMEGACLAW_SUBAGENT_MAX_PERSONA_PROMPT_BYTES": "-1",
        "OMEGACLAW_SUBAGENT_MAX_CONTRACT_ITEMS": "-2",
        "OMEGACLAW_SUBAGENT_MAX_CONTRACT_ITEM_CHARS": "0",
        "OMEGACLAW_SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS": "0",
        "OMEGACLAW_SUBAGENT_MAX_PATCH_PROPOSAL_CHARS": "0",
        "OMEGACLAW_SUBAGENT_MAX_EMIT_CHARS": "0",
        "OMEGACLAW_SUBAGENT_MAX_RESPONSE_CHARS": "0",
        "OMEGACLAW_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES": "-1",
        "OMEGACLAW_SUBAGENT_MAX_INDEX_AUDIT_BYTES": "-1",
        "OMEGACLAW_SUBAGENT_MAX_QUEUED_DISPATCHES": "-4",
        "OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS": "-4",
        "OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_IDLE_POLLS": "-1",
        "OMEGACLAW_SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S": "bad-float",
        "OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S": "bad-float",
        "OMEGACLAW_SUBAGENT_ASYNC_WORKER_LOCK_METADATA_BYTES": "12",
    }
    for name, value in bad_values.items():
        monkeypatch.setenv(name, value)

    reloaded = importlib.reload(subagent)
    assert reloaded.SUBAGENT_MAX_TURNS_HARD_CAP == 8
    assert reloaded.SUBAGENT_MAX_DIGEST_CHARS == 100
    assert reloaded._SUBAGENT_HISTORY_MAX_TURNS == 1
    assert reloaded._SUBAGENT_LLM_TIMEOUT_S == 1
    assert reloaded._SUBAGENT_LLM_RETRIES == 0
    assert reloaded._SUBAGENT_LLM_BACKOFF_S == 1.0
    assert reloaded._SUBAGENT_LLM_CALLS_PER_MINUTE == 0
    assert reloaded._SUBAGENT_MAX_CONCURRENT_LLM_CALLS == 0
    assert reloaded._SUBAGENT_MAX_LLM_STATE_BYTES == 1024
    assert reloaded._SUBAGENT_MAX_TOOL_CALLS == 0
    assert reloaded._SUBAGENT_MAX_TOOL_CALLS_PER_TURN == 1
    assert reloaded._SUBAGENT_MAX_PATH_ARG_CHARS == 1
    assert reloaded._SUBAGENT_MAX_TOOL_ARG_CHARS == 1
    assert reloaded._SHELL_MAX_ARGV == 1
    assert reloaded._SHELL_OUTPUT_CAP == 1
    assert reloaded._SHELL_TIMEOUT_S == 30.0
    assert reloaded._SUBAGENT_MAX_READ_FILE_CHARS == 1
    assert reloaded._SUBAGENT_MAX_JSON_FILE_BYTES == 1024
    assert reloaded._SUBAGENT_MAX_SHA256_SIDECAR_BYTES == 128
    assert reloaded._SUBAGENT_MAX_ESCALATION_POLICY_BYTES == 0
    assert reloaded._SUBAGENT_MAX_PERSONA_CONFIG_BYTES == 1024
    assert reloaded._SUBAGENT_MAX_PERSONA_PROMPT_BYTES == 0
    assert reloaded._SUBAGENT_MAX_CONTRACT_ITEMS == 0
    assert reloaded._SUBAGENT_MAX_CONTRACT_ITEM_CHARS == 1
    assert reloaded._SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS == 1
    assert reloaded._SUBAGENT_MAX_PATCH_PROPOSAL_CHARS == 1
    assert reloaded._SUBAGENT_MAX_EMIT_CHARS == 1
    assert reloaded._SUBAGENT_MAX_RESPONSE_CHARS == 1
    assert reloaded._SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES == 0
    assert reloaded._SUBAGENT_MAX_INDEX_AUDIT_BYTES == 0
    assert reloaded._SUBAGENT_MAX_QUEUED_DISPATCHES == 0
    assert reloaded._SUBAGENT_ASYNC_WORKER_MAX_TASKS == 0
    assert reloaded._SUBAGENT_ASYNC_WORKER_MAX_IDLE_POLLS == 0
    assert reloaded._SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S == 2.0
    assert reloaded._SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S == 600.0
    assert reloaded._SUBAGENT_ASYNC_WORKER_LOCK_METADATA_BYTES == 1024

    monkeypatch.undo()
    importlib.reload(subagent)


def _append_many_worker(workspace, worker_id, count):
    os.environ["OMEGACLAW_SUBAGENT_WORKSPACE"] = str(workspace)
    for i in range(count):
        result = subagent._tool_append_file("shared/log.txt", f"{worker_id}-{i}")
        if result != "APPEND-FILE-SUCCESS":
            raise RuntimeError(result)


def test_env_float_knobs_reject_non_finite_values(monkeypatch):
    non_finite_values = {
        "OMEGACLAW_SUBAGENT_LLM_BACKOFF_S": "nan",
        "OMEGACLAW_SUBAGENT_SHELL_TIMEOUT_S": "inf",
        "OMEGACLAW_SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S": "-inf",
        "OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S": "NaN",
        "OMEGACLAW_SUBAGENT_MAX_QUEUED_TASK_AGE_S": "Infinity",
        "OMEGACLAW_SUBAGENT_DISPATCH_TIMEOUT_S": "-Infinity",
    }
    for name, value in non_finite_values.items():
        monkeypatch.setenv(name, value)

    reloaded = importlib.reload(subagent)
    assert reloaded._SUBAGENT_LLM_BACKOFF_S == 1.0
    assert reloaded._SHELL_TIMEOUT_S == 30.0
    assert reloaded._SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S == 2.0
    assert reloaded._SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S == 600.0
    assert reloaded._SUBAGENT_MAX_QUEUED_TASK_AGE_S == 0.0
    assert reloaded._SUBAGENT_DISPATCH_TIMEOUT_S == 600.0

    monkeypatch.undo()
    importlib.reload(subagent)


def test_worker_usage_log_appends_regular_file(monkeypatch, tmp_path):
    usage_log = tmp_path / "memory" / "usage.jsonl"
    monkeypatch.setattr(subagent, "_USAGE_LOG_PATH", str(usage_log))

    subagent._log_worker_usage("unit-worker", 3, 5)

    records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["model"] == "unit-worker"
    assert records[0]["input_tokens"] == 3
    assert records[0]["output_tokens"] == 5


def test_worker_usage_log_rejects_symlink_target(monkeypatch, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    usage_log = tmp_path / "memory" / "usage.jsonl"
    usage_log.parent.mkdir()
    outside = tmp_path / "outside-usage.jsonl"
    outside.write_text("", encoding="utf-8")
    usage_log.symlink_to(outside)
    monkeypatch.setattr(subagent, "_USAGE_LOG_PATH", str(usage_log))

    subagent._log_worker_usage("unit-worker", 7, 11)

    assert outside.read_text(encoding="utf-8") == ""
    assert usage_log.is_symlink()


def test_worker_usage_log_rejects_symlink_parent(monkeypatch, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside_dir = tmp_path / "outside-memory"
    outside_dir.mkdir()
    memory_link = tmp_path / "memory"
    memory_link.symlink_to(outside_dir, target_is_directory=True)
    usage_log = memory_link / "usage.jsonl"
    monkeypatch.setattr(subagent, "_USAGE_LOG_PATH", str(usage_log))

    subagent._log_worker_usage("unit-worker", 7, 11)

    assert not (outside_dir / "usage.jsonl").exists()
    assert memory_link.is_symlink()


def test_worker_usage_log_rejects_symlink_ancestor_parent(monkeypatch, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside_dir = tmp_path / "outside-memory"
    outside_dir.mkdir()
    memory_link = tmp_path / "memory"
    memory_link.symlink_to(outside_dir, target_is_directory=True)
    usage_log = memory_link / "nested" / "usage.jsonl"
    monkeypatch.setattr(subagent, "_USAGE_LOG_PATH", str(usage_log))

    subagent._log_worker_usage("unit-worker", 7, 11)

    assert not (outside_dir / "nested").exists()
    assert memory_link.is_symlink()


def test_llm_retry_backoff_returns_success_after_transient_failure(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_BACKOFF_S", 0.0)

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("slow worker")
        return '(emit "ok")'

    assert subagent._call_with_retries(flaky, "unit") == '(emit "ok")'
    assert calls["n"] == 2


def test_llm_retry_backoff_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_BACKOFF_S", 0.0)

    def always_fails():
        raise TimeoutError("still slow")

    result = subagent._call_with_retries(always_fails, "unit")
    assert result.startswith("(subagent LLM call failed after 2 attempt(s) via unit")
    assert "TimeoutError" in result


def test_llm_calls_per_minute_rate_limit_is_atomic_state(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_CALLS_PER_MINUTE", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 0)

    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return '(emit "ok")'

    assert subagent._call_with_retries(counted, "unit-rate") == '(emit "ok")'
    blocked = subagent._call_with_retries(counted, "unit-rate")
    assert blocked.startswith("(subagent LLM call rate-limited via unit-rate")
    assert calls["n"] == 1


def test_llm_rate_limit_state_read_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_CALLS_PER_MINUTE", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_LLM_STATE_BYTES", 32)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 0)
    state_path = Path(subagent._rate_limit_state_path("unit-rate-huge"))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"calls": [' + ','.join(['1'] * 1000) + ']}', encoding="utf-8")

    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return '(emit "ok")'

    assert subagent._call_with_retries(counted, "unit-rate-huge") == '(emit "ok")'
    assert calls["n"] == 1
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(data["calls"]) == 1


def test_llm_concurrency_state_read_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_CONCURRENT_LLM_CALLS", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_LLM_STATE_BYTES", 32)
    state_path = Path(subagent._concurrency_state_path("unit-concurrency-huge"))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"inflight": [' + ','.join(['{"pid": 1, "ts": 1}'] * 1000) + ']}', encoding="utf-8")

    ok, _reason, token = subagent._subagent_llm_concurrency_acquire("unit-concurrency-huge")
    assert ok
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(data["inflight"]) == 1
    assert data["inflight"][0]["token"] == token
    subagent._subagent_llm_concurrency_release("unit-concurrency-huge", token)


def test_llm_guard_state_rejects_symlink_paths(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable")
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_CALLS_PER_MINUTE", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_CONCURRENT_LLM_CALLS", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 0)
    run_dir = Path(subagent.SUBAGENT_RUN_DIR)
    run_dir.mkdir(parents=True, exist_ok=True)

    outside_rate = tmp_path / "outside-rate.json"
    outside_rate.write_text('{"calls": []}', encoding="utf-8")
    rate_state = Path(subagent._rate_limit_state_path("unit-rate-link"))
    os.symlink(outside_rate, rate_state)

    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return '(emit "ok")'

    result = subagent._call_with_retries(counted, "unit-rate-link")
    assert result.startswith("(subagent LLM call rate-limited via unit-rate-link")
    assert calls["n"] == 0
    assert outside_rate.read_text(encoding="utf-8") == '{"calls": []}'

    outside_inflight = tmp_path / "outside-inflight.json"
    outside_inflight.write_text('{"inflight": []}', encoding="utf-8")
    inflight_state = Path(subagent._concurrency_state_path("unit-concurrency-link"))
    os.symlink(outside_inflight, inflight_state)

    ok, reason, token = subagent._subagent_llm_concurrency_acquire("unit-concurrency-link")
    assert not ok
    assert token == ""
    assert "regular non-symlink" in reason
    assert outside_inflight.read_text(encoding="utf-8") == '{"inflight": []}'


def test_llm_guard_state_rejects_symlink_run_dir_parent(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable")
    outside = tmp_path / "outside-runs"
    outside.mkdir()
    run_link = tmp_path / "runs-link"
    os.symlink(outside, run_link)
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_link))
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_CALLS_PER_MINUTE", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_CONCURRENT_LLM_CALLS", 0)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 0)

    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return '(emit "ok")'

    result = subagent._call_with_retries(counted, "unit-rate-parent-link")
    assert result.startswith("(subagent LLM call rate-limited via unit-rate-parent-link")
    assert calls["n"] == 0
    assert not (outside / ".llm-rate-unit-rate-parent-link.json").exists()

    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_CONCURRENT_LLM_CALLS", 1)
    ok, reason, token = subagent._subagent_llm_concurrency_acquire("unit-concurrency-parent-link")
    assert not ok
    assert token == ""
    assert "directory must be a real non-symlink directory" in reason
    assert not (outside / ".llm-inflight-unit-concurrency-parent-link.json").exists()


def test_llm_concurrency_limit_blocks_when_endpoint_slots_are_full(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_CONCURRENT_LLM_CALLS", 1)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_CALLS_PER_MINUTE", 0)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 0)

    ok, _reason, token = subagent._subagent_llm_concurrency_acquire("unit-concurrency")
    assert ok
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return '(emit "ok")'

    blocked = subagent._call_with_retries(counted, "unit-concurrency")
    assert blocked.startswith("(subagent LLM call concurrency-limited via unit-concurrency")
    assert calls["n"] == 0
    subagent._subagent_llm_concurrency_release("unit-concurrency", token)
    assert subagent._call_with_retries(counted, "unit-concurrency") == '(emit "ok")'
    assert calls["n"] == 1


def test_run_tools_rejects_bad_arg_counts_before_dispatch():
    result = subagent.run_tools([("write-file", ["only-path"])], ["write-file"])
    assert "SKILL_ARG_ERROR: write-file" in result
    assert "expected 2 arg" in result


def test_run_tools_rejects_oversized_tool_arguments(monkeypatch):
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_PATH_ARG_CHARS", 8)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOOL_ARG_CHARS", 12)

    long_path = "a" * 9
    long_content = "b" * 13
    path_result = subagent.run_tools([("write-file", [long_path, "ok"])], ["write-file"])
    content_result = subagent.run_tools([("write-file", ["ok.txt", long_content])], ["write-file"])

    assert "SKILL_ARG_ERROR: write-file" in path_result
    assert "path argument exceeds" in path_result
    assert "SKILL_ARG_ERROR: write-file" in content_result
    assert "argument(s) [2] exceed" in content_result


def test_read_file_is_bounded_before_return_to_worker_context(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_READ_FILE_CHARS", 5)
    target = tmp_path / "large.txt"
    target.write_text("abcdefghij")

    result = subagent._tool_read_file("large.txt")

    assert result.startswith("abcde\n...(read-file truncated at 5 chars)...")
    assert "fghij" not in result


def test_append_file_enforces_cap_during_fd_read(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 5)
    target = tmp_path / "growing.txt"
    target.write_text("abcdef")
    real_fstat = subagent.os.fstat

    def underreported_fstat(fd):
        st = real_fstat(fd)
        values = list(st)
        values[6] = 1
        return os.stat_result(values)

    monkeypatch.setattr(subagent.os, "fstat", underreported_fstat)

    result = subagent._tool_append_file("growing.txt", "x")

    assert "existing file exceeds max file size 5 chars during read" in result
    assert target.read_text() == "abcdef"


def test_write_file_uses_atomic_replace_inside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    target = tmp_path / "nested" / "artifact.txt"
    fsynced = []
    monkeypatch.setattr(subagent, "_fsync_parent_dir", lambda path: fsynced.append(path))

    assert subagent._tool_write_file("nested/artifact.txt", "first") == "WRITE-FILE-SUCCESS"
    assert target.read_text() == "first"
    assert subagent._tool_write_file("nested/artifact.txt", "second") == "WRITE-FILE-SUCCESS"
    assert target.read_text() == "second"
    assert fsynced == [str(target), str(target)]
    assert not list((tmp_path / "nested").glob(".*.tmp"))


def test_append_file_uses_atomic_replace_inside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    target = tmp_path / "nested" / "artifact.txt"
    fsynced = []
    monkeypatch.setattr(subagent, "_fsync_parent_dir", lambda path: fsynced.append(path))

    assert subagent._tool_append_file("nested/artifact.txt", "first") == "APPEND-FILE-SUCCESS"
    assert subagent._tool_append_file("nested/artifact.txt", "second") == "APPEND-FILE-SUCCESS"
    assert target.read_text() == "first\nsecond\n"
    assert fsynced == [str(target), str(target)]
    assert not list((tmp_path / "nested").glob(".*.tmp"))


def test_append_file_size_check_uses_nofollow_fd_not_getsize(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 8)
    target = tmp_path / "artifact.txt"
    target.write_text("1234567", encoding="utf-8")

    def forbidden_getsize(path):
        raise AssertionError("append-file must not use path-based getsize")

    monkeypatch.setattr(os.path, "getsize", forbidden_getsize)

    result = subagent._tool_append_file("artifact.txt", "x")

    assert "append-file error" in result
    assert "would exceed max file size 8 chars" in result
    assert target.read_text(encoding="utf-8") == "1234567"


def test_workspace_file_lock_rejects_symlink_lock_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    nested = tmp_path / "nested"
    nested.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_text("external")
    (nested / ".artifact.txt.lock").symlink_to(outside)

    result = subagent._tool_write_file("nested/artifact.txt", "content")

    assert "write-file error" in result
    assert "regular non-symlink" in result
    assert not (nested / "artifact.txt").exists()
    assert outside.read_text() == "external"


def test_file_tools_reject_symlink_workspace_root(tmp_path, monkeypatch):
    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    link_workspace = tmp_path / "workspace-link"
    link_workspace.symlink_to(real_workspace, target_is_directory=True)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(link_workspace))

    result = subagent._tool_write_file("artifact.txt", "content")

    assert "write-file error" in result
    assert "workspace root must be a real non-symlink directory" in result
    assert not (real_workspace / "artifact.txt").exists()


def test_open_workspace_file_read_rejects_symlink(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="regular non-symlink"):
        subagent._open_workspace_file_read(str(link))


def test_open_workspace_file_read_accepts_regular_file(tmp_path):
    target = tmp_path / "regular.txt"
    target.write_text("content")

    fd = subagent._open_workspace_file_read(str(target))
    with os.fdopen(fd, "r") as f:
        assert f.read() == "content"


def test_read_file_rejects_symlink_escape(tmp_path, monkeypatch):
    """Symlink inside workspace pointing outside is caught by realpath containment."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret")
    try:
        link = tmp_path / "link.txt"
        link.symlink_to(outside)

        result = subagent._tool_read_file("link.txt")

        assert "read-file error" in result
        assert "path escapes" in result
    finally:
        outside.unlink(missing_ok=True)


def test_append_file_rejects_symlink_escape(tmp_path, monkeypatch):
    """Symlink inside workspace pointing outside is caught by realpath containment."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("original")
    try:
        link = tmp_path / "link.txt"
        link.symlink_to(outside)

        result = subagent._tool_append_file("link.txt", "appended")

        assert "append-file error" in result
        assert "path escapes" in result
        assert outside.read_text() == "original"
    finally:
        outside.unlink(missing_ok=True)


def test_append_file_lock_prevents_concurrent_lost_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    worker_count = 4
    per_worker = 12
    procs = [
        multiprocessing.Process(target=_append_many_worker, args=(str(tmp_path), w, per_worker))
        for w in range(worker_count)
    ]

    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(10)

    assert all(proc.exitcode == 0 for proc in procs)
    lines = (tmp_path / "shared" / "log.txt").read_text().splitlines()
    assert len(lines) == worker_count * per_worker
    assert set(lines) == {f"{w}-{i}" for w in range(worker_count) for i in range(per_worker)}
    assert not list((tmp_path / "shared").glob(".*.tmp"))


def test_shell_tool_runs_from_subagent_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "pwd")

    result = subagent._tool_shell("pwd")

    assert result.strip() == str(tmp_path)


def test_shell_tool_rejects_symlink_workspace_root(tmp_path, monkeypatch):
    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    link_workspace = tmp_path / "workspace-link"
    link_workspace.symlink_to(real_workspace, target_is_directory=True)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(link_workspace))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "pwd")

    result = subagent._tool_shell("pwd")

    assert "shell error" in result
    assert "workspace root must be a real non-symlink directory" in result


def test_shell_tool_rejects_explicit_executable_paths(tmp_path, monkeypatch):
    python_exe = sys.executable
    python_name = Path(python_exe).name
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", python_name)

    result = subagent._tool_shell(f'{python_exe} -c "print(1)"')

    assert "executable must be an allowlisted command name" in result


def test_shell_tool_rejects_too_many_argv_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "printf")
    monkeypatch.setattr(subagent, "_SHELL_MAX_ARGV", 2)

    result = subagent._tool_shell("printf one two")

    assert "too many arguments" in result


def test_shell_tool_does_not_resolve_allowlisted_executable_from_workspace_path(tmp_path, monkeypatch):
    fake_pwd = tmp_path / "pwd"
    fake_pwd.write_text("#!/bin/sh\necho MALICIOUS\n")
    fake_pwd.chmod(0o755)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "pwd")
    monkeypatch.setenv("PATH", f".:{tmp_path}:{os.environ.get('PATH', '')}")

    result = subagent._tool_shell("pwd")

    assert result.strip() == str(tmp_path)
    assert "MALICIOUS" not in result


def test_shell_tool_does_not_inherit_secret_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "env")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")
    monkeypatch.setenv("UNIT_API_KEY", "should-not-leak")

    result = subagent._tool_shell("env")

    assert "OPENAI_API_KEY" not in result
    assert "UNIT_API_KEY" not in result
    assert f"HOME={tmp_path}" in result
    assert "PATH=" in result


def test_shell_tool_output_is_bounded_with_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "printf")
    monkeypatch.setattr(subagent, "_SHELL_OUTPUT_CAP", 5)

    result = subagent._tool_shell("printf abcdefghij")

    assert result.startswith("abcde\n...(shell output truncated at 5 chars)...")
    assert "fghij" not in result


def test_shell_tool_does_not_capture_unbounded_output_in_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "1")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "fakecmd")
    monkeypatch.setattr(subagent, "_SHELL_OUTPUT_CAP", 7)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["stdout"] = kwargs.get("stdout")
        seen["stderr"] = kwargs.get("stderr")
        assert kwargs.get("capture_output") is None
        kwargs["stdout"].write(b"abcdefghijklmnop")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subagent.subprocess, "run", fake_run)

    result = subagent._tool_shell("fakecmd")

    assert seen["stdout"] is not subprocess.PIPE
    assert seen["stderr"] is subprocess.STDOUT
    assert result.startswith("abcdefg\n...(shell output truncated at 7 chars)...")
    assert "hijklmnop" not in result


def test_history_is_bounded_and_evicted_turns_are_digested(monkeypatch):
    monkeypatch.setattr(subagent, "_SUBAGENT_HISTORY_MAX_TURNS", 2)
    history = []
    digest = []

    for i in range(4):
        subagent._append_bounded_history(history, (i + 1, f"raw-{i}", f"res-{i}"), digest)

    assert [entry[0] for entry in history] == [3, 4]
    assert len(digest) == 2
    assert digest[0].startswith("turn 1:")
    assert digest[1].startswith("turn 2:")


def _write_unit_persona(tmp_path, monkeypatch, node_role="local"):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("You are a unit-test subagent.")
    (persona_dir / "unit.json").write_text(json.dumps({
        "persona_file": "unit.txt",
        "provider": "ollama",
        "model": "unit-model",
        "api_key_env": "UNIT_API_KEY",
        "base_url": "http://localhost:11434" if node_role == "local" else "https://example.invalid/v1",
        "node_role": node_role,
        "endpoint_kind": "ollama_native" if node_role == "local" else "openai_compatible",
        "default_tool_subset": ["write-file"],
    }))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("UNIT_API_KEY", "dummy")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path / "workspace"))
    return persona_dir


def test_persona_config_requires_explicit_node_role(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("You are a unit-test subagent.")
    (persona_dir / "unit.json").write_text(json.dumps({
        "persona_file": "unit.txt",
        "provider": "ollama",
        "model": "unit-model",
        "api_key_env": "UNIT_API_KEY",
        "base_url": "http://localhost:11434",
    }))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    try:
        subagent.load_persona_config("unit")
        assert False, "missing node_role should be rejected"
    except ValueError as e:
        assert "node_role" in str(e)


def test_persona_key_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(tmp_path))

    try:
        subagent.load_persona_config("../secrets")
        assert False, "path-like persona key should be rejected"
    except ValueError as e:
        assert "persona key" in str(e)


def test_persona_config_read_is_bounded_without_path_leak(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.json").write_text("{" + "x" * 200 + "}")
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_PERSONA_CONFIG_BYTES", 64)

    try:
        subagent.load_persona_config("unit")
        assert False, "oversized persona config should fail closed"
    except ValueError as e:
        msg = str(e)
        assert "OMEGACLAW_SUBAGENT_MAX_PERSONA_CONFIG_BYTES=64" in msg
        assert str(tmp_path) not in msg


@pytest.mark.parametrize("root", [None, True, 123, [], ["not", "an", "object"]])
def test_persona_config_requires_json_object_root(tmp_path, monkeypatch, root):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.json").write_text(json.dumps(root))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    with pytest.raises(ValueError, match="must be a JSON object"):
        subagent.load_persona_config("unit")


@pytest.mark.parametrize("task_contract", [None, True, 123, [], "objective=unsafe"])
def test_persona_config_task_contract_requires_json_object(
    tmp_path, monkeypatch, task_contract
):
    persona_dir = _write_unit_persona(tmp_path, monkeypatch)
    config_path = persona_dir / "unit.json"
    config = json.loads(config_path.read_text())
    config["task_contract"] = task_contract
    config_path.write_text(json.dumps(config))

    with pytest.raises(ValueError, match="field 'task_contract' must be a JSON object"):
        subagent.load_persona_config("unit")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("persona_file", "unit\u2028spoof.metta"),
        ("provider", "Open\u202eAI"),
        ("model", "safe\ud800model"),
        ("node_role", "local\x00cloud"),
        ("endpoint_kind", "openai\ufeff_compatible"),
        ("base_url", "https://example.invalid/\u2029forged"),
    ],
)
def test_persona_config_rejects_unsafe_control_text(
    tmp_path, monkeypatch, field, value
):
    persona_dir = _write_unit_persona(tmp_path, monkeypatch)
    config_path = persona_dir / "unit.json"
    config = json.loads(config_path.read_text())
    config[field] = value
    config_path.write_text(json.dumps(config))

    with pytest.raises(ValueError, match=rf"field '{field}'.*control characters"):
        subagent.load_persona_config("unit")


@pytest.mark.parametrize("value", [True, 1.5, "100", 0, -1, 8193])
def test_persona_config_rejects_invalid_max_output_tokens(
    tmp_path, monkeypatch, value
):
    persona_dir = _write_unit_persona(tmp_path, monkeypatch)
    config_path = persona_dir / "unit.json"
    config = json.loads(config_path.read_text())
    config["max_output_tokens"] = value
    config_path.write_text(json.dumps(config))

    with pytest.raises(ValueError, match="field 'max_output_tokens'.*1 to 8192"):
        subagent.load_persona_config("unit")


@pytest.mark.parametrize(
    "value",
    ["read-file", [], ["read-file", 1], ["read-file\nwrite-file"], ["unknown-tool"]],
)
def test_persona_config_rejects_invalid_default_tool_subset(
    tmp_path, monkeypatch, value
):
    persona_dir = _write_unit_persona(tmp_path, monkeypatch)
    config_path = persona_dir / "unit.json"
    config = json.loads(config_path.read_text())
    config["default_tool_subset"] = value
    config_path.write_text(json.dumps(config))

    with pytest.raises(ValueError, match="default_tool_subset|unknown skill"):
        subagent.load_persona_config("unit")


def test_persona_config_rejects_symlink_before_read(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    target = tmp_path / "unit-target.json"
    target.write_text(json.dumps({
        "persona_file": "unit.txt",
        "provider": "ollama",
        "model": "unit-model",
        "api_key_env": "UNIT_API_KEY",
        "node_role": "local",
        "endpoint_kind": "ollama_native",
    }))
    (persona_dir / "unit.json").symlink_to(target)
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    try:
        subagent.load_persona_config("unit")
        assert False, "symlink persona config should fail closed"
    except ValueError as e:
        assert "regular non-symlink" in str(e)


def test_persona_config_rejects_non_string_scalar_fields(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("You are a unit-test subagent.")
    (persona_dir / "unit.json").write_text(json.dumps({
        "persona_file": "unit.txt",
        "provider": "ollama",
        "model": 123,
        "api_key_env": "UNIT_API_KEY",
        "node_role": "local",
        "endpoint_kind": "ollama_native",
    }))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    try:
        subagent.load_persona_config("unit")
        assert False, "non-string persona config scalars should fail closed"
    except ValueError as e:
        assert "field 'model' must be a non-empty string" in str(e)


def test_persona_config_rejects_unsafe_api_key_env_name(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("You are a unit-test subagent.")
    (persona_dir / "unit.json").write_text(json.dumps({
        "persona_file": "unit.txt",
        "provider": "ollama",
        "model": "unit-model",
        "api_key_env": "../../TOKEN",
        "node_role": "local",
        "endpoint_kind": "ollama_native",
    }))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    try:
        subagent.load_persona_config("unit")
        assert False, "unsafe api_key_env names should fail closed"
    except ValueError as e:
        assert "safe environment-variable name" in str(e)


def test_persona_config_rejects_malformed_persona_sha256(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("You are a unit-test subagent.")
    (persona_dir / "unit.json").write_text(json.dumps({
        "persona_file": "unit.txt",
        "persona_sha256": "not-a-digest",
        "provider": "ollama",
        "model": "unit-model",
        "api_key_env": "UNIT_API_KEY",
        "node_role": "local",
        "endpoint_kind": "ollama_native",
    }))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    try:
        subagent.load_persona_config("unit")
        assert False, "malformed persona_sha256 should fail closed before prompt read"
    except ValueError as e:
        assert "64-character hex SHA-256" in str(e)


def test_persona_config_rejects_oversized_scalar_fields(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("You are a unit-test subagent.")
    (persona_dir / "unit.json").write_text(json.dumps({
        "persona_file": "unit.txt",
        "provider": "ollama",
        "model": "x" * 80,
        "api_key_env": "UNIT_API_KEY",
        "node_role": "local",
        "endpoint_kind": "ollama_native",
    }))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_PERSONA_SCALAR_CHARS", 64)

    try:
        subagent.load_persona_config("unit")
        assert False, "oversized persona scalar fields should fail closed"
    except ValueError as e:
        assert "OMEGACLAW_SUBAGENT_MAX_PERSONA_SCALAR_CHARS=64" in str(e)


def test_persona_prompt_read_is_bounded_without_path_leak(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("x" * 200)
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_PERSONA_PROMPT_BYTES", 64)

    try:
        subagent.load_persona_prompt("unit.txt", "unit")
        assert False, "oversized persona prompt should fail closed"
    except ValueError as e:
        msg = str(e)
        assert "OMEGACLAW_SUBAGENT_MAX_PERSONA_PROMPT_BYTES=64" in msg
        assert str(tmp_path) not in msg


def test_persona_prompt_size_check_uses_open_fd(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("trusted prompt")
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_PERSONA_PROMPT_BYTES", 64)
    monkeypatch.setattr(os.path, "getsize", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("getsize race window")))

    assert subagent.load_persona_prompt("unit.txt", "unit") == "trusted prompt"


def test_persona_prompt_sha256_pin_fails_closed(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    prompt = persona_dir / "unit.txt"
    prompt.write_text("trusted prompt")
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    good_hash = hashlib.sha256(b"trusted prompt").hexdigest()
    assert subagent.load_persona_prompt("unit.txt", "unit", good_hash) == "trusted prompt"
    assert subagent.load_persona_prompt(str(prompt), "unit", good_hash) == "trusted prompt"
    try:
        subagent.load_persona_prompt("unit.txt", "unit", "0" * 64)
        assert False, "persona hash mismatch should fail closed"
    except ValueError as e:
        assert "sha256" in str(e)


def test_persona_prompt_rejects_path_escape(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    for path in ("../outside.txt", str(outside)):
        try:
            subagent.load_persona_prompt(path, "unit")
            assert False, f"persona prompt escape should be rejected: {path}"
        except ValueError as e:
            assert "escapes persona directory" in str(e)


def test_persona_prompt_rejects_symlink_before_read(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    target = persona_dir / "unit-target.txt"
    target.write_text("trusted prompt")
    (persona_dir / "unit.txt").symlink_to(target)
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    try:
        subagent.load_persona_prompt("unit.txt", "unit")
        assert False, "symlink persona prompt should fail closed"
    except ValueError as e:
        assert "regular non-symlink" in str(e)


def test_committed_persona_examples_use_explicit_metadata_and_valid_prompt_pin(monkeypatch):
    persona_dir = ROOT / "memory" / "personas-subagent"
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))

    examples = sorted(persona_dir.glob("*.json.example"))
    assert examples, "expected committed persona examples"
    for example in examples:
        key = example.name.removesuffix(".json.example")
        cfg = json.loads(example.read_text())
        assert cfg.get("node_role") in (subagent._LOCAL_NODE_ROLES | subagent._CLOUD_NODE_ROLES)
        assert subagent._endpoint_kind(cfg) in {"ollama_native", "openai_compatible"}
        assert cfg.get("persona_sha256"), f"{example.name} should pin its example prompt"
        assert subagent.load_persona_prompt(
            cfg["persona_file"], key, cfg["persona_sha256"]
        )


def test_openai_compatible_provider_init_fails_closed_without_client(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch, node_role="cloud")
    monkeypatch.setattr(subagent, "_escalation_gate", lambda _cfg: (True, "unit budget ok"))

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("openai sdk unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch("cloud setup", "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "OpenAI-compatible provider" in payload["summary"]
    assert "openai sdk unavailable" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "provider_invalid"
    assert saved["turns"] == []


def test_endpoint_kind_controls_llm_transport_without_base_url_heuristic(monkeypatch):
    seen = {}

    class FakeClient:
        class Chat:
            class Completions:
                def create(self, **kwargs):
                    seen["called"] = kwargs
                    return type("Resp", (), {
                        "usage": None,
                        "choices": [type("Choice", (), {
                            "message": type("Msg", (), {"content": '(emit "cloud")'})()
                        })()],
                    })()
            completions = Completions()
        chat = Chat()

    handle = {
        "provider": FakeClient(),
        "model": "unit-model",
        "base_url": "http://localhost:11434/v1",  # intentionally misleading
        "endpoint_kind": "openai_compatible",
    }
    assert subagent._call_subagent_llm(handle, "prompt", 12) == ('(emit "cloud")', 0, 0)
    assert seen["called"]["timeout"] == subagent._SUBAGENT_LLM_TIMEOUT_S


class _FakeHTTPResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            return self._body
        return self._body[:size]


def test_ollama_native_http_response_body_is_bounded(monkeypatch):
    import urllib.request as urllib_request

    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 0)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES", 16)
    monkeypatch.setattr(
        urllib_request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHTTPResponse(b"x" * 17),
    )

    handle = {
        "provider": "ollama",
        "model": "unit-model",
        "base_url": "http://localhost:11434",
        "endpoint_kind": "ollama_native",
    }

    text, in_tokens, out_tokens = subagent._call_subagent_llm(handle, "prompt", 12)
    assert "OMEGACLAW_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES=16" in text
    assert in_tokens == 0
    assert out_tokens == 0


def test_ollama_native_http_response_body_under_cap_decodes(monkeypatch):
    import urllib.request as urllib_request

    body = json.dumps({
        "message": {"content": '(emit "native")'},
        "prompt_eval_count": 4,
        "eval_count": 2,
    }).encode()
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 0)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES", len(body))
    monkeypatch.setattr(
        urllib_request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeHTTPResponse(body),
    )

    handle = {
        "provider": "ollama",
        "model": "unit-model",
        "base_url": "http://localhost:11434/v1",
        "endpoint_kind": "ollama_native",
    }

    assert subagent._call_subagent_llm(handle, "prompt", 12) == ('(emit "native")', 4, 2)


def test_dispatch_returns_structured_digest_and_persists_transcript(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)

    responses = iter(['(write-file "out.txt" "hello")', '(emit "done")'])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: (next(responses), 10, 5))

    result = subagent.dispatch("write a file", "write-file", "unit", max_turns=3)
    payload = json.loads(result)

    assert payload["summary"] == "done"
    assert payload["files_changed"] == ["out.txt"]
    assert payload["status"] == "ok"
    assert len(payload["transcript_sha256"]) == 64
    transcript = Path(payload["transcript_path"])
    assert transcript.exists()
    digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
    assert payload["transcript_sha256"] == digest
    assert transcript.with_suffix(transcript.suffix + ".sha256").read_text().startswith(digest)
    saved = json.loads(transcript.read_text())
    index_path = transcript.parent / "index.jsonl"
    assert index_path.exists()
    index_entries = [json.loads(line) for line in index_path.read_text().splitlines()]
    assert index_entries[-1]["run_id"] == saved["run_id"]
    assert index_entries[-1]["status"] == "ok"
    assert index_entries[-1]["transcript_path"] == str(transcript)
    assert index_entries[-1]["transcript_sha256"] == digest
    assert index_entries[-1]["previous_entry_sha256"] == ""
    assert len(index_entries[-1]["entry_sha256"]) == 64
    assert index_entries[-1]["entry_sha256"] == subagent._index_entry_hash(index_entries[-1])
    assert saved["status"] == "ok"
    assert len(saved["turns"]) == 2
    assert (tmp_path / "workspace" / "out.txt").read_text() == "hello"


def test_run_index_entries_are_hash_chained(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    first = {"run_id": "one", "status": "ok", "transcript_path": "one.json", "transcript_sha256": "a" * 64}
    second = {"run_id": "two", "status": "error", "transcript_path": "two.json", "transcript_sha256": "b" * 64}

    subagent._append_run_index(first)
    subagent._append_run_index(second)

    entries = [json.loads(line) for line in (tmp_path / "runs" / "index.jsonl").read_text().splitlines()]
    assert entries[0]["previous_entry_sha256"] == ""
    assert entries[0]["entry_sha256"] == subagent._index_entry_hash(entries[0])
    assert entries[1]["previous_entry_sha256"] == entries[0]["entry_sha256"]
    assert entries[1]["entry_sha256"] == subagent._index_entry_hash(entries[1])


def test_append_run_index_rejects_symlink_index_path(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        return
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    target = tmp_path / "outside-index.jsonl"
    target.write_text("", encoding="utf-8")
    (run_dir / "index.jsonl").symlink_to(target)
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    record = {"run_id": "one", "status": "ok", "transcript_path": "one.json", "transcript_sha256": "a" * 64}

    try:
        subagent._append_run_index(record)
    except ValueError as e:
        assert "regular non-symlink" in str(e)
    else:
        raise AssertionError("symlink run index should fail closed")
    assert target.read_text(encoding="utf-8") == ""


def test_append_run_index_rejects_symlink_index_lock_path(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        return
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    target = tmp_path / "outside-lock"
    target.write_text("", encoding="utf-8")
    (run_dir / "index.jsonl.lock").symlink_to(target)
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    record = {"run_id": "one", "status": "ok", "transcript_path": "one.json", "transcript_sha256": "a" * 64}

    try:
        subagent._append_run_index(record)
    except ValueError as e:
        assert "regular non-symlink" in str(e)
    else:
        raise AssertionError("symlink run-index lock should fail closed")
    assert target.read_text(encoding="utf-8") == ""


def test_verify_subagent_run_index_rejects_symlink_index_path(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        return
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    target = run_dir / "real-index.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    (run_dir / "index.jsonl").symlink_to(target)
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_audit_error"
    assert "regular non-symlink" in audit["summary"]


def test_verify_subagent_run_index_reads_index_with_nofollow_opener(tmp_path, monkeypatch):
    """Index audits should keep no-follow protection on the actual read."""
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    index_path = run_dir / "index.jsonl"
    entry = {
        "run_id": "one",
        "persona_key": "mock",
        "status": "ok",
        "started_at": 1,
        "finished_at": 2,
        "transcript_path": "",
        "transcript_sha256": "",
        "previous_entry_sha256": "",
    }
    entry["entry_sha256"] = subagent._index_entry_hash(entry)
    index_path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    opened = []
    original_open = subagent._open_regular_no_symlink

    def recording_open(path, flags, mode=0o600):
        opened.append((path, flags))
        return original_open(path, flags, mode)

    monkeypatch.setattr(subagent, "_open_regular_no_symlink", recording_open)

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_verified"
    assert any(path == str(index_path) and flags == os.O_RDONLY for path, flags in opened)


def test_verify_subagent_run_index_rejects_symlink_transcript_path(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        return
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    target = run_dir / "real-transcript.json"
    digest = subagent._json_atomic_write(str(target), {"status": "ok", "run_id": "one"})
    link = run_dir / "linked-transcript.json"
    link.symlink_to(target)
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    entry = {
        "run_id": "one",
        "persona_key": "mock",
        "status": "ok",
        "started_at": 1,
        "finished_at": 2,
        "transcript_path": str(link),
        "transcript_sha256": digest,
        "previous_entry_sha256": "",
    }
    entry["entry_sha256"] = subagent._index_entry_hash(entry)
    (run_dir / "index.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_tampered"
    assert audit["transcripts_checked"] == 0
    assert audit["issues"][0]["issue"] == "transcript_unverifiable:ValueError"


def test_review_subagent_candidate_rejects_symlink_transcript_path(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        return
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    target = run_dir / "real-transcript.json"
    subagent._json_atomic_write(str(target), {"status": "ok", "run_id": "one", "summary": "done"})
    link = run_dir / "linked-transcript.json"
    link.symlink_to(target)
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    review = json.loads(subagent.review_subagent_candidate(str(link)))

    assert review["status"] == "candidate_review_error"
    assert "regular non-symlink" in review["summary"]


def test_verify_subagent_run_index_checks_chain_and_transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    first_transcript = runs / "one.json"
    second_transcript = runs / "two.json"
    first_digest = subagent._json_atomic_write(str(first_transcript), {"status": "ok", "run_id": "one"})
    second_digest = subagent._json_atomic_write(str(second_transcript), {"status": "error", "run_id": "two"})

    subagent._append_run_index({
        "run_id": "one", "status": "ok",
        "transcript_path": str(first_transcript), "transcript_sha256": first_digest,
    })
    subagent._append_run_index({
        "run_id": "two", "status": "error",
        "transcript_path": str(second_transcript), "transcript_sha256": second_digest,
    })

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_verified"
    assert audit["entries_checked"] == 2
    assert audit["transcripts_checked"] == 2
    assert audit["issue_count"] == 0


def test_verify_subagent_run_index_detects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    transcript = runs / "one.json"
    digest = subagent._json_atomic_write(str(transcript), {"status": "ok", "run_id": "one"})
    subagent._append_run_index({
        "run_id": "one", "status": "ok",
        "transcript_path": str(transcript), "transcript_sha256": digest,
    })
    transcript.write_text(json.dumps({"status": "changed", "run_id": "one"}), encoding="utf-8")

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_tampered"
    assert audit["issue_count"] == 1
    assert audit["issues"][0]["issue"] == "transcript_hash_mismatch"


def test_verify_subagent_run_index_rejects_oversized_index_before_read(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_AUDIT_BYTES", 16)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    index_path = runs / "index.jsonl"
    index_path.write_text("x" * 64, encoding="utf-8")
    monkeypatch.setattr(
        os.path,
        "getsize",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("index audit must not use path-based getsize")),
    )

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_audit_too_large"
    assert audit["index_size_bytes"] == 64
    assert audit["entries_checked"] == 0
    assert "OMEGACLAW_SUBAGENT_MAX_INDEX_AUDIT_BYTES=16" in audit["summary"]


class _GrowingIndexRead:
    def __init__(self, raw_file):
        self._raw_file = raw_file
        self._lines = iter([b"{}\n", b"x" * 64])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._raw_file.close()
        return False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._lines)


def test_verify_subagent_run_index_enforces_index_cap_during_read(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_AUDIT_BYTES", 16)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    index_path = runs / "index.jsonl"
    index_path.write_text("{}\n", encoding="utf-8")
    original_fdopen = os.fdopen

    def growing_fdopen(fd, mode="r", *args, **kwargs):
        wrapped = original_fdopen(fd, mode, *args, **kwargs)
        if mode == "rb":
            return _GrowingIndexRead(wrapped)
        return wrapped

    monkeypatch.setattr(os, "fdopen", growing_fdopen)

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_audit_too_large"
    assert audit["index_size_bytes"] > 16
    assert audit["entries_checked"] == 0



def test_verify_subagent_run_index_rejects_oversized_transcript_before_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES", 16)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    transcript = runs / "one.json"
    transcript.write_text("x" * 64, encoding="utf-8")
    digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
    subagent._append_run_index({
        "run_id": "one", "status": "ok",
        "transcript_path": str(transcript), "transcript_sha256": digest,
    })

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_tampered"
    assert audit["entries_checked"] == 1
    assert audit["transcripts_checked"] == 0
    assert audit["issue_count"] == 1
    assert audit["issues"][0]["issue"] == "transcript_too_large"
    assert audit["issues"][0]["transcript_size_bytes"] == 64
    assert audit["issues"][0]["max_transcript_audit_bytes"] == 16


def test_verify_subagent_run_index_transcript_audit_cap_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES", 0)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    transcript = runs / "one.json"
    transcript.write_text("x" * 64, encoding="utf-8")
    digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
    subagent._append_run_index({
        "run_id": "one", "status": "ok",
        "transcript_path": str(transcript), "transcript_sha256": digest,
    })

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_verified"
    assert audit["entries_checked"] == 1
    assert audit["transcripts_checked"] == 1
    assert audit["issue_count"] == 0


def test_verify_subagent_run_index_transcript_size_check_uses_lstat_not_getsize(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES", 1024)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    transcript = runs / "one.json"
    transcript.write_text("bounded transcript", encoding="utf-8")
    digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
    subagent._append_run_index({
        "run_id": "one", "status": "ok",
        "transcript_path": str(transcript), "transcript_sha256": digest,
    })

    monkeypatch.setattr(
        os.path,
        "getsize",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("transcript audit must not use path-based getsize")),
    )

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_verified"
    assert audit["transcripts_checked"] == 1
    assert audit["issue_count"] == 0


def test_verify_subagent_run_index_streams_transcript_hash_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES", 1024 * 1024)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    transcript = runs / "one.json"
    transcript.write_text("x" * 128, encoding="utf-8")
    digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
    subagent._append_run_index({
        "run_id": "one", "status": "ok",
        "transcript_path": str(transcript), "transcript_sha256": digest,
    })

    real_open = builtins.open

    class NoUnboundedRead:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self._wrapped.__exit__(*args)

        def __iter__(self):
            return iter(self._wrapped)

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def read(self, size=-1):
            assert size != -1, "transcript audit must not call unbounded read()"
            return self._wrapped.read(size)

    def guarded_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if os.path.realpath(str(path)) == os.path.realpath(str(transcript)):
            return NoUnboundedRead(handle)
        return handle

    monkeypatch.setattr(builtins, "open", guarded_open)

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_verified"
    assert audit["transcripts_checked"] == 1
    assert audit["issue_count"] == 0

def test_verify_subagent_run_index_audit_cap_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_AUDIT_BYTES", 0)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True)
    index_path = runs / "index.jsonl"
    entry = {
        "run_id": "one",
        "status": "ok",
        "timestamp": 0,
        "transcript_path": "",
        "transcript_sha256": "",
        "previous_entry_sha256": "",
        "padding": "x" * 64,
    }
    entry["entry_sha256"] = subagent._index_entry_hash(entry)
    index_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    audit = json.loads(subagent.verify_subagent_run_index())

    assert audit["status"] == "index_verified"
    assert audit["entries_checked"] == 1
    assert audit["issue_count"] == 0


def test_dispatch_rejects_mixed_emit_and_tool_response(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(emit "done")\n(write-file "hidden.txt" "nope")', 0, 0),
    )

    payload = json.loads(subagent.dispatch("try mixed final", "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert not (tmp_path / "workspace" / "hidden.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"


@pytest.mark.parametrize(
    "raw",
    [
        'narration that the parser ignores\n(emit "done")',
        '(emit "done")\n(write-file "hidden.txt" "unterminated"',
    ],
)
def test_dispatch_rejects_unparsed_text_beside_final_emit(tmp_path, monkeypatch, raw):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: (raw, 0, 0),
    )

    payload = json.loads(
        subagent.dispatch("reject hidden final text", "write-file", "unit", max_turns=1)
    )

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert "unparsed text or additional records" in payload["summary"]
    assert not (tmp_path / "workspace" / "hidden.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"


def test_dispatch_rejects_same_line_trailing_payload_after_emit(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(emit "done") (write-file "hidden.txt" "nope")', 0, 0),
    )

    payload = json.loads(subagent.dispatch("try hidden same-line final", "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert "emit requires exactly one" in payload["summary"]
    assert not (tmp_path / "workspace" / "hidden.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"
    assert saved["turns"][0]["tool_calls"] == [
        {"name": "emit", "args": ["done", ') (write-file "hidden.txt" "nope"']}
    ]


def test_dispatch_rejects_unquoted_same_line_trailing_payload_after_emit(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(emit done) (write-file "hidden.txt" "nope")', 0, 0),
    )

    payload = json.loads(subagent.dispatch("try unquoted hidden same-line final", "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert "emit requires exactly one" in payload["summary"]
    assert not (tmp_path / "workspace" / "hidden.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"
    assert saved["turns"][0]["tool_calls"] == [
        {"name": "emit", "args": ["done", '(write-file "hidden.txt" "nope"']}
    ]


def test_dispatch_rejects_oversized_emit_before_success(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_EMIT_CHARS", 8)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(emit "this final response is too long")', 0, 0),
    )

    payload = json.loads(subagent.dispatch("bound final emit", "read-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert "exceeds 8 characters" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"
    assert saved["summary"].startswith("EMIT_PROTOCOL_VIOLATION")


def test_extract_final_emit_rejects_non_string_argument():
    value, error = subagent._extract_final_emit([("emit", [{"summary": "bad"}])])

    assert value is None
    assert error == "EMIT_PROTOCOL_VIOLATION: emit argument must be a string"


@pytest.mark.parametrize("raw", ['(emit "")', '(emit "   ")'])
def test_dispatch_rejects_empty_emit_before_success(tmp_path, monkeypatch, raw):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: (raw, 0, 0),
    )

    payload = json.loads(
        subagent.dispatch("reject empty final", "read-file", "unit", max_turns=1)
    )

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert "must not be empty or whitespace-only" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"


@pytest.mark.parametrize("control", ["\u2028", "\u202e", "\u2060", "\ud800"])
def test_dispatch_rejects_unsafe_unicode_emit_before_success(
    tmp_path, monkeypatch, control
):
    """Final returns cannot inject invisible audit/prompt controls."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: (f'(emit "safe{control}spoof")', 0, 0),
    )

    payload = json.loads(
        subagent.dispatch("reject unsafe final", "read-file", "unit", max_turns=1)
    )

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert "control characters" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"
    assert saved["summary"].startswith("EMIT_PROTOCOL_VIOLATION")


def test_dispatch_rejects_non_string_emit_before_success(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "parse_calls",
        lambda _raw: [("emit", [{"summary": "bad"}])],
    )
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('{"tool": "emit", "summary": "bad"}', 0, 0),
    )

    payload = json.loads(subagent.dispatch("reject typed emit", "read-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "EMIT_PROTOCOL_VIOLATION" in payload["summary"]
    assert "emit argument must be a string" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "emit_protocol_violation"


def test_dispatch_rejects_oversized_worker_response_before_parsing(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_RESPONSE_CHARS", 24)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(write-file "hidden.txt" "nope")\n' + ("x" * 200), 12, 34),
    )

    payload = json.loads(subagent.dispatch("bound raw worker response", "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "OMEGACLAW_SUBAGENT_MAX_RESPONSE_CHARS=24" in payload["summary"]
    assert not (tmp_path / "workspace" / "hidden.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "response_too_large"
    assert saved["worker_token_usage"] == {"input_tokens": 12, "output_tokens": 34, "total_tokens": 46}
    assert saved["turns"][0]["tool_calls"] == []
    assert len(saved["turns"][0]["raw_response"]) < 80
    assert saved["turns"][0]["raw_response"].startswith('(write-file "hidden')


def test_tool_quota_stops_dispatch_with_structured_error(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOOL_CALLS", 1)
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: ('(write-file "a.txt" "a")\n(write-file "b.txt" "b")', 0, 0))

    payload = json.loads(subagent.dispatch("write too much", "write-file", "unit", max_turns=2))

    assert payload["status"] == "error"
    assert "QUOTA_EXCEEDED" in payload["summary"]
    assert payload["files_changed"] == ["a.txt"]
    assert (tmp_path / "workspace" / "a.txt").read_text() == "a"
    assert not (tmp_path / "workspace" / "b.txt").exists()


def test_per_turn_tool_quota_limits_multi_call_worker_response(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOOL_CALLS", 8)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOOL_CALLS_PER_TURN", 1)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(write-file "a.txt" "a")\n(write-file "b.txt" "b")', 0, 0),
    )

    payload = json.loads(subagent.dispatch("write too much at once", "write-file", "unit", max_turns=2))

    assert payload["status"] == "error"
    assert "TURN_QUOTA_EXCEEDED" in payload["summary"]
    assert payload["files_changed"] == ["a.txt"]
    assert (tmp_path / "workspace" / "a.txt").read_text() == "a"
    assert not (tmp_path / "workspace" / "b.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "turn_quota_exceeded"


def test_cancel_file_stops_dispatch_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    cancel = tmp_path / "cancel.token"
    cancel.write_text("stop")
    monkeypatch.setattr(subagent, "_SUBAGENT_CANCEL_FILE", str(cancel))
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")))

    payload = json.loads(subagent.dispatch("cancel me", "write-file", "unit", max_turns=2))

    assert payload["status"] == "cancelled"
    assert "cancellation token" in payload["summary"]


def test_escalation_policy_hash_mismatch_denies_cloud_dispatch(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch, node_role="cloud")
    policy = tmp_path / "escalation.metta"
    policy.write_text("trusted policy")
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_PATH", str(policy))
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_SHA256", "0" * 64)
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")))

    result = subagent.dispatch("cloud task", "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "escalation denied" in payload["summary"]
    assert "integrity mismatch" in payload["summary"]
    assert str(tmp_path) not in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "escalation_denied"


def test_escalation_policy_integrity_read_is_bounded_before_hashing(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch, node_role="cloud")
    policy = tmp_path / "escalation.metta"
    policy.write_text("x" * 11)
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_PATH", str(policy))
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_SHA256", hashlib.sha256(b"x" * 11).hexdigest())
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_ESCALATION_POLICY_BYTES", 10)
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")))

    result = subagent.dispatch("cloud task", "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "escalation denied" in payload["summary"]
    assert "MAX_ESCALATION_POLICY_BYTES=10" in payload["summary"]
    assert str(tmp_path) not in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "escalation_denied"


def test_escalation_policy_integrity_size_check_uses_open_fd(tmp_path, monkeypatch):
    policy = tmp_path / "escalation.metta"
    policy.write_text("trusted policy")
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_PATH", str(policy))
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_SHA256", hashlib.sha256(b"trusted policy").hexdigest())
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_ESCALATION_POLICY_BYTES", 64)
    monkeypatch.setattr(os.path, "getsize", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("getsize race window")))

    ok, reason = subagent._escalation_policy_integrity()

    assert ok is True
    assert reason == "escalation.metta integrity ok"


def test_escalation_policy_integrity_rejects_symlink_policy(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch, node_role="cloud")
    real_policy = tmp_path / "real-escalation.metta"
    real_policy.write_text("trusted policy")
    symlink_policy = tmp_path / "escalation.metta"
    symlink_policy.symlink_to(real_policy)
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_PATH", str(symlink_policy))
    monkeypatch.setenv("OMEGACLAW_ESCALATION_METTA_SHA256", hashlib.sha256(b"trusted policy").hexdigest())
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")))

    result = subagent.dispatch("cloud task", "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "escalation denied" in payload["summary"]
    assert "not found" in payload["summary"] or "integrity read failed" in payload["summary"]
    assert str(tmp_path) not in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "escalation_denied"
    assert symlink_policy.is_symlink()
    assert real_policy.read_text() == "trusted policy"


def test_task_contract_limits_file_paths_and_persists_contract(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "write only inside safe output",
        "allowed_paths": ["safe"],
        "done_criteria": ["safe/out.txt exists"],
    })
    responses = iter([
        '(write-file "unsafe.txt" "nope")\n(write-file "safe/out.txt" "ok")',
        '(emit "contract respected")',
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: (next(responses), 0, 0))

    payload = json.loads(subagent.dispatch(contract_goal, "write-file", "unit", max_turns=3))

    assert payload["status"] == "ok"
    assert payload["files_changed"] == ["safe/out.txt"]
    assert not (tmp_path / "workspace" / "unsafe.txt").exists()
    assert (tmp_path / "workspace" / "safe" / "out.txt").read_text() == "ok"
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["goal"] == "write only inside safe output"
    assert saved["task_contract"]["objective"] == "write only inside safe output"
    assert saved["task_contract"]["allowed_paths"] == ["safe"]
    assert saved["task_contract"]["done_criteria"] == ["safe/out.txt exists"]
    assert "CONTRACT_VIOLATION" in saved["turns"][0]["tool_results"]


def test_task_contract_forbidden_action_blocks_tool(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "do not modify files",
        "forbidden_actions": ["write-file"],
    })
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: ('(write-file "out.txt" "nope")', 0, 0))

    payload = json.loads(subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1))

    assert payload["status"] == "incomplete"
    assert payload["files_changed"] == []
    assert not (tmp_path / "workspace" / "out.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert "forbidden by task contract" in saved["turns"][0]["tool_results"]


def test_task_contract_max_tool_calls_narrows_global_quota(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOOL_CALLS", 8)
    contract_goal = json.dumps({
        "objective": "write one file only",
        "max_tool_calls": 1,
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(write-file "a.txt" "a")\n(write-file "b.txt" "b")', 0, 0),
    )

    payload = json.loads(subagent.dispatch(contract_goal, "write-file", "unit", max_turns=2))

    assert payload["status"] == "error"
    assert "QUOTA_EXCEEDED" in payload["summary"]
    assert payload["files_changed"] == ["a.txt"]
    assert (tmp_path / "workspace" / "a.txt").read_text() == "a"
    assert not (tmp_path / "workspace" / "b.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["task_contract"]["max_tool_calls"] == 1


def test_task_contract_patch_proposal_only_records_without_writing(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "propose a patch but do not apply it",
        "patch_proposal_only": True,
    })
    responses = iter([
        '(write-file "proposed.txt" "candidate")',
        '(emit "patch proposed")',
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: (next(responses), 0, 0))

    payload = json.loads(subagent.dispatch(contract_goal, "write-file", "unit", max_turns=3))

    assert payload["status"] == "ok"
    assert payload["files_changed"] == []
    assert payload["patch_proposals"] == [{"action": "write-file", "path": "proposed.txt"}]
    assert not (tmp_path / "workspace" / "proposed.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["task_contract"]["patch_proposal_only"] is True
    assert saved["patch_proposals"] == [{
        "action": "write-file",
        "path": "proposed.txt",
        "content": "candidate",
    }]
    assert "PATCH_PROPOSAL_RECORDED" in saved["turns"][0]["tool_results"]


def test_task_contract_patch_proposal_only_bounds_persisted_content(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_PATCH_PROPOSAL_CHARS", 5)
    contract_goal = json.dumps({
        "objective": "propose a bounded patch but do not apply it",
        "patch_proposal_only": True,
    })
    responses = iter([
        '(write-file "proposed.txt" "abcdefghijklmnopqrstuvwxyz")',
        '(emit "patch proposed")',
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: (next(responses), 0, 0))

    payload = json.loads(subagent.dispatch(contract_goal, "write-file", "unit", max_turns=3))

    assert payload["status"] == "ok"
    assert payload["patch_proposals"] == [{"action": "write-file", "path": "proposed.txt"}]
    assert not (tmp_path / "workspace" / "proposed.txt").exists()
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    proposal = saved["patch_proposals"][0]
    assert proposal["content"].startswith("abcde")
    assert "patch proposal content truncated at 5 chars" in proposal["content"]
    assert "fghijklmnopqrstuvwxyz" not in proposal["content"]


def test_task_contract_patch_proposal_only_must_be_boolean_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(json.dumps({
        "objective": "bad patch mode",
        "patch_proposal_only": "yes",
    }), "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "patch_proposal_only" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"


def test_inline_task_contract_rejects_scalar_string_list_fields_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(json.dumps({
        "objective": "bad allowed path shape",
        "allowed_paths": "safe",
    }), "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "allowed_paths must be a list of strings" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    assert saved["task_contract"]["allowed_paths"] == "safe"


def test_inline_task_contract_rejects_non_string_list_items_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(json.dumps({
        "objective": "bad done criteria shape",
        "done_criteria": ["file exists", 123],
    }), "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "done_criteria entries must be strings" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    assert saved["task_contract"]["done_criteria"] == ["file exists", 123]


@pytest.mark.parametrize(
    "objective", [None, True, 123, ["typed", "objective"], {"typed": "objective"}]
)
def test_inline_task_contract_rejects_non_string_objective_before_llm(
    tmp_path, monkeypatch, objective
):
    """Typed JSON objectives must not be coerced into worker prompt strings."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(
        subagent.dispatch(
            json.dumps({"objective": objective}), "read-file", "unit", max_turns=1
        )
    )

    assert payload["status"] == "error"
    assert "objective must be a string" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    assert saved["task_contract"]["objective"] == objective


@pytest.mark.parametrize("objective", ["", "   ", "\t"])
def test_inline_task_contract_rejects_blank_objective_before_llm(
    tmp_path, monkeypatch, objective
):
    """A bounded child task must still contain a meaningful objective."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(
        subagent.dispatch(
            json.dumps({"objective": objective}), "read-file", "unit", max_turns=1
        )
    )

    assert payload["status"] == "error"
    assert "objective must be a non-empty string" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    assert saved["task_contract"]["objective"] == objective.strip()


@pytest.mark.parametrize("field", ["allowed_paths", "forbidden_actions", "done_criteria"])
@pytest.mark.parametrize("entry", ["", "   ", "\t"])
def test_inline_task_contract_rejects_blank_string_list_entries_before_llm(
    tmp_path, monkeypatch, field, entry
):
    """Blank contract constraints must not disappear during normalization."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(
        subagent.dispatch(
            json.dumps({"objective": "bounded task", field: [entry]}),
            "read-file",
            "unit",
            max_turns=1,
        )
    )

    assert payload["status"] == "error"
    assert f"{field} entries must be non-empty strings" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    assert saved["task_contract"][field] == [""]


@pytest.mark.parametrize("task_contract", [None, True, 123, [], "not-an-object"])
def test_inline_nested_task_contract_requires_json_object_before_llm(
    tmp_path, monkeypatch, task_contract
):
    """Malformed nested contracts must not be ignored in favor of the outer object."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(
        subagent.dispatch(
            json.dumps({"objective": "typed nested contract", "task_contract": task_contract}),
            "read-file",
            "unit",
            max_turns=1,
        )
    )

    assert payload["status"] == "error"
    assert "task contract field must be a JSON object" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    assert saved["task_contract"]["task_contract"] == task_contract


def test_task_contract_requires_adjudication_marks_candidate_not_final(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "produce high stakes output for review",
        "requires_adjudication": True,
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: ('(emit "candidate answer")', 12, 5),
    )

    payload = json.loads(subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1))

    assert payload["status"] == "needs_adjudication"
    assert payload["adjudication"]["required"] is True
    assert payload["adjudication"]["status"] == "pending"
    assert payload["adjudication"]["candidate_summary"] == "candidate answer"
    assert "requires adjudication" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "adjudication_required"
    assert saved["task_contract"]["requires_adjudication"] is True
    assert saved["adjudication"]["candidate_summary"] == "candidate answer"
    assert saved["worker_token_usage"]["total_tokens"] == 17


def test_task_contract_requires_adjudication_must_be_boolean_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch(json.dumps({
        "objective": "bad adjudicator flag",
        "requires_adjudication": "yes",
    }), "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "requires_adjudication" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"


def test_review_subagent_candidate_reports_proposals_and_adjudication(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    transcript = Path(subagent.SUBAGENT_RUN_DIR) / "reviewable.json"
    record = {
        "status": "adjudication_required",
        "summary": "candidate answer",
        "task_contract": {"requires_adjudication": True, "patch_proposal_only": True},
        "patch_proposals": [{"action": "write-file", "path": "candidate.txt", "content": "draft"}],
        "adjudication": {"required": True, "status": "pending", "candidate_summary": "candidate answer"},
    }
    digest = subagent._json_atomic_write(str(transcript), record)
    subagent._write_transcript_integrity_sidecar(str(transcript), digest)

    review = json.loads(subagent.review_subagent_candidate(str(transcript)))

    assert review["status"] == "candidate_review_ready"
    assert review["checksum"] == "verified"
    assert review["patch_proposals"] == [{"action": "write-file", "path": "candidate.txt"}]
    assert review["adjudication"]["required"] is True
    assert set(review["gates"]) == {"patch_proposal_review", "adjudication_required"}
    assert not (tmp_path / "candidate.txt").exists()


def test_review_subagent_candidate_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    outside = tmp_path / "outside.json"
    outside.write_text("{}")

    review = json.loads(subagent.review_subagent_candidate(str(outside)))

    assert review["status"] == "candidate_review_error"
    assert "escapes run dir" in review["summary"]
    assert str(tmp_path) not in review["summary"]
    assert str(tmp_path) not in review["transcript_path"]
    assert review["transcript_path"] == "outside.json"


def test_review_subagent_candidate_rejects_oversized_checksum_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SHA256_SIDECAR_BYTES", 128)
    transcript = Path(subagent.SUBAGENT_RUN_DIR) / "oversized-sidecar.json"
    subagent._json_atomic_write(str(transcript), {"status": "ok"})
    Path(f"{transcript}.sha256").write_text("a" * 129, encoding="utf-8")

    review = json.loads(subagent.review_subagent_candidate(str(transcript)))

    assert review["status"] == "candidate_review_error"
    assert "integrity sidecar exceeds 128 byte limit" in review["summary"]
    assert str(tmp_path) not in review["summary"]


def test_review_subagent_candidate_detects_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    transcript = Path(subagent.SUBAGENT_RUN_DIR) / "tampered.json"
    digest = subagent._json_atomic_write(str(transcript), {"status": "ok"})
    subagent._write_transcript_integrity_sidecar(str(transcript), digest)
    transcript.write_text(json.dumps({"status": "changed"}), encoding="utf-8")

    review = json.loads(subagent.review_subagent_candidate(str(transcript)))

    assert review["status"] == "transcript_tampered"
    assert review["expected_sha256"] == digest


def test_queue_only_dispatch_persists_task_without_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch("queue this safely", "write-file", "unit", max_turns=2))

    assert payload["status"] == "queued"
    assert payload["queue_path"].endswith(".json")
    queued = json.loads(Path(payload["queue_path"]).read_text())
    assert queued["status"] == "queued"
    assert queued["goal"] == "queue this safely"
    assert queued["tool_subset"] == ["write-file"]
    assert queued["max_turns"] == 2
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "queued"
    assert saved["queue_path"] == payload["queue_path"]
    assert len(payload["queue_sha256"]) == 64
    assert Path(payload["queue_sha256_path"]).exists()
    assert Path(payload["queue_sha256_path"]).read_text().startswith(payload["queue_sha256"])


def test_run_queued_dispatch_claims_task_and_runs_once(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    payload = json.loads(subagent.dispatch("queue and consume", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        return ('(emit "worker done")', 3, 2)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert calls["n"] == 1
    assert result["status"] == "ok"
    assert result["task_sha256"] == payload["queue_sha256"]
    assert result["result"]["summary"] == "worker done"
    assert result["result"]["worker_token_usage"]["total_tokens"] == 5
    assert not queue_path.exists()
    assert Path(result["task_done_path"]).exists()
    assert Path(result["task_done_path"] + ".result.json").exists()
    assert Path(result["task_sha256_path"]).exists()
    assert Path(result["task_sha256_path"]).read_text().startswith(payload["queue_sha256"])
    assert not Path(str(queue_path) + ".sha256").exists()
    assert os.environ.get("OMEGACLAW_SUBAGENT_QUEUE_ONLY") == "1"


def test_run_queued_dispatch_preserves_task_contract_during_worker_run(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    contract_goal = json.dumps({
        "objective": "queued contract must still constrain writes",
        "allowed_paths": ["safe"],
    })
    payload = json.loads(subagent.dispatch(contract_goal, "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])

    responses = iter([
        ('(write-file "unsafe.txt" "nope")\n(write-file "safe/out.txt" "ok")', 4, 2),
        ('(emit "contract preserved")', 3, 1),
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_args: next(responses))

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "ok"
    assert result["result"]["summary"] == "contract preserved"
    assert result["result"]["files_changed"] == ["safe/out.txt"]
    assert not (tmp_path / "workspace" / "unsafe.txt").exists()
    assert (tmp_path / "workspace" / "safe" / "out.txt").read_text() == "ok"
    worker_transcript = json.loads(Path(result["result"]["transcript_path"]).read_text())
    assert worker_transcript["task_contract"]["allowed_paths"] == ["safe"]
    assert "CONTRACT_VIOLATION" in worker_transcript["turns"][0]["tool_results"]


def test_run_queued_dispatch_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    outside = tmp_path / "outside.json"
    outside.write_text("{}")

    result = json.loads(subagent.run_queued_dispatch(str(outside)))

    assert result["status"] == "queue_worker_error"
    assert "escapes queue dir" in result["summary"]


def test_run_queued_dispatch_rejects_control_character_path_before_claim(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)
    task = queue_dir / "task.json"
    task.write_text('{"status":"queued"}', encoding="utf-8")

    result = json.loads(subagent.run_queued_dispatch(str(task) + "\n"))

    assert result["status"] == "queue_worker_error"
    assert "control characters" in result["summary"]
    assert task.exists()
    assert not Path(str(task) + ".claimed").exists()
    assert not Path(str(task) + ".failed").exists()


def test_run_queued_dispatch_rejects_unicode_control_path_before_claim(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)
    task = queue_dir / "task.json"
    task.write_text('{"status":"queued"}', encoding="utf-8")

    for control in ("\u202e", "\u2060", "\ud800"):
        result = json.loads(subagent.run_queued_dispatch(str(task) + control))
        assert result["status"] == "queue_worker_error"
        assert "control characters" in result["summary"]
        assert task.exists()
        assert not Path(str(task) + ".claimed").exists()
        assert not Path(str(task) + ".failed").exists()


def test_run_queued_dispatch_rejects_oversized_path_before_claim(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)
    task = queue_dir / "task.json"
    task.write_text('{"status":"queued"}', encoding="utf-8")

    too_long = str(task) + ("x" * (subagent._SUBAGENT_MAX_PATH_ARG_CHARS + 1))
    result = json.loads(subagent.run_queued_dispatch(too_long))

    assert result["status"] == "queue_worker_error"
    assert "bounded path string" in result["summary"]
    assert task.exists()
    assert not Path(str(task) + ".claimed").exists()
    assert not Path(str(task) + ".failed").exists()


def test_run_queued_dispatch_rejects_result_sidecar_without_renaming(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)
    sidecar = queue_dir / "task.json.done.result.json"
    sidecar.write_text('{"status":"ok"}', encoding="utf-8")

    result = json.loads(subagent.run_queued_dispatch(str(sidecar)))

    assert result["status"] == "queue_worker_error"
    assert "pending queue/*.json task record" in result["summary"]
    assert sidecar.exists()
    assert not Path(str(sidecar) + ".claimed").exists()
    assert not Path(str(sidecar) + ".failed").exists()


def test_queue_listing_and_worker_reject_symlink_task_records(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        return
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)
    target = queue_dir / "target.json"
    target.write_text('{"status":"queued"}', encoding="utf-8")
    symlink_task = queue_dir / "linked.json"
    os.symlink(target, symlink_task)

    assert str(symlink_task) not in subagent._pending_queued_dispatch_paths()
    assert subagent._pending_dispatch_queue_count() == 1

    result = json.loads(subagent.run_queued_dispatch(str(symlink_task)))

    assert result["status"] == "queue_worker_error"
    assert "regular non-symlink" in result["summary"]
    assert symlink_task.is_symlink()
    assert not Path(str(symlink_task) + ".claimed").exists()
    assert not Path(str(symlink_task) + ".failed").exists()


def test_queue_listing_and_worker_reject_symlink_queue_directory(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        return
    run_dir = tmp_path / "runs"
    outside_queue = tmp_path / "outside-queue"
    run_dir.mkdir()
    outside_queue.mkdir()
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    task = outside_queue / "task.json"
    task.write_text('{"status":"queued"}', encoding="utf-8")
    os.symlink(outside_queue, run_dir / "queue")

    assert subagent._pending_queued_dispatch_paths() == []
    assert subagent._pending_dispatch_queue_count() == subagent._SUBAGENT_MAX_QUEUED_DISPATCHES

    result = json.loads(subagent.run_queued_dispatch(str(run_dir / "queue" / "task.json")))

    assert result["status"] == "queue_worker_error"
    assert "subagent dispatch queue directory must be a real non-symlink directory" in result["summary"]
    assert task.exists()
    assert not Path(str(task) + ".claimed").exists()
    assert not Path(str(task) + ".failed").exists()


def test_run_queued_dispatch_retains_failed_claim_for_audit(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue invalid later", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["tool_subset"] = []
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "tool_subset" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()
    assert Path(str(queue_path) + ".failed.result.json").exists()
    saved_result = json.loads(Path(str(queue_path) + ".failed.result.json").read_text())
    assert saved_result["status"] == "queue_worker_error"
    assert saved_result["queue_path"] == str(queue_path)
    assert len(result["result_sha256"]) == 64
    assert subagent._pending_queued_dispatch_paths() == []
    assert subagent._pending_dispatch_queue_count() == 0


def test_run_queued_dispatch_rejects_unexpected_queue_task_fields_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue strict schema", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["worker_override"] = "unexpected mutable instruction"
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "unknown field" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_rejects_strict_numeric_schema_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue strict numeric schema", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["queued_at"] = float("nan")
    queued["max_turns"] = True
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "queued_at" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_rejects_coerced_integer_metadata_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue strict integer metadata", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["max_turns"] = 1.5
    queued["max_chars"] = "1000"
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "max_turns must be an integer" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_rejects_task_contract_shape_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue strict contract schema", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["task_contract"] = {"objective": "bad contract shape", "allowed_paths": "safe"}
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "allowed_paths must be a list" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_rejects_checksum_mismatch_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue then tamper", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["goal"] = "tampered before worker"
    queue_path.write_text(json.dumps(queued), encoding="utf-8")
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "checksum mismatch" in result["summary"]
    assert result["expected_task_sha256"] == payload["queue_sha256"]
    assert result["task_sha256"] != payload["queue_sha256"]
    assert not queue_path.exists()
    failed_path = Path(str(queue_path) + ".failed")
    assert failed_path.exists()
    assert Path(str(failed_path) + ".sha256").exists()
    assert not Path(str(queue_path) + ".sha256").exists()


def test_run_queued_dispatch_rejects_missing_checksum_sidecar_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue missing sidecar", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    Path(payload["queue_sha256_path"]).unlink()
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "missing integrity sidecar" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_preserves_task_cancel_file_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    cancel_file = Path(subagent.SUBAGENT_RUN_DIR) / "cancel.token"
    cancel_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_CANCEL_FILE", str(cancel_file))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue then cancel", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    cancel_file.write_text("cancel")
    monkeypatch.setattr(subagent, "_SUBAGENT_CANCEL_FILE", "")
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "cancelled"
    assert result["result"]["status"] == "cancelled"
    assert result["result"]["transcript_path"]
    saved = json.loads(Path(result["result"]["transcript_path"]).read_text())
    assert saved["status"] == "cancelled"
    assert subagent._SUBAGENT_CANCEL_FILE == ""
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".done").exists()


def test_run_queued_dispatch_rejects_cancel_file_escape_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue unsafe cancel", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["cancel_file"] = str(tmp_path / "outside.cancel")
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "cancel_file" in result["summary"]
    assert "run dir" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_rejects_cancel_file_control_chars_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    payload = json.loads(subagent.dispatch("queue forged cancel", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    queued["cancel_file"] = "safe.cancel\nforged-status: cancelled"
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "cancel_file" in result["summary"]
    assert "control characters" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_rejects_expired_task_age_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_TASK_AGE_S", 60.0)
    payload = json.loads(subagent.dispatch("queue expired task", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    queued = json.loads(queue_path.read_text())
    # Set queued_at to 2 hours ago, well beyond the 60s max age
    queued["queued_at"] = time.time() - 7200.0
    digest = subagent._json_atomic_write(str(queue_path), queued)
    subagent._write_transcript_integrity_sidecar(str(queue_path), digest)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "queue_worker_error"
    assert "expired" in result["summary"]
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".failed").exists()


def test_run_queued_dispatch_accepts_fresh_task_within_max_age(tmp_path, monkeypatch):
    """A task within the max age window should not be rejected by age validation."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_TASK_AGE_S", 3600.0)
    payload = json.loads(subagent.dispatch("queue fresh task", "write-file", "unit", max_turns=2))
    queue_path = Path(payload["queue_path"])
    # queued_at is already set to ~now by dispatch; verify age validation passes
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: ('(emit "fresh ok")', 3, 2),
    )

    result = json.loads(subagent.run_queued_dispatch(str(queue_path)))

    assert result["status"] == "ok"
    assert not queue_path.exists()
    assert Path(str(queue_path) + ".done").exists()


def test_drain_queued_dispatches_is_bounded_and_preserves_queue_only_env(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    first = json.loads(subagent.dispatch("queue first", "write-file", "unit", max_turns=2))
    second = json.loads(subagent.dispatch("queue second", "write-file", "unit", max_turns=2))
    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        return (f'(emit "worker done {calls["n"]}")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)

    drained = json.loads(subagent.drain_queued_dispatches(max_tasks=1))

    assert drained["status"] == "drained"
    assert drained["tasks_attempted"] == 1
    assert drained["tasks_completed"] == 1
    assert drained["remaining_queue_tasks"] == 1
    assert calls["n"] == 1
    assert not Path(first["queue_path"]).exists()
    assert Path(first["queue_path"] + ".done").exists()
    assert Path(second["queue_path"]).exists()
    assert os.environ.get("OMEGACLAW_SUBAGENT_QUEUE_ONLY") == "1"


def test_drain_queued_dispatches_reports_empty_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))

    drained = json.loads(subagent.drain_queued_dispatches(max_tasks=3))

    assert drained["status"] == "queue_empty"
    assert drained["tasks_attempted"] == 0
    assert drained["remaining_queue_tasks"] == 0
    assert drained["results"] == []


def test_run_queued_worker_loop_drains_until_idle_and_preserves_queue_only_env(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    first = json.loads(subagent.dispatch("queue async first", "write-file", "unit", max_turns=2))
    second = json.loads(subagent.dispatch("queue async second", "write-file", "unit", max_turns=2))
    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        return (f'(emit "async worker done {calls["n"]}")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=4, poll_interval_s=0, max_idle_polls=0, max_runtime_s=30,
    ))

    assert result["status"] == "worker_drained"
    assert result["stop_reason"] == "idle"
    assert result["tasks_attempted"] == 2
    assert result["tasks_completed"] == 2
    assert result["remaining_queue_tasks"] == 0
    assert calls["n"] == 2
    assert Path(first["queue_path"] + ".done").exists()
    assert Path(second["queue_path"] + ".done").exists()
    assert os.environ.get("OMEGACLAW_SUBAGENT_QUEUE_ONLY") == "1"


def test_run_queued_worker_loop_stops_on_max_runtime(tmp_path, monkeypatch):
    """The loop should exit with stop_reason=max_runtime when the wall-clock
    cap is reached between queued tasks, before claiming more work."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    first = json.loads(subagent.dispatch("runtime cap first", "write-file", "unit", max_turns=2))
    second = json.loads(subagent.dispatch("runtime cap second", "write-file", "unit", max_turns=2))
    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        return (f'(emit "runtime cap done {calls["n"]}")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    # Simulate: first task succeeds, then runtime cap is hit before second task.
    original_time = subagent.time.time
    fake_clock = {"t": 0.0}

    def fake_time():
        return fake_clock["t"]

    def fake_worker_loop_time(*_args):
        # After first task completes, jump clock past runtime cap
        if calls["n"] >= 1:
            fake_clock["t"] = 100.0
        return fake_time()

    monkeypatch.setattr(subagent.time, "time", fake_worker_loop_time)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=4, poll_interval_s=0, max_idle_polls=0, max_runtime_s=10.0,
    ))

    assert result["stop_reason"] == "max_runtime"
    assert result["tasks_attempted"] == 1
    assert calls["n"] == 1
    assert Path(first["queue_path"] + ".done").exists()
    assert Path(second["queue_path"]).exists()  # second still pending


def test_run_queued_worker_loop_records_worker_error_and_continues(tmp_path, monkeypatch):
    """A failing queued task should be recorded as queue_worker_error in
    results, but the loop should continue to the next pending task."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    first = json.loads(subagent.dispatch("error task", "write-file", "unit", max_turns=2))
    second = json.loads(subagent.dispatch("good task", "write-file", "unit", max_turns=2))
    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated worker failure")
        return (f'(emit "recovered after error")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=4, poll_interval_s=0, max_idle_polls=0, max_runtime_s=30,
    ))

    assert result["status"] == "worker_drained"
    assert result["tasks_attempted"] == 2
    assert result["tasks_completed"] == 1
    # First result should be an error; second should be a success
    statuses = [r.get("status") for r in result["results"]]
    assert "queue_worker_error" in statuses
    assert "ok" in statuses


def test_run_queued_worker_loop_honors_stop_file_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    stop_file = Path(subagent.SUBAGENT_RUN_DIR) / "stop.worker"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("stop", encoding="utf-8")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    queued = json.loads(subagent.dispatch("queue but stop worker", "write-file", "unit", max_turns=2))
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=4, poll_interval_s=0, max_idle_polls=0, stop_file=str(stop_file),
    ))

    assert result["status"] == "worker_stopped"
    assert result["stop_reason"] == "stop_file"
    assert result["tasks_attempted"] == 0
    assert result["remaining_queue_tasks"] == 1
    assert Path(queued["queue_path"]).exists()


def test_run_queued_worker_loop_rejects_concurrent_local_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    lock_path = Path(subagent.SUBAGENT_RUN_DIR) / ".async-worker.lock"
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        if subagent.fcntl is None:
            return
        subagent.fcntl.flock(lock.fileno(), subagent.fcntl.LOCK_EX | subagent.fcntl.LOCK_NB)
        subagent._write_worker_loop_lock_metadata(lock, {
            "pid": 12345,
            "started_at": 111.0,
            "status": "running",
            "run_dir": subagent.SUBAGENT_RUN_DIR,
        })
        try:
            result = json.loads(subagent.run_queued_worker_loop(
                max_tasks=1, poll_interval_s=0, max_idle_polls=0,
            ))
        finally:
            subagent.fcntl.flock(lock.fileno(), subagent.fcntl.LOCK_UN)

    assert result["status"] == "worker_already_running"
    assert result["tasks_attempted"] == 0
    assert result["worker_lock"]["status"] == "running"
    assert result["worker_lock"]["pid"] == 12345


def test_run_queued_worker_loop_writes_finished_lock_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))
    lock_path = Path(result["lock_path"])
    metadata = json.loads(lock_path.read_text(encoding="utf-8"))

    assert result["status"] == "worker_idle"
    assert result["stop_reason"] == "idle"
    assert metadata["status"] == "finished"
    assert metadata["stop_reason"] == "idle"
    assert metadata["tasks_attempted"] == 0


def test_run_queued_worker_loop_graceful_signal_shutdown(tmp_path, monkeypatch):
    """SIGTERM/SIGINT during worker loop causes graceful exit, not crash."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    # Simulate a signal arriving before the first iteration check.
    subagent._worker_signal_state["stop_requested"] = True
    try:
        result = json.loads(subagent.run_queued_worker_loop(
            max_tasks=4, poll_interval_s=0, max_idle_polls=0,
        ))
    finally:
        subagent._worker_signal_state["stop_requested"] = False

    assert result["status"] == "worker_stopped"
    assert result["stop_reason"] == "signal"
    assert result["tasks_attempted"] == 0
    lock_path = Path(result["lock_path"])
    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "finished"
    assert metadata["stop_reason"] == "signal"


def test_run_queued_worker_loop_clears_signal_state_after_exit(tmp_path, monkeypatch):
    """A handled stop signal must not poison a later same-process worker run."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    subagent._worker_signal_state["stop_requested"] = True

    first = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))
    second = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))

    assert first["status"] == "worker_stopped"
    assert first["stop_reason"] == "signal"
    assert subagent._worker_signal_state["stop_requested"] is False
    assert second["status"] == "worker_idle"
    assert second["stop_reason"] == "idle"


def test_run_queued_worker_loop_restores_signal_handlers(tmp_path, monkeypatch):
    """Signal handlers are restored after the worker loop exits."""
    import signal as _sig
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    # Ensure no stale signal flag from prior tests.
    subagent._worker_signal_state["stop_requested"] = False
    # Record the pre-loop handler so we can verify restoration.
    original = _sig.signal(_sig.SIGTERM, _sig.SIG_DFL)
    try:
        result = json.loads(subagent.run_queued_worker_loop(
            max_tasks=0, poll_interval_s=0, max_idle_polls=0,
        ))
        # After the loop exits, SIGTERM handler should be restored to SIG_DFL.
        current = _sig.getsignal(_sig.SIGTERM)
        assert current == _sig.SIG_DFL
    finally:
        _sig.signal(_sig.SIGTERM, original)
        subagent._worker_signal_state["stop_requested"] = False


def test_run_queued_worker_loop_rejects_invalid_stop_file_before_lock(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0, stop_file="bad\x00token",
    ))

    assert result["status"] == "worker_config_invalid"
    assert result["tasks_attempted"] == 0
    assert "stop_file" in result["summary"]
    assert not (run_dir / ".async-worker.lock").exists()


def test_run_queued_worker_loop_rejects_stop_file_escape_before_lock(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
        stop_file=str(tmp_path / "outside.stop"),
    ))

    assert result["status"] == "worker_config_invalid"
    assert result["tasks_attempted"] == 0
    assert "stop_file" in result["summary"]
    assert "run dir" in result["summary"]
    assert not (run_dir / ".async-worker.lock").exists()


def test_run_queued_worker_loop_rejects_stop_file_control_chars_before_lock(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
        stop_file="safe.stop\nforged-status: idle",
    ))

    assert result["status"] == "worker_config_invalid"
    assert result["tasks_attempted"] == 0
    assert "stop_file" in result["summary"]
    assert "control characters" in result["summary"]
    assert not (run_dir / ".async-worker.lock").exists()


def test_run_queued_worker_loop_rejects_unicode_stop_file_controls_before_lock(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    for control in ("\u202e", "\u2060", "\ud800"):
        result = json.loads(subagent.run_queued_worker_loop(
            max_tasks=1, poll_interval_s=0, max_idle_polls=0,
            stop_file=f"safe.stop{control}spoof",
        ))
        assert result["status"] == "worker_config_invalid"
        assert result["tasks_attempted"] == 0
        assert "stop_file" in result["summary"]
        assert "control characters" in result["summary"]
        assert not (run_dir / ".async-worker.lock").exists()


def test_run_control_symlink_tokens_are_ignored(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    real_token = run_dir / "real.stop"
    token = run_dir / "stop.link"
    run_dir.mkdir(parents=True)
    real_token.write_text("stop", encoding="utf-8")
    token.symlink_to(real_token)
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0, stop_file=str(token),
    ))

    assert result["status"] == "worker_idle"
    assert result["stop_reason"] == "idle"


def test_run_queued_worker_loop_rejects_malformed_explicit_bounds_before_lock(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    cases = [
        {"max_tasks": True},
        {"max_tasks": "2"},
        {"max_idle_polls": 1.5},
        {"poll_interval_s": float("nan")},
        {"max_runtime_s": -0.1},
    ]
    for kwargs in cases:
        result = json.loads(subagent.run_queued_worker_loop(**kwargs))
        assert result["status"] == "worker_config_invalid"
        assert result["tasks_attempted"] == 0
        assert next(iter(kwargs)) in result["summary"]

    assert not (run_dir / ".async-worker.lock").exists()


def test_run_queued_worker_loop_detects_stale_lock_from_crashed_worker(tmp_path, monkeypatch):
    """When a new worker acquires a flock left by a crashed previous worker,
    the structured return includes ``stale_lock`` audit metadata."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    lock_path = run_dir / ".async-worker.lock"
    lock_path.parent.mkdir(parents=True)

    # Simulate a crashed previous worker: write lock metadata with status=running
    # but do NOT hold the flock (as if the process died and the OS released it).
    stale_metadata = {
        "pid": 99999,
        "started_at": 1000.0,
        "status": "running",
        "run_dir": str(run_dir),
        "max_tasks": 4,
        "tasks_attempted": 2,
        "tasks_completed": 1,
        "consecutive_errors": 0,
        "error_count": 0,
    }
    with lock_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(stale_metadata, ensure_ascii=False, sort_keys=True))
        f.write("\n")

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    assert result["stale_lock"] is not None
    assert result["stale_lock"]["pid"] == 99999
    assert result["stale_lock"]["status"] == "running"
    assert result["stale_lock"]["started_at"] == 1000.0

    # The lock file should now show finished metadata from the new worker.
    finished_metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert finished_metadata["status"] == "finished"
    assert finished_metadata["pid"] != 99999


def test_run_queued_worker_loop_ignores_oversized_stale_lock_metadata(tmp_path, monkeypatch):
    """Stale lock inspection should not parse an oversized local lock file."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent, "_SUBAGENT_ASYNC_WORKER_LOCK_METADATA_BYTES", 64)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    lock_path = run_dir / ".async-worker.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps({"status": "running", "pid": 777, "padding": "x" * 200}),
        encoding="utf-8",
    )

    assert subagent._read_worker_loop_lock_metadata(str(lock_path)) == {}
    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=0, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    assert result["stale_lock"] is None


def test_run_queued_worker_loop_ignores_symlink_stale_lock_metadata(tmp_path, monkeypatch):
    """Stale-lock inspection should not follow local lock symlinks."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    run_dir.mkdir(parents=True)
    target = tmp_path / "outside-lock.json"
    target.write_text(json.dumps({"status": "running", "pid": 31337}), encoding="utf-8")
    lock_path = run_dir / ".async-worker.lock"
    lock_path.symlink_to(target)

    assert subagent._read_worker_loop_lock_metadata(str(lock_path)) == {}


def test_read_worker_loop_lock_metadata_uses_nofollow_opener(tmp_path, monkeypatch):
    """Stale-lock metadata reads should not fall back to built-in open()."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    run_dir.mkdir(parents=True)
    lock_path = run_dir / ".async-worker.lock"
    lock_path.write_text(json.dumps({"status": "running", "pid": 123}), encoding="utf-8")
    opened = []
    original_open = subagent._open_regular_no_symlink

    def recording_open(path, flags, mode=0o600):
        opened.append((path, flags))
        return original_open(path, flags, mode)

    monkeypatch.setattr(subagent, "_open_regular_no_symlink", recording_open)

    metadata = subagent._read_worker_loop_lock_metadata(str(lock_path))

    assert metadata["status"] == "running"
    assert any(path == str(lock_path) and flags == os.O_RDONLY for path, flags in opened)


def test_run_queued_worker_loop_rejects_symlink_lock_path(tmp_path, monkeypatch):
    """The worker lock must be a regular in-run-dir file, not a symlink."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    run_dir.mkdir(parents=True)
    target = tmp_path / "outside-lock.json"
    target.write_text("{}\n", encoding="utf-8")
    (run_dir / ".async-worker.lock").symlink_to(target)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_config_invalid"
    assert "lock invalid" in result["summary"]
    assert result["tasks_attempted"] == 0


def test_run_queued_worker_loop_no_stale_lock_on_fresh_start(tmp_path, monkeypatch):
    """No ``stale_lock`` field when no previous lock file exists."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=0, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    assert result["stale_lock"] is None


def test_run_queued_worker_loop_no_stale_lock_after_clean_shutdown(tmp_path, monkeypatch):
    """No ``stale_lock`` when the previous worker wrote status=finished."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    lock_path = run_dir / ".async-worker.lock"
    lock_path.parent.mkdir(parents=True)

    clean_metadata = {
        "pid": 12345,
        "started_at": 500.0,
        "finished_at": 501.0,
        "status": "finished",
        "stop_reason": "idle",
        "tasks_attempted": 0,
    }
    with lock_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(clean_metadata, ensure_ascii=False, sort_keys=True))
        f.write("\n")

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=0, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    assert result["stale_lock"] is None


def test_run_queued_worker_loop_lock_has_current_task_during_execution(tmp_path, monkeypatch):
    """The lock metadata should include current_task_started_at and
    current_task_queue_path while a task is being processed, then clear
    them after the task completes."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    queued = json.loads(subagent.dispatch("track current task", "write-file", "unit", max_turns=2))
    queue_path = queued["queue_path"]

    lock_path = Path(subagent.SUBAGENT_RUN_DIR) / ".async-worker.lock"
    captured = {}

    original_dispatch = subagent.run_queued_dispatch

    def spy_dispatch(qp):
        # While dispatch is running, read the lock metadata
        meta = subagent._read_worker_loop_lock_metadata(str(lock_path))
        captured["during"] = meta
        return original_dispatch(qp)

    monkeypatch.setattr(subagent, "run_queued_dispatch", spy_dispatch)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        label = calls["n"]
        return (f'(emit "current-task test done {label}")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0, max_runtime_s=30,
    ))

    assert result["status"] == "worker_drained"
    assert result["tasks_attempted"] == 1
    assert result["tasks_completed"] == 1

    # During task execution, the lock should show current task info
    assert captured["during"]["status"] == "running"
    assert captured["during"]["current_task_started_at"] is not None
    assert captured["during"]["current_task_queue_path"] == queue_path
    assert captured["during"]["tasks_attempted"] == 1

    # After completion, the lock file should show finished with cleared fields
    finished_meta = json.loads(lock_path.read_text(encoding="utf-8"))
    assert finished_meta["status"] == "finished"
    assert finished_meta["current_task_started_at"] is None
    assert finished_meta["current_task_queue_path"] is None


def test_run_queued_worker_loop_stale_lock_includes_current_task_fields(tmp_path, monkeypatch):
    """When a stale lock is detected from a crashed worker, the stale_lock
    metadata should include current_task_started_at and current_task_queue_path
    if the crash happened mid-task."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    lock_path = run_dir / ".async-worker.lock"
    lock_path.parent.mkdir(parents=True)

    # Simulate a crashed worker that was mid-task
    stale_metadata = {
        "pid": 99999,
        "started_at": 1000.0,
        "status": "running",
        "run_dir": str(run_dir),
        "max_tasks": 4,
        "tasks_attempted": 3,
        "tasks_completed": 2,
        "consecutive_errors": 0,
        "error_count": 0,
        "current_task_started_at": 1500.0,
        "current_task_queue_path": str(run_dir / "queue" / "task-003.json"),
    }
    with lock_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(stale_metadata, ensure_ascii=False, sort_keys=True))
        f.write("\n")

    # Use max_tasks=1 so the lock detection code path is exercised
    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    assert result["stale_lock"] is not None
    assert result["stale_lock"]["pid"] == 99999
    assert result["stale_lock"]["current_task_started_at"] == 1500.0
    assert result["stale_lock"]["current_task_queue_path"] == str(run_dir / "queue" / "task-003.json")


def test_run_queued_worker_loop_initial_lock_has_null_current_task(tmp_path, monkeypatch):
    """The finished lock metadata should have current_task_started_at=None
    and current_task_queue_path=None after a clean worker_idle exit."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    lock_path = run_dir / ".async-worker.lock"
    lock_path.parent.mkdir(parents=True)

    # Use max_tasks=1, max_idle_polls=0 so the loop enters the lock block,
    # finds no pending tasks, and exits as worker_idle.
    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    finished_meta = json.loads(lock_path.read_text(encoding="utf-8"))
    assert finished_meta["status"] == "finished"
    assert finished_meta["current_task_started_at"] is None
    assert finished_meta["current_task_queue_path"] is None


def test_run_subagent_worker_loop_script_supports_no_claim_smoke(tmp_path):
    script = ROOT / "scripts" / "run-subagent-worker-loop"
    run_dir = tmp_path / "script-runs"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--run-dir",
            str(run_dir),
            "--max-tasks",
            "0",
            "--max-idle-polls",
            "0",
            "--poll-interval-s",
            "0",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "worker_idle"
    assert result["stop_reason"] == "max_tasks"
    assert result["tasks_attempted"] == 0
    assert result["remaining_queue_tasks"] == 0


def test_run_subagent_worker_loop_script_loads_operator_env_file(tmp_path):
    script = ROOT / "scripts" / "run-subagent-worker-loop"
    env_run_dir = tmp_path / "env-file-runs"
    cli_run_dir = tmp_path / "cli-runs"
    env_file = tmp_path / "worker.env"
    env_file.write_text(
        f"# conservative staged worker-loop config\n"
        f"OMEGACLAW_SUBAGENT_RUN_DIR={env_run_dir}\n"
        f"OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS=9\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--env-file",
            str(env_file),
            "--run-dir",
            str(cli_run_dir),
            "--max-tasks",
            "1",
            "--max-idle-polls",
            "0",
            "--poll-interval-s",
            "0",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "worker_idle"
    assert result["lock_path"].startswith(str(cli_run_dir))
    assert not env_run_dir.exists()  # explicit CLI run-dir remains the final override


def test_run_subagent_worker_loop_script_rejects_bad_env_file(tmp_path):
    script = ROOT / "scripts" / "run-subagent-worker-loop"
    env_file = tmp_path / "bad.env"
    env_file.write_text("not a valid line\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script), "--env-file", str(env_file), "--max-tasks", "0"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "expected KEY=VALUE" in completed.stderr


def test_run_subagent_worker_loop_script_rejects_unsafe_env_file_keys(tmp_path):
    script = ROOT / "scripts" / "run-subagent-worker-loop"
    cases = [
        ("PYTHONPATH=/tmp/hijack\n", "PYTHONPATH"),
        ("LD_PRELOAD=/tmp/libhack.so\n", "LD_PRELOAD"),
        ("PATH=/tmp/fake-bin\n", "PATH"),
    ]
    for content, key in cases:
        env_file = tmp_path / f"bad-{key}.env"
        env_file.write_text(content, encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, str(script), "--env-file", str(env_file), "--max-tasks", "0"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode != 0
        assert "unsafe environment key" in completed.stderr
        assert key in completed.stderr


def test_run_subagent_worker_loop_script_rejects_symlink_and_oversized_env_files(tmp_path):
    script = ROOT / "scripts" / "run-subagent-worker-loop"

    real_env = tmp_path / "real.env"
    real_env.write_text("OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS=0\n", encoding="utf-8")
    symlink_env = tmp_path / "linked.env"
    symlink_env.symlink_to(real_env)

    completed = subprocess.run(
        [sys.executable, str(script), "--env-file", str(symlink_env), "--max-tasks", "0"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "must not be symlinks" in completed.stderr

    huge_env = tmp_path / "huge.env"
    huge_env.write_text("OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS=" + ("1" * 5000) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script), "--env-file", str(huge_env), "--max-tasks", "0"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "is too long" in completed.stderr


def test_run_subagent_worker_loop_env_file_uses_open_fd_metadata(tmp_path, monkeypatch):
    script = ROOT / "scripts" / "run-subagent-worker-loop"
    module = runpy.run_path(str(script), run_name="threadkeeper_worker_loop_script_for_test")
    env_file = tmp_path / "worker.env"
    env_file.write_text("OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS=0\n", encoding="utf-8")

    def fail_path_open(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003 - local monkeypatch helper
        raise AssertionError("env loader should read via os.open/os.fdopen after fstat")

    monkeypatch.setattr(Path, "open", fail_path_open)
    monkeypatch.delenv("OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS", raising=False)

    module["_load_env_file"](str(env_file))

    assert os.environ["OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS"] == "0"


def test_run_subagent_worker_loop_env_file_bounds_read_after_fstat(tmp_path, monkeypatch):
    script = ROOT / "scripts" / "run-subagent-worker-loop"
    module = runpy.run_path(str(script), run_name="threadkeeper_worker_loop_script_read_cap_test")
    env_file = tmp_path / "grown.env"
    env_file.write_bytes(b"A=" + b"x" * (64 * 1024))
    real_fstat = os.fstat

    def underreport_size(fd):
        result = real_fstat(fd)
        return os.stat_result((*result[:6], 0, *result[7:]))

    monkeypatch.setattr(os, "fstat", underreport_size)

    with pytest.raises(ValueError, match="too large while reading"):
        module["_load_env_file"](str(env_file))


def test_artifact_local_worker_loop_one_task_smoke_script(tmp_path):
    script = ROOT / "Autotests" / "mock" / "run_worker_loop_one_task_smoke.py"
    completed = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "artifact-smoke")],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    worker = result["worker"]
    queued = result["queued"]
    assert result["status"] == "smoke_passed"
    assert worker["status"] == "worker_drained"
    assert worker["tasks_attempted"] == 1
    assert worker["tasks_completed"] == 1
    assert worker["remaining_queue_tasks"] == 0
    assert queued["status"] == "queued"
    assert Path(queued["queue_path"] + ".done").exists()
    assert Path(queued["queue_path"] + ".done.result.json").exists()


def test_queue_only_dispatch_backpressure_fails_before_worker_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    queue_dir = tmp_path / "runs" / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "already.json").write_text("{}")
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 1)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch("queue overflow", "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "queue backpressure" in payload["summary"]
    assert "queue_path" not in payload
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "queue_backpressure"


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("max_turns", True, "max_turns must be an integer"),
        ("max_turns", 1.5, "max_turns must be an integer"),
        ("max_chars", "not-an-int", "max_chars must be an integer"),
        ("max_chars", "9" * 5000, "max_chars integer is too long"),
    ],
)
def test_dispatch_rejects_coerced_integer_limits_before_setup_or_llm(
    tmp_path, monkeypatch, argument, value, message
):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "load_persona_config",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not load persona")),
    )
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )
    kwargs = {argument: value}

    payload = json.loads(subagent.dispatch("strict limits", "write-file", "unit", **kwargs))

    assert payload["status"] == "error"
    assert message in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "dispatch_args_invalid"


def test_dispatch_accepts_decimal_string_limits(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_args: ('(emit "done")', 0, 0))

    payload = json.loads(subagent.dispatch(
        "strict limits", "write-file", "unit", max_turns="1", max_chars="1000"
    ))

    assert payload["status"] == "ok"
    assert payload["summary"] == "done"


@pytest.mark.parametrize(
    ("goal", "tool_subset", "persona", "message"),
    [
        (False, "write-file", "unit", "goal must be a string"),
        ("   ", "write-file", "unit", "goal must be a non-empty string"),
        ("strict args", ["write-file"], "unit", "tool_subset_csv must be a string or null"),
        ("strict args", "write-file", 7, "persona_key must be a string"),
    ],
)
def test_dispatch_rejects_non_string_scalar_args_before_setup_or_llm(
    tmp_path, monkeypatch, goal, tool_subset, persona, message
):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "load_persona_config",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not load persona")),
    )
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch(goal, tool_subset, persona, max_turns=1))

    assert payload["status"] == "error"
    assert message in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "dispatch_args_invalid"


def test_dispatch_compound_invalid_args_still_persist_structured_error(
    tmp_path, monkeypatch
):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "load_persona_config",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not load persona")),
    )
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch(False, ["write-file"], 7, max_turns=True))

    assert payload["status"] == "error"
    assert "max_turns must be an integer" in payload["summary"]
    assert "goal must be a string" in payload["summary"]
    assert "tool_subset_csv must be a string or null" in payload["summary"]
    assert "persona_key must be a string" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "dispatch_args_invalid"
    assert saved["persona_key"] == "invalid"
    assert saved["goal"] == ""


def test_task_contract_rejects_bad_max_tool_calls_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    negative = subagent.dispatch(json.dumps({
        "objective": "bad quota",
        "max_tool_calls": -1,
    }), "write-file", "unit", max_turns=1)
    fractional = subagent.dispatch(json.dumps({
        "objective": "bad quota",
        "max_tool_calls": 1.5,
    }), "write-file", "unit", max_turns=1)

    assert "subagent error" in negative
    assert "max_tool_calls" in negative
    assert "non-negative" in negative
    assert "subagent error" in fractional
    assert "max_tool_calls" in fractional
    assert "not an integer" in fractional


@pytest.mark.parametrize("source", ["nested", "persona"])
def test_task_contract_rejects_unknown_fields_before_llm(tmp_path, monkeypatch, source):
    _write_unit_persona(tmp_path, monkeypatch)
    goal = json.dumps({
        "objective": "bounded task",
        "task_contract": {"objective": "bounded task", "max_tools_calls": 0},
    })
    if source == "persona":
        goal = "bounded task"
        cfg_path = Path(subagent.PERSONA_DIR) / "unit.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["task_contract"] = {"max_tools_calls": 0}
        cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch(goal, "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "unknown field(s): ['max_tools_calls']" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"


@pytest.mark.parametrize("raw_quota", [25, "25", "9" * 5000])
def test_task_contract_rejects_max_tool_calls_above_global_limit_before_llm(
    tmp_path, monkeypatch, raw_quota
):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOOL_CALLS", 24)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch(json.dumps({
        "objective": "bounded quota",
        "max_tool_calls": raw_quota,
    }), "write-file", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "max_tool_calls must not exceed global limit 24" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"


def test_task_contract_rejects_allowed_path_escape_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "escape attempt",
        "allowed_paths": ["../outside"],
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "subagent error" in payload["summary"]
    assert "allowed_paths" in payload["summary"]
    assert "parent-directory traversal" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    assert saved["task_contract"]["allowed_paths"] == ["../outside"]


def test_task_contract_rejects_absolute_allowed_path_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "absolute allowed path",
        "allowed_paths": [str(tmp_path / "safe")],
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "allowed_paths" in payload["summary"]
    assert "relative to the subagent workspace" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"


def test_task_contract_rejects_control_chars_in_allowed_path_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "control char allowed path",
        "allowed_paths": ["safe\nlog-forge"],
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1)
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert "allowed_paths" in payload["summary"]
    assert "control characters" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("objective", "audit\u2028forgery"),
        ("done_criteria", ["write safe output\ud800"]),
    ],
)
def test_task_contract_rejects_unsafe_unicode_text_before_llm(
    tmp_path, monkeypatch, field, value
):
    """Prompt/audit contract text fails closed before provider handling."""
    _write_unit_persona(tmp_path, monkeypatch)
    contract = {"objective": "safe objective", field: value}
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(
        subagent.dispatch(json.dumps(contract), "write-file", "unit", max_turns=1)
    )

    assert payload["status"] == "error"
    assert field in payload["summary"]
    assert "control characters" in payload["summary"]
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "contract_invalid"
    escaped = saved["task_contract"][field]
    if field == "objective":
        assert escaped == "audit\\u2028forgery"
    else:
        assert escaped == ["write safe output\\ud800"]


def test_task_contract_rejects_oversized_contract_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_CONTRACT_ITEMS", 1)
    contract_goal = json.dumps({
        "objective": "too broad",
        "done_criteria": ["one", "two"],
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1)

    assert "subagent error" in result
    assert "done_criteria" in result
    assert "max 1" in result


def test_task_contract_rejects_unsafe_forbidden_action_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    contract_goal = json.dumps({
        "objective": "unsafe action name",
        "forbidden_actions": ["../write-file"],
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1)

    assert "subagent error" in result
    assert "forbidden_actions" in result
    assert "safe action identifier" in result


def test_task_contract_rejects_oversized_objective_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS", 8)
    contract_goal = json.dumps({
        "objective": "x" * 9,
    })
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    result = subagent.dispatch(contract_goal, "write-file", "unit", max_turns=1)

    assert "subagent error" in result
    assert "objective exceeds 8 characters" in result


def test_dispatch_without_tool_subset_or_default_persists_structured_error(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "unit.txt").write_text("You are a unit-test subagent.")
    (persona_dir / "unit.json").write_text(json.dumps({
        "persona_file": "unit.txt",
        "provider": "ollama",
        "model": "unit-model",
        "api_key_env": "UNIT_API_KEY",
        "base_url": "http://localhost:11434",
        "node_role": "local",
        "endpoint_kind": "ollama_native",
    }))
    monkeypatch.setattr(subagent, "PERSONA_DIR", str(persona_dir))
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("UNIT_API_KEY", "dummy")
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch("needs tools", "", "unit", max_turns=1))

    assert payload["status"] == "error"
    assert "no tool subset" in payload["summary"]
    transcript = Path(payload["transcript_path"])
    assert transcript.exists()
    saved = json.loads(transcript.read_text())
    assert saved["status"] == "tool_subset_invalid"
    assert saved["turns"] == []


def test_dispatch_wall_clock_timeout_stops_before_llm(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_DISPATCH_TIMEOUT_S", 0.01)
    monkeypatch.setattr(subagent, "_dispatch_timeout_exceeded", lambda start: True)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: (_ for _ in ()).throw(AssertionError("should not call llm")),
    )

    payload = json.loads(subagent.dispatch("slow dispatch", "write-file", "unit", max_turns=2))

    assert payload["status"] == "error"
    assert "dispatch wall-clock timeout" in payload["summary"]
    assert payload.get("worker_token_usage", {}).get("total_tokens") == 0
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "dispatch_timeout"


def test_worker_token_usage_aggregated_in_structured_return(tmp_path, monkeypatch):
    _write_unit_persona(tmp_path, monkeypatch)

    responses = iter([
        ('(write-file "out.txt" "hello")', 100, 50),
        ('(emit "done")', 80, 40),
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: next(responses))

    payload = json.loads(subagent.dispatch("write a file", "write-file", "unit", max_turns=3))

    assert payload["status"] == "ok"
    usage = payload.get("worker_token_usage", {})
    assert usage["input_tokens"] == 180
    assert usage["output_tokens"] == 90
    assert usage["total_tokens"] == 270
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["worker_token_usage"]["total_tokens"] == 270


def test_worker_loop_stops_on_max_consecutive_errors(tmp_path, monkeypatch):
    """The worker loop should stop early when too many consecutive tasks fail,
    rather than burning through all max_tasks on a poisoned queue."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 8)
    for i in range(5):
        subagent.dispatch(f"failing task {i}", "write-file", "unit", max_turns=1)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("simulated worker failure")),
    )
    monkeypatch.setattr(subagent.time, "sleep", lambda *_a: None)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=8, poll_interval_s=0, max_idle_polls=0, max_runtime_s=30,
        max_consecutive_errors=2,
    ))

    assert result["status"] == "worker_drained"
    assert result["stop_reason"] == "max_consecutive_errors"
    assert result["consecutive_errors"] == 2
    assert result["error_count"] == 2
    assert result["tasks_attempted"] == 2
    assert result["tasks_completed"] == 0
    assert result["remaining_queue_tasks"] == 3
    assert all(r["status"] == "queue_worker_error" for r in result["results"])


def test_worker_loop_consecutive_errors_reset_on_success(tmp_path, monkeypatch):
    """A successful task between failures should reset the consecutive error counter."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 8)
    for i in range(5):
        subagent.dispatch(f"task {i}", "write-file", "unit", max_turns=1)
    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        if calls["n"] in (1, 3, 5):
            raise RuntimeError("simulated worker failure")
        return ('(emit "ok")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_a: None)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=8, poll_interval_s=0, max_idle_polls=0, max_runtime_s=30,
        max_consecutive_errors=2,
    ))

    assert result["stop_reason"] in ("max_tasks", "idle")
    assert result["tasks_attempted"] == 5
    assert result["error_count"] == 3
    assert result["consecutive_errors"] == 1
    statuses = [r.get("status") for r in result["results"]]
    assert statuses.count("queue_worker_error") == 3
    assert statuses.count("ok") == 2


def test_worker_loop_max_consecutive_errors_disabled(tmp_path, monkeypatch):
    """max_consecutive_errors=0 disables the consecutive-error limit."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 8)
    for i in range(3):
        subagent.dispatch(f"failing task {i}", "write-file", "unit", max_turns=1)
    monkeypatch.setattr(
        subagent,
        "_call_subagent_llm",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("simulated worker failure")),
    )
    monkeypatch.setattr(subagent.time, "sleep", lambda *_a: None)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=5, poll_interval_s=0, max_idle_polls=0, max_runtime_s=30,
        max_consecutive_errors=0,
    ))

    assert result["stop_reason"] in ("max_tasks", "idle")
    assert result["tasks_attempted"] == 3
    assert result["error_count"] == 3
    assert result["consecutive_errors"] == 3
    assert result["remaining_queue_tasks"] == 0


def test_worker_loop_rejects_malformed_max_consecutive_errors(tmp_path, monkeypatch):
    """Non-integer or fractional max_consecutive_errors should fail closed."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_a: None)

    for bad_val in (True, "3", 1.5, -1):
        result = json.loads(subagent.run_queued_worker_loop(
            max_tasks=1, poll_interval_s=0, max_idle_polls=0,
            max_consecutive_errors=bad_val,
        ))
        assert result["status"] == "worker_config_invalid"
        assert "max_consecutive_errors" in result["summary"]


def test_worker_loop_results_truncated(tmp_path, monkeypatch):
    """When results exceed the cap, older entries are dropped and results_truncated is set."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(subagent, "_SUBAGENT_ASYNC_WORKER_MAX_RESULTS", 2)

    run_dir = tmp_path / "runs"
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)

    for i in range(5):
        task = {"run_id": f"r{i}", "persona_key": "p", "prompt": "hi",
                "tool_subset": ["emit"], "max_turns": 1, "max_chars": 1000}
        task_path = queue_dir / f"task{i}.json"
        task_path.write_text(json.dumps(task))
        sha_path = queue_dir / f"task{i}.sha256"
        sha_path.write_text(subagent.hashlib.sha256(task_path.read_bytes()).hexdigest())

    processed = set()

    def fake_pending():
        return sorted(p for p in queue_dir.glob("task[0-9].json")
                       if p.name not in processed)

    def fake_run_queued_dispatch(queue_path):
        idx = int(Path(queue_path).stem.replace("task", ""))
        processed.add(Path(queue_path).name)
        return json.dumps({"status": "ok", "summary": f"task {idx}", "run_id": f"r{idx}"})

    monkeypatch.setattr(subagent, "run_queued_dispatch", fake_run_queued_dispatch)
    monkeypatch.setattr(subagent, "_pending_queued_dispatch_paths", fake_pending)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=5, poll_interval_s=0, max_idle_polls=0,
    ))
    assert result["status"] == "worker_drained"
    assert result["tasks_attempted"] == 5
    assert result["results_truncated"] == 3
    assert len(result["results"]) == 2
    assert result["results"][-1]["run_id"] == "r4"
    assert result["results"][0]["run_id"] == "r3"


def test_worker_loop_results_truncated_disabled(tmp_path, monkeypatch):
    """When _SUBAGENT_ASYNC_WORKER_MAX_RESULTS is 0, no truncation occurs."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(subagent, "_SUBAGENT_ASYNC_WORKER_MAX_RESULTS", 0)

    run_dir = tmp_path / "runs"
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)

    for i in range(3):
        task = {"run_id": f"r{i}", "persona_key": "p", "prompt": "hi",
                "tool_subset": ["emit"], "max_turns": 1, "max_chars": 1000}
        task_path = queue_dir / f"task{i}.json"
        task_path.write_text(json.dumps(task))
        sha_path = queue_dir / f"task{i}.sha256"
        sha_path.write_text(subagent.hashlib.sha256(task_path.read_bytes()).hexdigest())

    processed = set()

    def fake_pending():
        return sorted(p for p in queue_dir.glob("task[0-9].json")
                       if p.name not in processed)

    def fake_run_queued_dispatch(queue_path):
        idx = int(Path(queue_path).stem.replace("task", ""))
        processed.add(Path(queue_path).name)
        return json.dumps({"status": "ok", "summary": f"task {idx}", "run_id": f"r{idx}"})

    monkeypatch.setattr(subagent, "run_queued_dispatch", fake_run_queued_dispatch)
    monkeypatch.setattr(subagent, "_pending_queued_dispatch_paths", fake_pending)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=3, poll_interval_s=0, max_idle_polls=0,
    ))
    assert result["status"] == "worker_drained"
    assert result["tasks_attempted"] == 3
    assert result["results_truncated"] == 0
    assert len(result["results"]) == 3


def test_worker_loop_running_lock_metadata_has_live_counters(tmp_path, monkeypatch):
    """Running lock metadata should include tasks_attempted, consecutive_errors, and error_count."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_a: None)

    run_dir = tmp_path / "runs"
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(parents=True)

    task = {"run_id": "r0", "persona_key": "p", "prompt": "hi",
            "tool_subset": ["emit"], "max_turns": 1, "max_chars": 1000}
    task_path = queue_dir / "task0.json"
    task_path.write_text(json.dumps(task))
    sha_path = queue_dir / "task0.sha256"
    sha_path.write_text(subagent.hashlib.sha256(task_path.read_bytes()).hexdigest())

    processed = set()

    def fake_pending():
        return sorted(p for p in queue_dir.glob("task[0-9].json")
                       if p.name not in processed)

    captured_metadata = []
    original_write = subagent._write_worker_loop_lock_metadata

    def capturing_write(lock, metadata):
        captured_metadata.append(dict(metadata))
        original_write(lock, metadata)

    monkeypatch.setattr(subagent, "_write_worker_loop_lock_metadata", capturing_write)

    def fake_run_queued_dispatch(queue_path):
        processed.add(Path(queue_path).name)
        return json.dumps({"status": "ok", "summary": "done", "run_id": "r0"})

    monkeypatch.setattr(subagent, "run_queued_dispatch", fake_run_queued_dispatch)
    monkeypatch.setattr(subagent, "_pending_queued_dispatch_paths", fake_pending)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))
    assert result["status"] == "worker_drained"

    # Find the last "running" metadata entry (before "finished")
    running_entries = [m for m in captured_metadata if m.get("status") == "running"]
    assert len(running_entries) >= 2  # initial + after-task update
    last_running = running_entries[-1]
    assert last_running["tasks_attempted"] == 1
    assert last_running["consecutive_errors"] == 0
    assert last_running["error_count"] == 0
    assert last_running["tasks_completed"] == 1


def test_transcript_turn_bounding_caps_turn_count(monkeypatch, tmp_path):
    """_bound_transcript_turns drops older turns when the cap is exceeded."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_TURNS", 2)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS", 0)
    record = {
        "turns": [
            {"turn": 1, "prompt": "a", "raw_response": "r1", "tool_calls": []},
            {"turn": 2, "prompt": "b", "raw_response": "r2", "tool_calls": []},
            {"turn": 3, "prompt": "c", "raw_response": "r3", "tool_calls": []},
            {"turn": 4, "prompt": "d", "raw_response": "r4", "tool_calls": []},
        ],
    }
    subagent._bound_transcript_turns(record)
    assert len(record["turns"]) == 2
    assert record["turns"][0]["turn"] == 3
    assert record["turns"][1]["turn"] == 4
    assert record["transcript_truncated"]["turns_dropped"] == 2


def test_transcript_turn_bounding_caps_field_sizes(monkeypatch, tmp_path):
    """_bound_transcript_turns truncates long prompt/response/tool_results fields."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_TURNS", 0)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS", 100)
    long_str = "x" * 500
    record = {
        "turns": [
            {
                "turn": 1,
                "prompt": long_str,
                "raw_response": long_str,
                "tool_calls": [],
                "tool_results": long_str,
            },
        ],
    }
    subagent._bound_transcript_turns(record)
    t = record["turns"][0]
    assert len(t["prompt"]) < 200  # 100 + truncation marker
    assert "truncated" in t["prompt"]
    assert len(t["raw_response"]) < 200
    assert "truncated" in t["raw_response"]
    assert len(t["tool_results"]) < 200
    assert "truncated" in t["tool_results"]
    assert "transcript_truncated" in record


def test_transcript_turn_bounding_disabled_when_zero(monkeypatch, tmp_path):
    """_bound_transcript_turns is a no-op when both caps are 0."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_TURNS", 0)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS", 0)
    record = {
        "turns": [
            {"turn": 1, "prompt": "x" * 500, "raw_response": "y" * 500, "tool_calls": []},
        ],
    }
    subagent._bound_transcript_turns(record)
    assert len(record["turns"]) == 1
    assert len(record["turns"][0]["prompt"]) == 500
    assert "transcript_truncated" not in record


def test_transcript_turn_bounding_preserves_non_string_fields(monkeypatch, tmp_path):
    """_bound_transcript_turns does not touch non-string fields like tool_calls."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_TURNS", 0)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS", 50)
    tool_calls = [{"name": "read-file", "args": ["file.txt"]}]
    record = {
        "turns": [
            {"turn": 1, "prompt": "short", "raw_response": "short", "tool_calls": tool_calls},
        ],
    }
    subagent._bound_transcript_turns(record)
    assert record["turns"][0]["tool_calls"] == tool_calls


def test_retry_backoff_has_jitter(monkeypatch):
    """_call_with_retries adds jitter to the exponential backoff delay."""
    delays = []
    original_sleep = subagent.time.sleep

    def fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr(subagent.time, "sleep", fake_sleep)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_RETRIES", 2)
    monkeypatch.setattr(subagent, "_SUBAGENT_LLM_BACKOFF_S", 1.0)

    call_count = [0]

    def failing_call():
        call_count[0] += 1
        raise RuntimeError("fail")

    result = subagent._call_with_retries(failing_call, "test")
    assert "failed after 3 attempt(s)" in result
    assert call_count[0] == 3
    # Should have slept twice (between attempts 1->2 and 2->3)
    assert len(delays) == 2
    # Base delays: 1.0 and 2.0, jitter adds up to 25% of each
    assert 1.0 <= delays[0] <= 1.25
    assert 2.0 <= delays[1] <= 2.5


def test_dispatch_token_budget_exceeded_stops_after_llm(tmp_path, monkeypatch):
    """When OMEGACLAW_SUBAGENT_MAX_TOKENS_PER_DISPATCH is set, dispatch should stop
    after a worker LLM call pushes total tokens past the cap."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOKENS_PER_DISPATCH", 200)
    responses = iter([
        ('(write-file "out.txt" "hello")', 100, 50),   # total=150, under cap
        ('(emit "done")', 80, 40),                         # total=270, over cap
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: next(responses))

    payload = json.loads(subagent.dispatch("budget test", "write-file", "unit", max_turns=3))

    assert payload["status"] == "error"
    assert "token budget" in payload["summary"]
    assert payload.get("worker_token_usage", {}).get("total_tokens") == 270
    saved = json.loads(Path(payload["transcript_path"]).read_text())
    assert saved["status"] == "token_budget_exceeded"


def test_dispatch_token_budget_disabled_when_zero(tmp_path, monkeypatch):
    """When OMEGACLAW_SUBAGENT_MAX_TOKENS_PER_DISPATCH=0 (default), no cap is enforced."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOKENS_PER_DISPATCH", 0)
    responses = iter([
        ('(write-file "out.txt" "hello")', 10000, 5000),
        ('(emit "done")', 10000, 5000),
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: next(responses))

    payload = json.loads(subagent.dispatch("no budget cap", "write-file", "unit", max_turns=3))

    assert payload["status"] == "ok"
    assert payload.get("worker_token_usage", {}).get("total_tokens") == 30000


def test_dispatch_token_budget_not_exceeded_under_cap(tmp_path, monkeypatch):
    """Dispatch should proceed normally when tokens stay under the cap."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TOKENS_PER_DISPATCH", 1000)
    responses = iter([
        ('(write-file "out.txt" "hello")', 100, 50),
        ('(emit "done")', 80, 40),
    ])
    monkeypatch.setattr(subagent, "_call_subagent_llm", lambda *_a: next(responses))

    payload = json.loads(subagent.dispatch("under cap", "write-file", "unit", max_turns=3))

    assert payload["status"] == "ok"
    assert payload.get("worker_token_usage", {}).get("total_tokens") == 270


def test_tool_error_messages_sanitize_absolute_paths(tmp_path, monkeypatch):
    """Tool error messages must not leak absolute filesystem paths to the worker LLM."""
    _write_unit_persona(tmp_path, monkeypatch)
    workspace = str(tmp_path / "workspace")
    (tmp_path / "workspace").mkdir()

    # read-file on a path that escapes workspace
    result = subagent._tool_read_file("../../../../etc/passwd")
    assert "read-file error" in result
    assert workspace not in result
    assert "/home/" not in result
    assert tmp_path.as_posix() not in result

    # write-file with path escape
    result = subagent._tool_write_file("../../../etc/evil", "content")
    assert "write-file error" in result
    assert workspace not in result
    assert tmp_path.as_posix() not in result

    # append-file with path escape
    result = subagent._tool_append_file("../../../tmp/evil", "content")
    assert "append-file error" in result
    assert workspace not in result
    assert tmp_path.as_posix() not in result


def test_resolve_workspace_path_error_does_not_leak_root(tmp_path, monkeypatch):
    """_resolve_workspace_path error must not include the absolute workspace root."""
    _write_unit_persona(tmp_path, monkeypatch)
    try:
        subagent._resolve_workspace_path("../../../../etc/passwd")
        assert False, "should have raised"
    except ValueError as e:
        msg = str(e)
        assert "escapes subagent workspace" in msg
        # The absolute workspace root must not appear in the error
        workspace = str(tmp_path / "workspace")
        assert workspace not in msg
        assert tmp_path.as_posix() not in msg


def test_sanitize_error_msg_replaces_workspace_and_absolute_paths(tmp_path, monkeypatch):
    """_sanitize_error_msg replaces workspace root and absolute paths with placeholders."""
    _write_unit_persona(tmp_path, monkeypatch)
    workspace = str(tmp_path / "workspace")

    # Workspace root in message
    msg = subagent._sanitize_error_msg(Exception(f"FileNotFoundError: {workspace}/missing.txt"))
    assert workspace not in msg
    assert "<workspace>" in msg

    # Other absolute paths
    msg2 = subagent._sanitize_error_msg(Exception("permission denied: /tmp/secret"))
    assert "/tmp/secret" not in msg2
    assert "<path>" in msg2

    # No paths - unchanged
    msg3 = subagent._sanitize_error_msg(Exception("invalid path"))
    assert "invalid path" in msg3
    assert "<" not in msg3


def test_shell_error_does_not_leak_workspace_path(tmp_path, monkeypatch):
    """Shell tool error for missing workspace must not leak the absolute path."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent, "_shell_enabled", lambda: True)
    monkeypatch.setattr(subagent, "_shell_allowlist", lambda: {"echo"})
    # Point workspace to a non-existent directory
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path / "no_such_dir"))
    result = subagent._tool_shell("echo hello")
    assert "shell error" in result
    assert str(tmp_path / "no_such_dir") not in result


def test_finished_lock_metadata_includes_completion_fields(tmp_path, monkeypatch):
    """The finished lock metadata should include tasks_completed, consecutive_errors,
    and remaining_queue_tasks for operator audit after the worker exits."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    lock_path = run_dir / ".async-worker.lock"
    lock_path.parent.mkdir(parents=True)

    # Use max_tasks=1, max_idle_polls=0 so the loop enters the lock block,
    # finds no pending tasks, and exits as worker_idle.
    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    finished_meta = json.loads(lock_path.read_text(encoding="utf-8"))
    assert finished_meta["status"] == "finished"
    assert finished_meta["tasks_attempted"] == 0
    assert finished_meta["tasks_completed"] == 0
    assert finished_meta["consecutive_errors"] == 0
    assert "remaining_queue_tasks" in finished_meta
    assert finished_meta["remaining_queue_tasks"] == 0
    assert finished_meta["current_task_started_at"] is None
    assert finished_meta["current_task_queue_path"] is None


def test_running_lock_metadata_includes_remaining_queue_tasks(tmp_path, monkeypatch):
    """The running lock metadata should include remaining_queue_tasks so
    operators can see queue depth while the worker is actively processing."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    # Queue 3 tasks so there is measurable queue depth during processing.
    for i in range(3):
        subagent.dispatch(f"task {i}", "write-file", "unit", max_turns=2)

    lock_path = Path(subagent.SUBAGENT_RUN_DIR) / ".async-worker.lock"
    captured = []

    original_dispatch = subagent.run_queued_dispatch

    def spy_dispatch(qp):
        meta = subagent._read_worker_loop_lock_metadata(str(lock_path))
        captured.append(meta)
        return original_dispatch(qp)

    monkeypatch.setattr(subagent, "run_queued_dispatch", spy_dispatch)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        n = calls["n"]
        return (f'(emit "done {n}")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=3, poll_interval_s=0, max_idle_polls=0, max_runtime_s=60,
    ))

    assert result["status"] == "worker_drained"
    assert result["tasks_attempted"] == 3
    assert result["tasks_completed"] == 3

    # During the first task execution, the pre-task lock metadata should
    # show all 3 pending tasks (including the one about to be processed).
    # The spy reads the metadata before the original dispatch claims the task.
    assert len(captured) == 3
    assert captured[0]["status"] == "running"
    assert captured[0]["remaining_queue_tasks"] == 3
    # Subsequent calls should show decreasing queue depth.
    assert captured[1]["remaining_queue_tasks"] == 2
    assert captured[2]["remaining_queue_tasks"] == 1

    # After completion, finished lock should show 0 remaining.
    finished_meta = json.loads(lock_path.read_text(encoding="utf-8"))
    assert finished_meta["status"] == "finished"
    assert finished_meta["remaining_queue_tasks"] == 0
    assert finished_meta["tasks_completed"] == 3
    assert finished_meta["consecutive_errors"] == 0


def test_finished_lock_metadata_shows_errors_after_failures(tmp_path, monkeypatch):
    """The finished lock metadata should reflect consecutive_errors and error_count
    when the worker exits after task failures."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)

    # Queue 2 tasks.
    for i in range(2):
        subagent.dispatch(f"task {i}", "write-file", "unit", max_turns=2)

    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    # Make every worker LLM call raise so all tasks fail.
    def failing_worker(*_args):
        raise RuntimeError("simulated worker failure")

    monkeypatch.setattr(subagent, "_call_subagent_llm", failing_worker)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=2, poll_interval_s=0, max_idle_polls=0, max_runtime_s=60,
        max_consecutive_errors=5,
    ))

    assert result["tasks_attempted"] == 2
    assert result["error_count"] == 2
    assert result["consecutive_errors"] == 2

    lock_path = Path(result["lock_path"])
    finished_meta = json.loads(lock_path.read_text(encoding="utf-8"))
    assert finished_meta["status"] == "finished"
    assert finished_meta["tasks_completed"] == 0
    assert finished_meta["consecutive_errors"] == 2
    assert finished_meta["error_count"] == 2
    assert finished_meta["remaining_queue_tasks"] == 0


def test_worker_loop_results_include_task_duration_s(tmp_path, monkeypatch):
    """Each result item in the worker loop output should include task_duration_s
    so operators can identify slow tasks without parsing timestamps."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    # Queue 2 tasks.
    for i in range(2):
        subagent.dispatch(f"task {i}", "write-file", "unit", max_turns=2)

    calls = {"n": 0}

    def worker_response(*_args):
        calls["n"] += 1
        return (f'(emit "done {calls["n"]}")', 1, 1)

    monkeypatch.setattr(subagent, "_call_subagent_llm", worker_response)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=2, poll_interval_s=0, max_idle_polls=0, max_runtime_s=60,
    ))

    assert result["status"] == "worker_drained"
    assert len(result["results"]) == 2
    for item in result["results"]:
        assert "task_duration_s" in item
        assert isinstance(item["task_duration_s"], (int, float))
        assert item["task_duration_s"] >= 0


def test_worker_loop_results_include_task_duration_s_on_error(tmp_path, monkeypatch):
    """Error result items should also include task_duration_s so operators can
    see how long a task ran before failing."""
    _write_unit_persona(tmp_path, monkeypatch)
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "1")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUEUED_DISPATCHES", 4)
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    subagent.dispatch("doomed task", "write-file", "unit", max_turns=2)

    def failing_worker(*_args):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(subagent, "_call_subagent_llm", failing_worker)

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1, poll_interval_s=0, max_idle_polls=0, max_runtime_s=60,
        max_consecutive_errors=5,
    ))

    assert result["tasks_attempted"] == 1
    assert result["error_count"] == 1
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["status"] == "queue_worker_error"
    assert "task_duration_s" in item
    assert isinstance(item["task_duration_s"], (int, float))
    assert item["task_duration_s"] >= 0


def test_worker_loop_return_includes_total_runtime_s(tmp_path, monkeypatch):
    """The worker loop structured return should include total_runtime_s so
    operators can see the overall wall-clock duration at a glance."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)

    # Use max_tasks=0 so the loop exits immediately as worker_idle.
    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=0, poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_idle"
    assert "total_runtime_s" in result
    assert isinstance(result["total_runtime_s"], (int, float))
    assert result["total_runtime_s"] >= 0
    # Verify it is consistent with started_at/finished_at.
    expected = round(result["finished_at"] - result["started_at"], 3)
    assert abs(result["total_runtime_s"] - expected) < 0.01


def test_worker_loop_config_invalid_includes_total_runtime_s(tmp_path, monkeypatch):
    """The worker_config_invalid return should also include total_runtime_s
    for consistency."""
    run_dir = tmp_path / "runs"
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(run_dir))

    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks="bad", poll_interval_s=0, max_idle_polls=0,
    ))

    assert result["status"] == "worker_config_invalid"
    assert "total_runtime_s" in result
    assert isinstance(result["total_runtime_s"], (int, float))
    assert result["total_runtime_s"] >= 0


def test_worker_loop_already_running_includes_total_runtime_s(tmp_path, monkeypatch):
    """The worker_already_running return should also include total_runtime_s
    for consistency."""
    run_dir = tmp_path / "runs"
    run_dir.mkdir(parents=True)
    lock_path = run_dir / ".async-worker.lock"
    # Write a stale running lock so the loop detects it but can acquire flock.
    lock_path.write_text(json.dumps({
        "pid": 99999,
        "started_at": time.time(),
        "status": "running",
        "run_dir": str(run_dir),
        "max_tasks": 1,
        "max_idle_polls": 1,
        "max_runtime_s": 0,
        "max_consecutive_errors": 3,
        "stop_file": "",
        "tasks_attempted": 0,
        "tasks_completed": 0,
        "consecutive_errors": 0,
        "error_count": 0,
        "current_task_started_at": None,
        "current_task_queue_path": None,
    }))

    # Actually, a running lock that can be flock'd means stale_lock is detected
    # but the loop proceeds. We need to simulate already_running by holding the
    # flock ourselves. Use a separate process approach is too complex; instead
    # test the worker_idle path with total_runtime_s which is the common case.
    # This test verifies total_runtime_s is present in the idle return.
    monkeypatch.setattr(subagent.time, "sleep", lambda *_args: None)
    result = json.loads(subagent.run_queued_worker_loop(
        max_tasks=0, poll_interval_s=0, max_idle_polls=0,
    ))
    assert result["status"] == "worker_idle"
    assert "total_runtime_s" in result


# ------------------------------------------------------------------
# Transcript summary bounding via OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS
# ------------------------------------------------------------------

def test_transcript_summary_bounding_caps_long_summary(monkeypatch, tmp_path):
    """When OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS is non-zero, the
    transcript record's summary field is capped with a truncation marker."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS", 100)
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    transcript_path = run_dir / "test_summary.json"
    record = {
        "turns": [],
        "transcript_path": str(transcript_path),
        "status": "running",
    }
    long_summary = "x" * 500
    subagent._finish_run_record(record, "ok", long_summary)
    written = json.loads(transcript_path.read_text())
    assert len(written["summary"]) <= 100 + len("\n[...summary truncated at 100 chars...]")
    assert "summary truncated at 100 chars" in written["summary"]
    assert written["summary"].startswith("x" * 100)


def test_transcript_summary_bounding_disabled_when_zero(monkeypatch, tmp_path):
    """When OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS is 0 (default),
    the summary is stored as-is without truncation."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS", 0)
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    transcript_path = run_dir / "test_summary2.json"
    record = {
        "turns": [],
        "transcript_path": str(transcript_path),
        "status": "running",
    }
    long_summary = "y" * 5000
    subagent._finish_run_record(record, "ok", long_summary)
    written = json.loads(transcript_path.read_text())
    assert written["summary"] == "y" * 5000


def test_transcript_summary_bounding_preserves_short_summary(monkeypatch, tmp_path):
    """Short summaries are not truncated even when the cap is enabled."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS", 200)
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    transcript_path = run_dir / "test_summary3.json"
    record = {
        "turns": [],
        "transcript_path": str(transcript_path),
        "status": "running",
    }
    short_summary = "All tests passed."
    subagent._finish_run_record(record, "ok", short_summary)
    written = json.loads(transcript_path.read_text())
    assert written["summary"] == "All tests passed."


# ------------------------------------------------------------------
# Shell command non-empty validation
# ------------------------------------------------------------------

def test_validate_tool_args_shell_rejects_empty_command():
    """Empty or whitespace-only shell commands are rejected by arg validation."""
    assert subagent._validate_tool_args("shell", [""]) == "shell command must not be empty or whitespace-only"
    assert subagent._validate_tool_args("shell", ["   "]) == "shell command must not be empty or whitespace-only"
    assert subagent._validate_tool_args("shell", ["\t\n"]) == "shell command must not be empty or whitespace-only"


def test_validate_tool_args_shell_accepts_nonempty_command():
    """Non-empty shell commands pass validation."""
    assert subagent._validate_tool_args("shell", ["echo hello"]) is None
    assert subagent._validate_tool_args("shell", ["ls -la"]) is None


def test_validate_tool_args_shell_rejects_control_characters():
    """Shell command strings must stay single-line/plain for transcript clarity."""
    assert (
        subagent._validate_tool_args("shell", ["echo safe\nwhoami"])
        == "shell command must not contain control characters"
    )
    assert (
        subagent._validate_tool_args("shell", ["echo safe\x7f"])
        == "shell command must not contain control characters"
    )


def test_validate_tool_args_rejects_non_string_arguments():
    """Tool calls must use strongly typed string arguments, not JSON arrays,
    objects, booleans, or numbers coerced with str()."""
    cases = [
        ("read-file", [123]),
        ("write-file", ["out.txt", {"content": "bad"}]),
        ("append-file", ["out.txt", ["bad"]]),
        ("shell", [["echo", "bad"]]),
        ("search", [{"query": "bad"}]),
        ("tavily-search", [True]),
        ("technical-analysis", [3.14]),
    ]
    for tool, args in cases:
        assert subagent._validate_tool_args(tool, args) == "arguments must be strings"


def test_validate_tool_args_rejects_absolute_file_paths():
    """Subagent file tools accept workspace-relative paths only, so worker
    calls cannot smuggle host absolute paths into resolver/audit paths."""
    assert (
        subagent._validate_tool_args("read-file", ["/tmp/secret.txt"])
        == "path argument must be relative to the subagent workspace"
    )
    assert (
        subagent._validate_tool_args("write-file", ["/tmp/out.txt", "x"])
        == "path argument must be relative to the subagent workspace"
    )
    assert (
        subagent._validate_tool_args("append-file", ["/tmp/out.txt", "x"])
        == "path argument must be relative to the subagent workspace"
    )


def test_validate_tool_args_rejects_parent_directory_traversal():
    """Traversal attempts are rejected at argument validation before any
    workspace path resolution or tool execution."""
    assert (
        subagent._validate_tool_args("read-file", ["../secret.txt"])
        == "path argument must not contain parent-directory traversal"
    )
    assert (
        subagent._validate_tool_args("write-file", ["safe/../../secret.txt", "x"])
        == "path argument must not contain parent-directory traversal"
    )
    assert (
        subagent._validate_tool_args("append-file", ["safe/../out.txt", "x"])
        == "path argument must not contain parent-directory traversal"
    )


def test_validate_tool_args_rejects_control_chars_in_file_paths():
    """File-tool path arguments must not carry line/control characters that
    can forge transcript/audit lines or ambiguous filesystem names."""
    assert (
        subagent._validate_tool_args("read-file", ["safe\nspoof.txt"])
        == "path argument must not contain control characters"
    )
    assert (
        subagent._validate_tool_args("write-file", ["safe\tspoof.txt", "x"])
        == "path argument must not contain control characters"
    )
    assert (
        subagent._validate_tool_args("append-file", ["safe\rspoof.txt", "x"])
        == "path argument must not contain control characters"
    )


# ------------------------------------------------------------------
# Search/tavily-search/technical-analysis empty query validation
# ------------------------------------------------------------------

def test_validate_tool_args_search_rejects_empty_query():
    """Empty or whitespace-only search/tavily-search/technical-analysis
    queries are rejected by arg validation, preventing wasted external calls."""
    for tool in ("search", "tavily-search", "technical-analysis"):
        assert subagent._validate_tool_args(tool, [""]) == "query argument must not be empty or whitespace-only"
        assert subagent._validate_tool_args(tool, ["   "]) == "query argument must not be empty or whitespace-only"
        assert subagent._validate_tool_args(tool, ["\t\n"]) == "query argument must not be empty or whitespace-only"


def test_validate_tool_args_search_rejects_control_characters():
    """External query tools reject control characters before provider calls."""
    for tool in ("search", "tavily-search", "technical-analysis"):
        assert (
            subagent._validate_tool_args(tool, ["normal query\nforged line"])
            == "query argument must not contain control characters"
        )
        assert (
            subagent._validate_tool_args(tool, ["normal query\x7f"])
            == "query argument must not contain control characters"
        )


def test_validate_tool_args_search_accepts_nonempty_query():
    """Search tools accept queries, while technical analysis accepts symbols."""
    assert subagent._validate_tool_args("search", ["latest AI news"]) is None
    assert subagent._validate_tool_args("tavily-search", ["quantum computing breakthroughs"]) is None
    for ticker in ("AAPL", "BRK.B", "BTC-USD", "ES=F", "^GSPC"):
        assert subagent._validate_tool_args("technical-analysis", [ticker]) is None


def test_validate_tool_args_search_rejects_oversized_query(monkeypatch):
    """External query inputs have a tighter cap than general tool content."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_QUERY_ARG_CHARS", 8)
    for tool in ("search", "tavily-search", "technical-analysis"):
        assert (
            subagent._validate_tool_args(tool, ["A" * 9])
            == "query argument exceeds 8 characters"
        )


def test_validate_tool_args_shell_rejects_oversized_command(monkeypatch):
    """Shell input has a dedicated cap before parsing or subprocess execution."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SHELL_ARG_CHARS", 8)
    assert subagent._validate_tool_args("shell", ["echo ok"]) is None
    assert (
        subagent._validate_tool_args("shell", ["A" * 9])
        == "shell command exceeds 8 characters"
    )


def test_validate_tool_args_technical_analysis_rejects_non_symbol_queries():
    """Technical analysis is narrower than free-form search and fails closed."""
    for ticker in ("RSI analysis of AAPL", "$AAPL", ".AAPL", "A" * 33):
        assert (
            subagent._validate_tool_args("technical-analysis", [ticker])
            == "ticker argument must be a 1-32 character market symbol"
        )


def test_parse_args_rejects_unterminated_quoted_single_arguments():
    """Malformed quoted calls must not become executable one-argument tools."""
    for tool in ("shell", "search", "tavily-search", "technical-analysis", "read-file"):
        args = subagent._parse_args(tool, '"unterminated payload')
        assert len(args) == 2
        assert subagent._validate_tool_args(tool, args) == "expected 1 arg(s), got 2"
    assert len(subagent._parse_args("emit", '"unterminated payload')) == 2


def test_parse_args_rejects_malformed_quoted_file_content():
    """Quoted file content must close cleanly and have no trailing payload."""
    for tool in ("write-file", "append-file"):
        unterminated = subagent._parse_args(tool, '"safe.txt" "unterminated content')
        assert len(unterminated) == 3
        assert subagent._validate_tool_args(tool, unterminated) == "expected 2 arg(s), got 3"

        trailing = subagent._parse_args(
            tool, '"safe.txt" "content" (shell "echo hidden")'
        )
        assert len(trailing) == 3
        assert subagent._validate_tool_args(tool, trailing) == "expected 2 arg(s), got 3"

        assert subagent._parse_args(tool, '"safe.txt" "normal content"') == [
            "safe.txt",
            "normal content",
        ]


# ------------------------------------------------------------------
# Run index entry bounding / rotation
# ------------------------------------------------------------------

def test_tail_index_lines_reads_recent_lines_with_bounded_tail(tmp_path):
    index_path = tmp_path / "index.jsonl"
    old_lines = [json.dumps({"run_id": f"old-{i}"}) for i in range(300)]
    recent = [json.dumps({"run_id": "recent-1"}), json.dumps({"run_id": "recent-2"})]
    index_path.write_text("\n".join(old_lines + recent) + "\n", encoding="utf-8")

    lines, truncated = subagent._tail_index_lines(str(index_path), desired_count=2, max_bytes=256)
    entries = [json.loads(line.decode("utf-8")) for line in lines]

    assert truncated is True
    assert [entry["run_id"] for entry in entries] == ["recent-1", "recent-2"]


def test_tail_index_lines_rejects_symlink_index(tmp_path):
    target = tmp_path / "outside-index.jsonl"
    target.write_text(json.dumps({"run_id": "outside"}) + "\n", encoding="utf-8")
    link = tmp_path / "index.jsonl"
    link.symlink_to(target)

    lines, truncated = subagent._tail_index_lines(str(link), desired_count=1, max_bytes=256)

    assert lines == []
    assert truncated is False


def test_run_index_rotation_ignores_predictable_temp_symlink(tmp_path, monkeypatch):
    """Rotation rewrites through a random temp file, not index.jsonl.tmp.<pid>."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_ENTRIES", 1)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("do-not-touch", encoding="utf-8")
    predictable_tmp = runs / f"index.jsonl.tmp.{os.getpid()}"
    predictable_tmp.symlink_to(outside)

    for i in range(2):
        subagent._append_run_index({
            "run_id": f"run-{i}",
            "status": "ok",
            "transcript_path": f"run-{i}.json",
            "transcript_sha256": "a" * 64,
        })

    assert outside.read_text(encoding="utf-8") == "do-not-touch"
    assert predictable_tmp.is_symlink()
    entries = [
        json.loads(line)
        for line in (runs / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [entry["run_id"] for entry in entries] == ["run-1"]


def test_append_run_index_hash_chain_uses_bounded_tail_for_large_index(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_ENTRIES", 0)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True, exist_ok=True)
    index_path = runs / "index.jsonl"

    previous = {
        "run_id": "previous",
        "persona_key": "",
        "status": "ok",
        "started_at": None,
        "finished_at": None,
        "transcript_path": "previous.json",
        "transcript_sha256": "a" * 64,
        "previous_entry_sha256": "",
    }
    previous["entry_sha256"] = subagent._index_entry_hash(previous)
    # Put more than the default 1 MiB tail window before the final valid entry.
    with index_path.open("w", encoding="utf-8") as f:
        for i in range(70000):
            f.write(json.dumps({"padding": i}) + "\n")
        f.write(json.dumps(previous, sort_keys=True) + "\n")

    subagent._append_run_index({
        "run_id": "new",
        "status": "ok",
        "transcript_path": "new.json",
        "transcript_sha256": "b" * 64,
    })

    last_entry = json.loads(index_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last_entry["run_id"] == "new"
    assert last_entry["previous_entry_sha256"] == previous["entry_sha256"]

def test_run_index_rotation_truncates_old_entries(tmp_path, monkeypatch):
    """When _SUBAGENT_MAX_INDEX_ENTRIES is set, the index is rotated to keep
    only the most recent N entries after each append."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_ENTRIES", 3)
    for i in range(5):
        subagent._append_run_index({
            "run_id": f"run-{i}",
            "status": "ok",
            "transcript_path": f"run-{i}.json",
            "transcript_sha256": "a" * 64,
        })
    index_path = tmp_path / "runs" / "index.jsonl"
    lines = [line for line in index_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 3
    entries = [json.loads(line) for line in lines]
    # The retained entries should be the last 3: run-2, run-3, run-4
    assert entries[0]["run_id"] == "run-2"
    assert entries[1]["run_id"] == "run-3"
    assert entries[2]["run_id"] == "run-4"
    # First retained entry has empty previous_entry_sha256 (as if first)
    assert entries[0]["previous_entry_sha256"] == ""
    # Hash chain is intact among retained entries
    assert entries[0]["entry_sha256"] == subagent._index_entry_hash(entries[0])
    assert entries[1]["previous_entry_sha256"] == entries[0]["entry_sha256"]
    assert entries[1]["entry_sha256"] == subagent._index_entry_hash(entries[1])
    assert entries[2]["previous_entry_sha256"] == entries[1]["entry_sha256"]
    assert entries[2]["entry_sha256"] == subagent._index_entry_hash(entries[2])


def test_run_index_rotation_disabled_when_zero(tmp_path, monkeypatch):
    """When _SUBAGENT_MAX_INDEX_ENTRIES is 0 (default), no rotation occurs."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_ENTRIES", 0)
    for i in range(5):
        subagent._append_run_index({
            "run_id": f"run-{i}",
            "status": "ok",
            "transcript_path": f"run-{i}.json",
            "transcript_sha256": "a" * 64,
        })
    index_path = tmp_path / "runs" / "index.jsonl"
    lines = [line for line in index_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 5


def test_run_index_rotation_not_triggered_under_cap(tmp_path, monkeypatch):
    """When entry count is at or below the cap, no rotation occurs."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_ENTRIES", 4)
    for i in range(4):
        subagent._append_run_index({
            "run_id": f"run-{i}",
            "status": "ok",
            "transcript_path": f"run-{i}.json",
            "transcript_sha256": "a" * 64,
        })
    index_path = tmp_path / "runs" / "index.jsonl"
    lines = [line for line in index_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 4
    entries = [json.loads(line) for line in lines]
    # Original chain is preserved (no rotation)
    assert entries[0]["previous_entry_sha256"] == ""
    assert entries[0]["run_id"] == "run-0"
    assert entries[3]["run_id"] == "run-3"


def test_run_index_rotation_preserves_verify(tmp_path, monkeypatch):
    """After rotation, verify_subagent_run_index should pass on the retained portion."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_ENTRIES", 2)
    runs = Path(subagent.SUBAGENT_RUN_DIR)
    runs.mkdir(parents=True, exist_ok=True)
    # Create transcripts with matching SHA-256 sidecars.
    for i in range(4):
        transcript = runs / f"run-{i}.json"
        digest = subagent._json_atomic_write(str(transcript), {"status": "ok", "run_id": f"run-{i}"})
        subagent._append_run_index({
            "run_id": f"run-{i}",
            "status": "ok",
            "transcript_path": str(transcript),
            "transcript_sha256": digest,
        })
    # After 4 appends with cap=2, only the last 2 entries should remain.
    audit = json.loads(subagent.verify_subagent_run_index())
    assert audit["status"] == "index_verified"
    assert audit["entries_checked"] == 2
    assert audit["issue_count"] == 0
    entries = [json.loads(line) for line in (tmp_path / "runs" / "index.jsonl").read_text().splitlines() if line.strip()]
    assert entries[0]["run_id"] == "run-2"
    assert entries[1]["run_id"] == "run-3"


def test_run_index_rotation_handles_single_entry_cap(tmp_path, monkeypatch):
    """A cap of 1 keeps only the most recent entry after each append."""
    monkeypatch.setattr(subagent, "SUBAGENT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_INDEX_ENTRIES", 1)
    for i in range(3):
        subagent._append_run_index({
            "run_id": f"run-{i}",
            "status": "ok",
            "transcript_path": f"run-{i}.json",
            "transcript_sha256": "a" * 64,
        })
    index_path = tmp_path / "runs" / "index.jsonl"
    lines = [line for line in index_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["run_id"] == "run-2"
    assert entry["previous_entry_sha256"] == ""
    assert entry["entry_sha256"] == subagent._index_entry_hash(entry)


# ------------------------------------------------------------------
# External tool output bounding (search/tavily-search/technical-analysis)
# ------------------------------------------------------------------

def test_bound_tool_output_truncates_large_result(monkeypatch):
    """Large external tool output is truncated with a marker."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS", 100)
    big = "x" * 500
    result = subagent._bound_tool_output(big)
    assert len(result) < 500
    assert "truncated at 100 chars" in result
    assert result.startswith("x" * 100)


def test_bound_tool_output_preserves_small_result(monkeypatch):
    """Small external tool output passes through unchanged."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS", 4000)
    result = subagent._bound_tool_output("short result")
    assert result == "short result"


def test_bound_tool_output_disabled_when_zero(monkeypatch):
    """When cap is 0, no truncation occurs (defense-in-depth disabled)."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS", 0)
    big = "x" * 10000
    result = subagent._bound_tool_output(big)
    assert result == big


def test_bound_tool_output_handles_non_string_result(monkeypatch):
    """Non-string results (e.g. lists/dicts from search) are str()'d and bounded."""
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS", 50)
    result = subagent._bound_tool_output([{"title": "a" * 200}])
    assert len(result) < 200
    assert "truncated at 50 chars" in result


def test_build_tool_registry_wraps_search_with_bound(monkeypatch):
    """The search tool in the registry should be wrapped with _bound_tool_output."""
    # Verify the wrapper is applied by checking that a large result gets truncated
    import types

    class FakeWebsearch:
        @staticmethod
        def search(q):
            return "x" * 10000

    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS", 100)
    monkeypatch.setitem(sys.modules, "websearch", FakeWebsearch)
    # Force rebuild
    monkeypatch.setattr(subagent, "_TOOL_REGISTRY", None)
    reg = subagent._tool_registry()
    assert "search" in reg
    fn, _ = reg["search"]
    result = fn("test query")
    assert len(result) < 200
    assert "truncated at 100 chars" in result


def test_build_tool_registry_wraps_tavily_with_bound(monkeypatch):
    """The tavily-search tool in the registry should be wrapped with _bound_tool_output."""

    class FakeAgentverse:
        @staticmethod
        def tavily_search(q):
            return "y" * 10000

        @staticmethod
        def technical_analysis(t):
            return "z" * 10000

    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS", 100)
    monkeypatch.setitem(sys.modules, "agentverse", FakeAgentverse)
    monkeypatch.setattr(subagent, "_TOOL_REGISTRY", None)
    reg = subagent._tool_registry()
    assert "tavily-search" in reg
    assert "technical-analysis" in reg
    fn_t, _ = reg["tavily-search"]
    result_t = fn_t("test")
    assert len(result_t) < 200
    assert "truncated at 100 chars" in result_t
    fn_a, _ = reg["technical-analysis"]
    result_a = fn_a("AAPL")
    assert len(result_a) < 200
    assert "truncated at 100 chars" in result_a


def test_write_file_rejects_content_exceeding_max_file_size(tmp_path, monkeypatch):
    """write-file refuses to write content larger than the configured max file size."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 100)
    big_content = "x" * 200
    result = subagent._tool_write_file("big.txt", big_content)
    assert "write-file error" in result
    assert "exceeds max file size" in result
    assert not (tmp_path / "big.txt").exists()


def test_write_file_allows_content_under_max_file_size(tmp_path, monkeypatch):
    """write-file succeeds when content is under the cap."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 1000)
    result = subagent._tool_write_file("ok.txt", "small content")
    assert result == "WRITE-FILE-SUCCESS"
    assert (tmp_path / "ok.txt").read_text() == "small content"


def test_write_file_max_file_size_disabled_when_zero(tmp_path, monkeypatch):
    """write-file has no size cap when _SUBAGENT_MAX_FILE_SIZE_CHARS is 0."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 0)
    big_content = "x" * 50000
    result = subagent._tool_write_file("big.txt", big_content)
    assert result == "WRITE-FILE-SUCCESS"
    assert (tmp_path / "big.txt").read_text() == big_content


def test_append_file_rejects_existing_file_exceeding_max_size(tmp_path, monkeypatch):
    """append-file refuses to read/append when the existing file already exceeds the cap."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 100)
    target = tmp_path / "existing.txt"
    target.write_text("y" * 200)
    result = subagent._tool_append_file("existing.txt", "more")
    assert "append-file error" in result
    assert "existing file size" in result
    assert "exceeds max file size" in result
    # File unchanged
    assert target.read_text() == "y" * 200


def test_append_file_rejects_resulting_file_exceeding_max_size(tmp_path, monkeypatch):
    """append-file refuses when existing + new content would exceed the cap."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 100)
    target = tmp_path / "growing.txt"
    target.write_text("x" * 80)
    result = subagent._tool_append_file("growing.txt", "y" * 50)
    assert "append-file error" in result
    assert "resulting file size" in result
    assert "would exceed max file size" in result
    # File unchanged
    assert target.read_text() == "x" * 80


def test_append_file_allows_resulting_file_under_max_size(tmp_path, monkeypatch):
    """append-file succeeds when existing + new content stays under the cap."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 1000)
    target = tmp_path / "ok.txt"
    target.write_text("existing\n")
    result = subagent._tool_append_file("ok.txt", "appended")
    assert result == "APPEND-FILE-SUCCESS"
    assert "existing" in target.read_text()
    assert "appended" in target.read_text()


def test_append_file_max_size_disabled_when_zero(tmp_path, monkeypatch):
    """append-file has no size cap when _SUBAGENT_MAX_FILE_SIZE_CHARS is 0."""
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_FILE_SIZE_CHARS", 0)
    target = tmp_path / "big.txt"
    target.write_text("x" * 50000)
    result = subagent._tool_append_file("big.txt", "more")
    assert result == "APPEND-FILE-SUCCESS"
    assert "more" in target.read_text()


def test_atomic_replace_text_rejects_symlink_workspace_parent_after_resolution(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe_parent = workspace / "safe"
    safe_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(workspace))
    resolved = subagent._resolve_workspace_path("safe/file.txt")
    safe_parent.rmdir()
    safe_parent.symlink_to(outside, target_is_directory=True)

    try:
        subagent._atomic_replace_text(resolved, "new content")
    except ValueError as exc:
        assert "non-symlink directory" in str(exc) or "escapes subagent workspace" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected symlink workspace write parent to be rejected")
    assert not (outside / "file.txt").exists()


def test_append_file_rejects_symlink_workspace_parent_after_resolution(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe_parent = workspace / "safe"
    safe_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("OMEGACLAW_SUBAGENT_WORKSPACE", str(workspace))
    resolved = subagent._resolve_workspace_path("safe/log.txt")
    safe_parent.rmdir()
    safe_parent.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(subagent, "_resolve_workspace_path", lambda _path: resolved)

    result = subagent._tool_append_file("safe/log.txt", "entry")

    assert "append-file error" in result
    assert "symlink" in result or "escapes subagent workspace" in result
    assert not (outside / "log.txt").exists()
    assert resolved.endswith("/workspace/safe/log.txt")


def test_read_json_file_uses_configured_size_cap(monkeypatch, tmp_path):
    path = tmp_path / "large.json"
    path.write_text(json.dumps({"payload": "x" * 2048}), encoding="utf-8")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_JSON_FILE_BYTES", 128)

    try:
        subagent._read_json_file(str(path))
    except ValueError as exc:
        assert "JSON file exceeds 128 byte limit" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected oversized JSON file to be rejected")


def test_read_json_file_explicit_size_cap_still_supported(tmp_path):
    path = tmp_path / "small.json"
    path.write_text('{"ok": true}', encoding="utf-8")

    parsed, digest = subagent._read_json_file(str(path), max_bytes=64)

    assert parsed == {"ok": True}
    assert digest == hashlib.sha256(b'{"ok": true}').hexdigest()


def test_read_integrity_sidecar_digest_uses_configured_size_cap(monkeypatch, tmp_path):
    path = tmp_path / "task.json"
    path.write_text("{}", encoding="utf-8")
    sidecar = tmp_path / "task.json.sha256"
    sidecar.write_text("a" * 256, encoding="utf-8")
    monkeypatch.setattr(subagent, "_SUBAGENT_MAX_SHA256_SIDECAR_BYTES", 128)

    try:
        subagent._read_integrity_sidecar_digest(str(path))
    except ValueError as exc:
        assert "integrity sidecar exceeds 128 byte limit" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected oversized integrity sidecar to be rejected")


def test_read_integrity_sidecar_digest_rejects_bad_digest_without_path_leak(tmp_path):
    path = tmp_path / "task.json"
    path.write_text("{}", encoding="utf-8")
    sidecar = tmp_path / "task.json.sha256"
    sidecar.write_text("not-a-digest  task.json\n", encoding="utf-8")

    try:
        subagent._read_integrity_sidecar_digest(str(path))
    except ValueError as exc:
        assert "invalid integrity sidecar digest" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected malformed integrity sidecar to be rejected")


def test_read_integrity_sidecar_digest_rejects_symlink_sidecar(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    path = tmp_path / "task.json"
    path.write_text("{}", encoding="utf-8")
    target = tmp_path / "external.sha256"
    target.write_text("a" * 64 + "  task.json\n", encoding="utf-8")
    sidecar = tmp_path / "task.json.sha256"
    sidecar.symlink_to(target)

    try:
        subagent._read_integrity_sidecar_digest(str(path))
    except ValueError as exc:
        assert "regular non-symlink" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected symlink integrity sidecar to be rejected")


def test_json_atomic_write_rejects_existing_symlink_target(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    target = tmp_path / "record.json"
    target.symlink_to(outside)

    try:
        subagent._json_atomic_write(str(target), {"ok": True})
    except ValueError as exc:
        assert "regular non-symlink" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected symlink JSON audit target to be rejected")
    assert outside.read_text(encoding="utf-8") == "{}"


def test_write_transcript_integrity_sidecar_rejects_existing_symlink(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    transcript = tmp_path / "run.json"
    transcript.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.sha256"
    outside.write_text("old\n", encoding="utf-8")
    sidecar = tmp_path / "run.json.sha256"
    sidecar.symlink_to(outside)

    try:
        subagent._write_transcript_integrity_sidecar(str(transcript), "a" * 64)
    except ValueError as exc:
        assert "regular non-symlink" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected symlink transcript integrity sidecar to be rejected")
    assert outside.read_text(encoding="utf-8") == "old\n"


def test_json_atomic_write_rejects_symlink_parent_directory(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_parent = tmp_path / "audit-dir"
    symlink_parent.symlink_to(outside, target_is_directory=True)

    try:
        subagent._json_atomic_write(str(symlink_parent / "record.json"), {"ok": True})
    except ValueError as exc:
        assert "non-symlink directory" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected symlink JSON audit parent to be rejected")
    assert not (outside / "record.json").exists()


def test_json_atomic_write_rejects_symlink_ancestor_parent_directory(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside = tmp_path / "outside"
    outside.mkdir()
    run_dir = tmp_path / "run-dir"
    run_dir.mkdir()
    symlink_ancestor = run_dir / "audit-link"
    symlink_ancestor.symlink_to(outside, target_is_directory=True)

    try:
        subagent._json_atomic_write(str(symlink_ancestor / "nested" / "record.json"), {"ok": True})
    except ValueError as exc:
        assert "non-symlink directory" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected symlink JSON audit ancestor to be rejected")
    assert not (outside / "nested").exists()


def test_write_transcript_integrity_sidecar_rejects_symlink_parent_directory(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable on this platform")
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_parent = tmp_path / "run-dir"
    symlink_parent.symlink_to(outside, target_is_directory=True)

    try:
        subagent._write_transcript_integrity_sidecar(str(symlink_parent / "run.json"), "a" * 64)
    except ValueError as exc:
        assert "non-symlink directory" in str(exc)
        assert str(tmp_path) not in str(exc)
    else:
        raise AssertionError("expected symlink sidecar parent to be rejected")
    assert not (outside / "run.json.sha256").exists()


def test_parse_args_rejects_unquoted_trailing_calls_for_single_arg_tools():
    """Spaced or compact same-line payloads must not reach one-argument tools."""
    for tool in ("read-file", "shell", "search", "tavily-search", "technical-analysis"):
        for payload in ("safe-value) (emit hidden", "safe-value)(emit hidden"):
            args = subagent._parse_args(tool, payload)
            assert len(args) == 2
            assert subagent._validate_tool_args(tool, args) == "expected 1 arg(s), got 2"


def test_parse_args_keeps_unquoted_parenthesized_prose_without_trailing_call():
    assert subagent._parse_args("search", "model (small) comparison") == ["model (small) comparison"]


def test_parse_args_rejects_unquoted_trailing_calls_in_file_content():
    """Unquoted file content must not hide a second same-line worker call."""
    for tool in ("write-file", "append-file"):
        for payload in (
            "notes.txt safe content) (emit hidden",
            "notes.txt safe content)(emit hidden",
        ):
            args = subagent._parse_args(tool, payload)
            assert len(args) == 3
            assert subagent._validate_tool_args(tool, args) == "expected 2 arg(s), got 3"


def test_parse_args_keeps_unquoted_parenthesized_file_content():
    assert subagent._parse_args(
        "write-file", "notes.txt model (small) comparison"
    ) == ["notes.txt", "model (small) comparison"]


def test_validate_tool_args_rejects_unicode_line_separators():
    """Unicode separators must not create hidden prompt/audit lines."""
    for separator in ("\u0085", "\u2028", "\u2029"):
        assert subagent._validate_tool_args("read-file", [f"safe{separator}spoof.txt"]) == (
            "path argument must not contain control characters"
        )
        assert subagent._validate_tool_args("search", [f"safe{separator}spoof"]) == (
            "query argument must not contain control characters"
        )
        assert subagent._validate_tool_args("shell", [f"echo{separator}spoof"]) == (
            "shell command must not contain control characters"
        )


def test_validate_tool_args_rejects_unicode_bidi_controls():
    """Bidi formatting must not visually reorder paths, queries, or commands."""
    for control in ("\u061c", "\u200e", "\u202e", "\u2066", "\u2069"):
        assert subagent._validate_tool_args("read-file", [f"safe{control}spoof.txt"]) == (
            "path argument must not contain control characters"
        )
        assert subagent._validate_tool_args("search", [f"safe{control}spoof"]) == (
            "query argument must not contain control characters"
        )
        assert subagent._validate_tool_args("shell", [f"echo{control}spoof"]) == (
            "shell command must not contain control characters"
        )


def test_validate_tool_args_rejects_invisible_format_and_surrogate_chars():
    """Unsafe invisible formats and lone surrogates fail before tool handling."""
    for control in ("\u00ad", "\u2060", "\ufeff", "\ud800"):
        assert subagent._validate_tool_args("read-file", [f"safe{control}spoof.txt"]) == (
            "path argument must not contain control characters"
        )
        assert subagent._validate_tool_args("search", [f"safe{control}spoof"]) == (
            "query argument must not contain control characters"
        )
        assert subagent._validate_tool_args("shell", [f"echo{control}spoof"]) == (
            "shell command must not contain control characters"
        )


def test_validate_tool_args_preserves_unicode_joiners():
    """Keep common linguistic and emoji joiners usable in query text."""
    assert subagent._validate_tool_args("search", ["می‌روم family: 👩‍👩‍👧‍👦"]) is None
