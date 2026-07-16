#!/usr/bin/env python3
"""Non-live one-task smoke for ThreadKeeper's queued worker loop.

This helper builds an artifact-local run directory and persona, queues exactly
one task through queue-only dispatch, monkeypatches the worker LLM call to a
local deterministic emit, and drains it with run_queued_worker_loop(...). It is
intended for staged install/supervisor validation without Telegram, secrets, or
paid/provider calls.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import subagent  # noqa: E402


def _write_smoke_persona(base: Path) -> Path:
    persona_dir = base / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "artifact-smoke.txt").write_text(
        "You are a deterministic artifact-local smoke subagent. Emit concise success.",
        encoding="utf-8",
    )
    (persona_dir / "artifact-smoke.json").write_text(json.dumps({
        "persona_file": "artifact-smoke.txt",
        "provider": "ollama",
        "model": "artifact-smoke-model",
        "api_key_env": "THREADKEEPER_ARTIFACT_SMOKE_KEY",
        "base_url": "http://localhost:11434",
        "node_role": "local",
        "endpoint_kind": "ollama_native",
        "default_tool_subset": ["write-file"],
    }, indent=2, sort_keys=True), encoding="utf-8")
    return persona_dir


def run_smoke(base: Path) -> dict:
    base.mkdir(parents=True, exist_ok=True)
    persona_dir = _write_smoke_persona(base)
    workspace = base / "workspace"
    run_dir = base / "runs"
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["THREADKEEPER_ARTIFACT_SMOKE_KEY"] = "dummy-local-smoke-key"
    os.environ["OMEGACLAW_SUBAGENT_WORKSPACE"] = str(workspace)
    os.environ["OMEGACLAW_SUBAGENT_QUEUE_ONLY"] = "1"
    subagent.PERSONA_DIR = str(persona_dir)
    subagent.SUBAGENT_RUN_DIR = str(run_dir)
    subagent._SUBAGENT_MAX_QUEUED_DISPATCHES = 2

    queued = json.loads(subagent.dispatch(
        json.dumps({
            "objective": "artifact-local queued worker loop smoke",
            "allowed_paths": ["smoke.txt"],
            "max_tool_calls": 0,
        }, sort_keys=True),
        "write-file",
        "artifact-smoke",
        max_turns=1,
        max_chars=1200,
    ))
    if queued.get("status") != "queued":
        raise RuntimeError(f"expected queued status, got {queued}")

    def fake_worker_llm(*_args):
        return ('(emit "artifact-local queued worker loop smoke ok")', 3, 5)

    subagent._call_subagent_llm = fake_worker_llm
    worker = json.loads(subagent.run_queued_worker_loop(
        max_tasks=1,
        poll_interval_s=0,
        max_idle_polls=0,
        max_runtime_s=30,
    ))
    return {
        "status": "smoke_passed" if worker.get("status") == "worker_drained" else "smoke_failed",
        "queued": queued,
        "worker": worker,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
    }


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv:
        base = Path(argv[0]).resolve()
        result = run_smoke(base)
    else:
        with tempfile.TemporaryDirectory(prefix="threadkeeper-worker-loop-smoke-") as td:
            result = run_smoke(Path(td))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["status"] == "smoke_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
