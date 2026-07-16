"""Subagent dispatch primitive for OmegaClaw.

The `dispatch` function below is the Python target of the MeTTa
`(delegate goal tools persona max_turns)` skill defined in
src/skills.metta. It runs a bounded, narrowly-scoped child LLM loop
against a configurable provider/model/endpoint (per the persona's
JSON config) and returns a single-string digest to the parent loop.

Architectural intent: pair the foundation-model parent (routing
judgment) with a narrow specialist subagent (execution) chosen per
task. The persona config binds each subagent to its own
provider/model/endpoint — typically a smaller, cheaper, or more-
specialized model than the parent runs. See
docs/reference-skills-subagent.md for the skill reference and
docs/tutorial-09-subagents.md for the end-to-end walkthrough.

Provider integration uses lib_llm_ext.AIProvider — instantiated
fresh per dispatch from the persona's JSON config. Stays inside
the existing class abstraction; does not mutate
lib_llm_ext._provider_registry.

The minimal response-cleanup logic below (strip <think> blocks,
strip markdown fences, parse line-leading s-exprs) keeps the
dispatch primitive independent of any specific format-adapter
beyond what reasoning models routinely emit.

v1 scope (documented in docs/reference-skills-subagent.md):
- Tool registry: search, read-file, write-file, append-file, shell
  (restricted), tavily-search, technical-analysis. Excluded:
  remember, query, episodes, pin, metta, send, delegate.
- One dispatch at a time, synchronously.
- No subagent → subagent recursion.
- Digest returned as a single-line string, capped per
  OMEGACLAW_SUBAGENT_MAX_DIGEST_CHARS (default 2000).
"""

import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import tempfile
import uuid
import hashlib
import contextlib
import math
import random
import signal as _signal_module
import unicodedata
try:
    import fcntl
except Exception:  # pragma: no cover - non-Unix fallback
    fcntl = None

# Module-level flag for graceful signal-based worker-loop shutdown.
# When a supervisor sends SIGTERM/SIGINT, the handler sets this flag
# instead of raising, so the current task can finish and the loop
# exits cleanly at the next iteration check.
_worker_signal_state = {"stop_requested": False}


def _worker_signal_handler(signum, frame):
    """Signal handler for graceful worker-loop shutdown."""
    _worker_signal_state["stop_requested"] = True

# Worker-call usage log — SAME file the parent loop + dashboard read, so
# delegated work shows up on the ThreadKeeper mesh's Local Worker tile.
_USAGE_LOG_PATH = os.path.join(
    os.environ.get("MEMORY_DIR", "/PeTTa/repos/OmegaClaw-Core/memory"),
    "usage.jsonl",
)


def _log_worker_usage(model, in_tok, out_tok):
    """Append a worker LLM call to usage.jsonl. Never raises.

    The usage log is operator/accounting state shared with the parent loop and
    dashboard. Treat it like other local audit files: append through the
    regular non-symlink opener so a pre-existing local symlink cannot redirect
    worker accounting writes outside the configured memory directory.
    """
    try:
        rec = {"ts": time.time(), "model": model,
               "input_tokens": int(in_tok or 0), "output_tokens": int(out_tok or 0)}
        parent = os.path.dirname(_USAGE_LOG_PATH)
        if parent:
            _ensure_regular_directory(parent, "worker usage log parent")
        fd = _open_regular_no_symlink(_USAGE_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        _fsync_parent_dir(_USAGE_LOG_PATH)
    except Exception:
        pass


# ----------------------------------------------------------------------
# ThreadKeeper escalation gate.
#
# Delegations to a CLOUD specialist are the expensive node — so before we
# dispatch one, we consult ThreadKeeper's budget policy (which lives in
# src/escalation.metta, evaluated through PeTTa by BudgetTracker). LOCAL
# delegations (Ollama on .41/.248) are free and always proceed ungated.
#
# Fail-CLOSED by default: if the policy can't be evaluated (module missing,
# etc.), cloud delegation is refused. Operators may temporarily restore the
# prototype's historical fail-open behavior with
# OMEGACLAW_SUBAGENT_BUDGET_FALLBACK=allow, but the safe default is deny.
# ----------------------------------------------------------------------
_LOCAL_NODE_ROLES = frozenset(["worker_loop", "control_loop", "local", "worker"])
_CLOUD_NODE_ROLES = frozenset(["cloud_specialist", "cloud", "specialist", "adjudicator"])
_OPENAI_COMPAT_ENDPOINTS = frozenset(["openai_compatible", "openai-compatible", "openai", "cloud"])
_OLLAMA_ENDPOINTS = frozenset(["ollama_native", "ollama-native", "ollama"])


def _node_role(cfg):
    """Return the explicit persona node role.

    Older prototypes guessed cloud/local status from model/base_url strings.
    ThreadKeeper hardening now requires persona metadata to say what kind of
    worker this is, so safety gates do not depend on fragile endpoint names.
    """
    return (cfg.get("node_role") or "").strip().lower()


def _endpoint_kind(cfg):
    """Return explicit provider transport metadata for worker LLM calls.

    `endpoint_kind` is preferred. For compatibility with existing persona files,
    provider names are accepted only as metadata labels (never by base_url/model
    substring). This keeps cloud/local budget classification on `node_role`.
    """
    kind = (cfg.get("endpoint_kind") or cfg.get("provider_transport") or cfg.get("provider") or "").strip().lower()
    if kind in _OLLAMA_ENDPOINTS:
        return "ollama_native"
    if kind in _OPENAI_COMPAT_ENDPOINTS:
        return "openai_compatible"
    return kind


def _persona_is_cloud(cfg):
    """Classify a persona from explicit `node_role` metadata only."""
    role = _node_role(cfg)
    if role in _CLOUD_NODE_ROLES:
        return True
    if role in _LOCAL_NODE_ROLES:
        return False
    raise ValueError(
        "persona config has invalid node_role; expected one of "
        f"{sorted(_LOCAL_NODE_ROLES | _CLOUD_NODE_ROLES)}"
    )


def _escalation_policy_path():
    here = os.path.dirname(os.path.abspath(__file__))
    configured = os.environ.get("OMEGACLAW_ESCALATION_METTA_PATH", "")
    candidates = [
        configured,
        os.path.join(here, "escalation.metta"),
        os.path.join(here, "..", "src", "escalation.metta"),
    ]
    for path in candidates:
        if not path:
            continue
        candidate = os.path.abspath(path)
        try:
            st = os.lstat(candidate)
        except OSError:
            if path == configured:
                return ""
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            if path == configured:
                return ""
            continue
        return candidate
    return ""


def _escalation_policy_integrity():
    """Return (ok, reason) for optional escalation.metta integrity pin.

    Operators can set OMEGACLAW_ESCALATION_METTA_SHA256 to the trusted policy
    hash. When set, mismatches fail closed before any cloud delegation.
    """
    expected = os.environ.get("OMEGACLAW_ESCALATION_METTA_SHA256", "").strip().lower()
    if not expected:
        return (True, "no escalation policy hash configured")
    path = _escalation_policy_path()
    if not path:
        return (False, "escalation.metta not found for integrity check")
    try:
        fd = _open_regular_no_symlink(path, os.O_RDONLY)
        with os.fdopen(fd, "rb") as f:
            if _SUBAGENT_MAX_ESCALATION_POLICY_BYTES:
                policy_size = os.fstat(f.fileno()).st_size
                if policy_size > _SUBAGENT_MAX_ESCALATION_POLICY_BYTES:
                    return (
                        False,
                        "escalation.metta exceeds "
                        f"OMEGACLAW_SUBAGENT_MAX_ESCALATION_POLICY_BYTES={_SUBAGENT_MAX_ESCALATION_POLICY_BYTES}",
                    )
                read_limit = _SUBAGENT_MAX_ESCALATION_POLICY_BYTES + 1
            else:
                read_limit = -1
            payload = f.read(read_limit)
        if _SUBAGENT_MAX_ESCALATION_POLICY_BYTES and len(payload) > _SUBAGENT_MAX_ESCALATION_POLICY_BYTES:
            return (
                False,
                "escalation.metta exceeds "
                f"OMEGACLAW_SUBAGENT_MAX_ESCALATION_POLICY_BYTES={_SUBAGENT_MAX_ESCALATION_POLICY_BYTES}",
            )
        actual = hashlib.sha256(payload).hexdigest()
    except Exception as e:
        return (False, f"escalation.metta integrity read failed: {type(e).__name__}")
    if actual != expected:
        return (False, "escalation.metta integrity mismatch")
    return (True, "escalation.metta integrity ok")


def _escalation_gate(cfg, thread_id="default"):
    """Return (allowed: bool, reason: str). Local → always allow.
    Cloud → ThreadKeeper's MeTTa policy decides. Never raises (fail-closed by
    default, configurable with OMEGACLAW_SUBAGENT_BUDGET_FALLBACK=allow)."""
    if not _persona_is_cloud(cfg):
        return (True, "local node — no budget gate")

    def fallback(reason):
        mode = os.environ.get(
            "OMEGACLAW_SUBAGENT_BUDGET_FALLBACK", "deny"
        ).strip().lower()
        if mode in ("allow", "open", "fail-open", "true", "1"):
            return (True, f"{reason} — fail-open allow by explicit fallback")
        return (False, f"{reason} — fail-closed deny")

    integrity_ok, integrity_reason = _escalation_policy_integrity()
    if not integrity_ok:
        return (False, integrity_reason)

    try:
        # Locate threadkeeper_budget.py: shipped beside this overlay module,
        # or in the repo src/. Add whichever dir holds it to sys.path.
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            here,                                            # overlay/
            os.path.join(here, "..", "src"),                 # repo src/
            os.environ.get("THREADKEEPER_SRC_DIR", ""),
        ]
        BudgetTracker = None
        for d in candidates:
            if d and os.path.isfile(os.path.join(d, "threadkeeper_budget.py")):
                if d not in sys.path:
                    sys.path.insert(0, d)
                from threadkeeper_budget import BudgetTracker  # noqa
                break
        if BudgetTracker is None:
            return fallback("budget module unavailable")
        bt = BudgetTracker()
        # A cloud delegation IS the "this subproblem is hard" signal.
        d = bt.should_escalate(thread_id=thread_id, subproblem_is_hard=True)
        return (bool(d.allowed), d.reason)
    except Exception as e:
        return fallback(f"gate error ({type(e).__name__})")


# Persona-config directory. Configurable via env var; default is
# memory/personas-subagent/ resolved relative to this module's
# parent (i.e. the OmegaClaw-Core repo root).
_DEFAULT_PERSONA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "memory", "personas-subagent"
)
PERSONA_DIR = os.environ.get("OMEGACLAW_SUBAGENT_PERSONA_DIR", _DEFAULT_PERSONA_DIR)

def _env_int(name, default, minimum=1):
    """Read a bounded integer env knob without making import crash.

    ThreadKeeper hardening relies on env-configured caps for retries, quotas,
    transcript digest size, and validation bounds. A malformed value should not
    crash module import or accidentally disable a guard; use the safe default
    and clamp below-minimum values instead.
    """
    raw = os.environ.get(name, str(default))
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value


def _env_float(name, default, minimum=0.0):
    raw = os.environ.get(name, str(default))
    try:
        value = float(str(raw).strip())
    except Exception:
        return default
    if not math.isfinite(value):
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value


# Hard caps. Per-call max_turns is clamped by the lower of dispatch
# arg, persona-config default, and this hard cap. Same for digest.
SUBAGENT_MAX_TURNS_HARD_CAP = _env_int("OMEGACLAW_SUBAGENT_MAX_TURNS", 8, minimum=1)
SUBAGENT_MAX_DIGEST_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_DIGEST_CHARS", 2000, minimum=100)
SUBAGENT_DEFAULT_OUTPUT_TOKENS = 1500
SUBAGENT_MAX_OUTPUT_TOKENS_HARD_CAP = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_OUTPUT_TOKENS", 8192, minimum=1,
)

# Per-subagent-iteration history cap. The subagent's internal history
# is much smaller than the parent's (~4000 chars vs 30000) because
# the subagent operates on a focused goal, not an ongoing
# conversation.
_SUBAGENT_HISTORY_CAP = 4000
_SUBAGENT_RESULTS_CAP = 4000
_SUBAGENT_HISTORY_MAX_TURNS = _env_int("OMEGACLAW_SUBAGENT_HISTORY_MAX_TURNS", 6, minimum=1)

# Shell tool restrictions. Subagent's shell is more restricted than
# parent's — disabled by default, optional executable allowlist, no shell=True,
# output truncated, default 30s timeout.
_SHELL_OUTPUT_CAP = _env_int("OMEGACLAW_SUBAGENT_SHELL_OUTPUT_CAP", 4000, minimum=1)
_SHELL_TIMEOUT_S = _env_float("OMEGACLAW_SUBAGENT_SHELL_TIMEOUT_S", 30.0, minimum=1.0)
_SHELL_MAX_ARGV = _env_int("OMEGACLAW_SUBAGENT_SHELL_MAX_ARGV", 32, minimum=1)

# External tool output cap. Search/tavily-search/technical-analysis return
# arbitrary external API responses. Cap at the tool level (before run_tools
# clips to 2000 chars for the prompt) as defense-in-depth against very large
# in-memory responses from external services.
_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_SEARCH_OUTPUT_CHARS", 4000, minimum=1)

# JSON audit/task read cap. Queue records and reviewable transcript records are
# checksum-verified, but a malformed or adversarial local file should not be
# read into memory unbounded before schema/status validation. Keep the default
# comfortably above normal task/transcript sidecars while allowing operators to
# lower it for constrained deployments/tests.
_SUBAGENT_MAX_JSON_FILE_BYTES = _env_int("OMEGACLAW_SUBAGENT_MAX_JSON_FILE_BYTES", 262144, minimum=1024)

# Checksum sidecar read cap. Integrity sidecars should be tiny sha256 files;
# bound reads before parsing so a tampered ``*.sha256`` cannot force the queue
# worker/reviewer to load an arbitrary local blob into memory.
_SUBAGENT_MAX_SHA256_SIDECAR_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_SHA256_SIDECAR_BYTES", 4096, minimum=128,
)

# Escalation policy integrity read cap. Cloud escalation may optionally pin
# ``escalation.metta`` by sha256; bound the policy read before hashing so a
# malformed/oversized local policy cannot turn a pre-dispatch budget check into
# an unbounded memory read. Set to 0 to disable.
_SUBAGENT_MAX_ESCALATION_POLICY_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_ESCALATION_POLICY_BYTES", 1048576, minimum=0,
)

# Persona config/prompt read caps. Persona files are deployment-controlled, but
# they are still local inputs to dispatch setup and prompt construction. Bound
# reads before JSON parsing/hash checks so malformed local persona artifacts
# cannot become unbounded setup-time memory reads. Set prompt cap to 0 to
# disable for unusual large-prompt deployments.
_SUBAGENT_MAX_PERSONA_CONFIG_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_PERSONA_CONFIG_BYTES", 65536, minimum=1024,
)
_SUBAGENT_MAX_PERSONA_PROMPT_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_PERSONA_PROMPT_BYTES", 262144, minimum=0,
)
_SUBAGENT_MAX_PERSONA_SCALAR_CHARS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_PERSONA_SCALAR_CHARS", 2048, minimum=64,
)

# Subagent LLM call reliability controls. Keep defaults bounded so a stuck
# worker endpoint cannot hang the parent loop indefinitely.
_SUBAGENT_LLM_TIMEOUT_S = _env_int("OMEGACLAW_SUBAGENT_LLM_TIMEOUT_S", 180, minimum=1)
_SUBAGENT_LLM_RETRIES = _env_int("OMEGACLAW_SUBAGENT_LLM_RETRIES", 1, minimum=0)
_SUBAGENT_LLM_BACKOFF_S = _env_float("OMEGACLAW_SUBAGENT_LLM_BACKOFF_S", 1.0, minimum=0.0)
_SUBAGENT_LLM_CALLS_PER_MINUTE = _env_int("OMEGACLAW_SUBAGENT_LLM_CALLS_PER_MINUTE", 60, minimum=0)
_SUBAGENT_MAX_CONCURRENT_LLM_CALLS = _env_int("OMEGACLAW_SUBAGENT_MAX_CONCURRENT_LLM_CALLS", 4, minimum=0)
_SUBAGENT_MAX_LLM_STATE_BYTES = _env_int("OMEGACLAW_SUBAGENT_MAX_LLM_STATE_BYTES", 65536, minimum=1024)

# Per-dispatch safety controls. Tool-call quota bounds work even if a worker
# loops or emits many calls per turn. Cancellation is intentionally file-based
# so supervisors/parents can stop in-flight work without signals or shared state.
_SUBAGENT_MAX_TOOL_CALLS = _env_int("OMEGACLAW_SUBAGENT_MAX_TOOL_CALLS", 24, minimum=0)
_SUBAGENT_MAX_TOOL_CALLS_PER_TURN = _env_int("OMEGACLAW_SUBAGENT_MAX_TOOL_CALLS_PER_TURN", 3, minimum=1)
_SUBAGENT_CANCEL_FILE = os.environ.get("OMEGACLAW_SUBAGENT_CANCEL_FILE", "")
_SUBAGENT_MAX_PATH_ARG_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_PATH_ARG_CHARS", 512, minimum=1)
_SUBAGENT_MAX_TOOL_ARG_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_TOOL_ARG_CHARS", 20000, minimum=1)
_SUBAGENT_MAX_QUERY_ARG_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_QUERY_ARG_CHARS", 4096, minimum=1)
_SUBAGENT_MAX_SHELL_ARG_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_SHELL_ARG_CHARS", 4096, minimum=1)
_SUBAGENT_MAX_READ_FILE_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_READ_FILE_CHARS", 20000, minimum=1)
_SUBAGENT_MAX_CONTRACT_ITEMS = _env_int("OMEGACLAW_SUBAGENT_MAX_CONTRACT_ITEMS", 32, minimum=0)
_SUBAGENT_MAX_CONTRACT_ITEM_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_CONTRACT_ITEM_CHARS", 512, minimum=1)
_SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS = _env_int("OMEGACLAW_SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS", 4000, minimum=1)
_SUBAGENT_MAX_QUEUED_DISPATCHES = _env_int("OMEGACLAW_SUBAGENT_MAX_QUEUED_DISPATCHES", 32, minimum=0)
_SUBAGENT_ASYNC_WORKER_MAX_TASKS = _env_int("OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_TASKS", 32, minimum=0)
_SUBAGENT_ASYNC_WORKER_MAX_IDLE_POLLS = _env_int("OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_IDLE_POLLS", 3, minimum=0)
_SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S = _env_float("OMEGACLAW_SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S", 2.0, minimum=0.0)
_SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S = _env_float("OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S", 600.0, minimum=0.0)
_SUBAGENT_ASYNC_WORKER_STOP_FILE = os.environ.get("OMEGACLAW_SUBAGENT_ASYNC_WORKER_STOP_FILE", "")
_SUBAGENT_ASYNC_WORKER_MAX_CONSECUTIVE_ERRORS = _env_int(
    "OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_CONSECUTIVE_ERRORS", 3, minimum=0,
)
_SUBAGENT_ASYNC_WORKER_MAX_RESULTS = _env_int(
    "OMEGACLAW_SUBAGENT_ASYNC_WORKER_MAX_RESULTS", 16, minimum=0,
)
_SUBAGENT_ASYNC_WORKER_LOCK_METADATA_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_ASYNC_WORKER_LOCK_METADATA_BYTES", 8192, minimum=1024,
)

# Transcript turn bounding. The full turn list (prompts + raw responses + tool
# results) is written to the local transcript file for audit/debug. For
# long-running dispatches this could grow large, so cap the number of turns
# retained in the transcript and the per-field size of each turn entry.
_SUBAGENT_MAX_TRANSCRIPT_TURNS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_TURNS", 0, minimum=0,
)
_SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS", 0, minimum=0,
)
_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS", 0, minimum=0,
)

# Patch-proposal content bounding. In patch_proposal_only mode, proposed
# write-file/append-file payloads are persisted in transcripts for human review
# even though the parent digest exposes only bounded metadata. Keep each stored
# proposal content bounded independently of tool-argument caps so operators can
# tune transcript retention without allowing large proposal blobs.
_SUBAGENT_MAX_PATCH_PROPOSAL_CHARS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_PATCH_PROPOSAL_CHARS", 20000, minimum=1,
)

# Final emit bounding. The parent receives only the structured digest cap, but
# the raw emit also becomes the transcript summary/candidate output. Reject
# oversized final emits at protocol level before treating them as successful
# worker output, keeping transcripts and adjudication candidates bounded even
# when transcript-summary capping is left disabled for compatibility.
_SUBAGENT_MAX_EMIT_CHARS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_EMIT_CHARS", 20000, minimum=1,
)
# Cap worker response text before parsing/persisting tool calls. The provider has
# already returned bytes by this point, but this prevents a single oversized
# response from expanding transcript files or driving unbounded parser work.
_SUBAGENT_MAX_RESPONSE_CHARS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_RESPONSE_CHARS", 50000, minimum=1,
)
# Native Ollama-compatible calls use urllib directly, so bound the raw HTTP body
# before JSON decoding. OpenAI-compatible calls are still bounded at the parsed
# worker-response layer above because the SDK owns transport/body buffering.
_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES", 1048576, minimum=0,
)

# Queue task max age. When non-zero, a queued task older than this many seconds
# is rejected before any worker LLM call, preventing stale/expired work from
# being processed after a long supervisor outage or queue backlog. Set to 0 to
# disable (default).
_SUBAGENT_MAX_QUEUED_TASK_AGE_S = _env_float(
    "OMEGACLAW_SUBAGENT_MAX_QUEUED_TASK_AGE_S", 0.0, minimum=0.0,
)

# Dispatch-level wall-clock timeout. Even if individual LLM calls are bounded,
# a subagent making many fast calls could run for a very long time. This cap
# is checked before each LLM call and tool execution in the dispatch loop.
# Set to 0 to disable.
_SUBAGENT_DISPATCH_TIMEOUT_S = _env_float("OMEGACLAW_SUBAGENT_DISPATCH_TIMEOUT_S", 600.0, minimum=0.0)

# Dispatch-level token budget cap. While worker_token_usage tracks tokens for
# accounting, this optional cap stops a runaway dispatch from consuming
# unbounded tokens across many turns. Set to 0 to disable. When non-zero, the
# dispatch loop checks total accumulated tokens (input + output) after each
# worker LLM call and returns a structured token_budget_exceeded record if the
# cap is exceeded.
_SUBAGENT_MAX_TOKENS_PER_DISPATCH = _env_int("OMEGACLAW_SUBAGENT_MAX_TOKENS_PER_DISPATCH", 0, minimum=0)

# Workspace file size cap. When non-zero, write-file and append-file refuse to
# write or append to a file whose resulting size would exceed this many chars,
# preventing unbounded disk growth from repeated appends and memory
# exhaustion from reading very large existing files. Set to 0 to disable.
_SUBAGENT_MAX_FILE_SIZE_CHARS = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_FILE_SIZE_CHARS", 100000, minimum=0,
)

# Run index entry cap. When non-zero, the compact audit index (index.jsonl) is
# rotated after each append to keep at most the most recent N entries. This
# prevents unbounded index growth in long-running deployments. The hash chain
# is recomputed for retained entries so audit verification still works on the
# retained portion. Set to 0 to disable (default).
_SUBAGENT_MAX_INDEX_ENTRIES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_INDEX_ENTRIES", 0, minimum=0,
)

# Run-index audit read cap. Operators may intentionally leave index rotation
# disabled for append-only evidence, but the read-only verifier should still
# avoid loading/scanning an unbounded or adversarially large local index file.
# Set to 0 to disable.
_SUBAGENT_MAX_INDEX_AUDIT_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_INDEX_AUDIT_BYTES", 1048576, minimum=0,
)

# Transcript audit read cap. verify_subagent_run_index() validates transcript
# hashes referenced by the compact index; cap each local transcript read so a
# corrupt or adversarially large transcript cannot turn a read-only audit into
# an unbounded memory read. Set to 0 to disable.
_SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES = _env_int(
    "OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES", 1048576, minimum=0,
)

# Persistent local run records. Full worker prompts/responses/tool results are
# kept out of the parent context; the parent receives only a bounded structured
# digest plus the local transcript path for audit/debug.
_DEFAULT_SUBAGENT_RUN_DIR = os.path.join(
    os.environ.get("MEMORY_DIR", os.path.join(os.getcwd(), "memory")),
    "subagent-runs",
)
SUBAGENT_RUN_DIR = os.environ.get("OMEGACLAW_SUBAGENT_RUN_DIR", _DEFAULT_SUBAGENT_RUN_DIR)


def _queue_only_enabled():
    return os.environ.get("OMEGACLAW_SUBAGENT_QUEUE_ONLY", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _subagent_workspace_root():
    """Return the filesystem root visible to subagent file tools.

    Defaults to the current working directory so deployments that run the agent
    from the repo keep the historical relative-path ergonomics while closing
    absolute/parent traversal escapes. Override with
    OMEGACLAW_SUBAGENT_WORKSPACE for a narrower or dedicated scratch root.
    """
    root = os.environ.get("OMEGACLAW_SUBAGENT_WORKSPACE") or os.getcwd()
    absolute = os.path.abspath(root)
    try:
        st = os.lstat(absolute)
    except FileNotFoundError:
        # Preserve historical write-file ergonomics: a dedicated workspace root
        # may be created lazily by the atomic write path. Existing workspace
        # roots, however, must be real directories rather than symlinks.
        pass
    except OSError:
        raise ValueError("subagent workspace root stat failed")
    else:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError("subagent workspace root must be a real non-symlink directory")
    return os.path.realpath(absolute)


def _resolve_workspace_path(path):
    if not path or "\x00" in str(path):
        raise ValueError("invalid path")
    root = _subagent_workspace_root()
    raw = str(path)
    candidate = raw if os.path.isabs(raw) else os.path.join(root, raw)
    resolved = os.path.realpath(os.path.abspath(candidate))
    if os.path.commonpath([root, resolved]) != root:
        raise ValueError("path escapes subagent workspace")
    return resolved


def _open_workspace_file_read(path):
    """Open a workspace file for reading without following symlinks.

    This complements ``_resolve_workspace_path`` (which uses ``realpath`` to
    resolve symlinks and check containment) with a defense-in-depth
    ``O_NOFOLLOW`` open so a TOCTOU symlink swap between path resolution and
    the actual read cannot redirect workspace file I/O outside the workspace.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as e:
        if getattr(e, "errno", None) in (40, 17):  # ELOOP, EEXIST on some platforms
            raise ValueError("workspace file is not a regular non-symlink file")
        raise
    try:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise ValueError("workspace file is not a regular non-symlink file")
        return fd
    except Exception:
        os.close(fd)
        raise


def _safe_slug(text, max_len=48):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text or "").strip()).strip("-._")
    return (slug or "run")[:max_len]


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fsync_parent_dir(path):
    """Best-effort fsync of the parent directory after atomic renames.

    File fsync protects the bytes written to the temp file; syncing the parent
    directory makes the replacement/name update durable on filesystems that
    require an explicit directory fsync. This helper is intentionally
    best-effort so portability quirks do not break the agent response path.
    """
    try:
        parent = os.path.dirname(os.path.abspath(path)) or "."
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(parent, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


def _ensure_regular_directory(path, label):
    """Create/validate a local directory tree without accepting symlinks.

    ``os.makedirs(..., exist_ok=True)`` follows symlinked ancestor components,
    so a local ``run-dir/link/child`` path could otherwise create/use ``child``
    under the symlink target before the final-directory check runs.  Walk the
    path component-by-component with ``lstat`` so every existing ancestor is a
    real directory and missing components are created only below already-vetted
    parents.
    """
    if not path:
        return
    abs_path = os.path.abspath(path)
    current = os.path.sep if os.path.isabs(abs_path) else os.curdir
    for part in abs_path.strip(os.path.sep).split(os.path.sep):
        if not part:
            continue
        current = os.path.join(current, part)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current)
                st = os.lstat(current)
            except OSError:
                raise ValueError(f"{label} directory creation failed")
        except OSError:
            raise ValueError(f"{label} directory stat failed")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"{label} directory must be a real non-symlink directory")


def _json_atomic_write(path, data):
    _reject_nonregular_existing_path(path, "JSON audit target")
    parent = os.path.dirname(path)
    if parent:
        _ensure_regular_directory(parent, "JSON audit target parent")
    payload = _json_bytes(data)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=parent or None
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_parent_dir(path)
        return hashlib.sha256(payload).hexdigest()
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _write_transcript_integrity_sidecar(path, digest):
    """Write a small checksum sidecar for local transcript/audit checks."""
    if not path or not digest:
        return ""
    sidecar = f"{path}.sha256"
    _reject_nonregular_existing_path(sidecar, "transcript integrity sidecar")
    parent = os.path.dirname(sidecar)
    if parent:
        _ensure_regular_directory(parent, "transcript integrity sidecar parent")
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(sidecar)}.", suffix=".tmp", dir=parent or None
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{digest}  {os.path.basename(path)}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, sidecar)
        _fsync_parent_dir(sidecar)
        return sidecar
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _read_integrity_sidecar_digest(path):
    """Read and validate a required ``<path>.sha256`` audit sidecar."""
    sidecar = f"{path}.sha256"
    try:
        st = os.lstat(sidecar)
    except FileNotFoundError:
        raise ValueError("missing integrity sidecar")
    except OSError:
        raise ValueError("integrity sidecar stat failed")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("integrity sidecar must be a regular non-symlink file")
    fd = _open_regular_no_symlink(sidecar, os.O_RDONLY)
    with os.fdopen(fd, "rb") as f:
        payload = f.read(_SUBAGENT_MAX_SHA256_SIDECAR_BYTES + 1)
    if len(payload) > _SUBAGENT_MAX_SHA256_SIDECAR_BYTES:
        raise ValueError(
            "integrity sidecar exceeds "
            f"{_SUBAGENT_MAX_SHA256_SIDECAR_BYTES} byte limit"
        )
    try:
        digest = payload.decode("utf-8").strip().split()[0]
    except IndexError:
        raise ValueError("empty integrity sidecar")
    except UnicodeDecodeError:
        raise ValueError("integrity sidecar is not utf-8")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid integrity sidecar digest")
    return digest


def _index_entry_hash(entry):
    """Hash an index entry without its self-referential entry hash field."""
    payload = dict(entry or {})
    payload.pop("entry_sha256", None)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _tail_index_lines(index_path, desired_count=1, max_bytes=1048576):
    """Return recent non-empty index lines without reading an unbounded file.

    The run-index append path only needs the last entry hash, and index
    rotation only needs the most recent N entries. Scan backward in bounded
    chunks so a long-running append-only index cannot turn a single finished
    subagent run into an unbounded memory read. The boolean return value is
    true when older file content was intentionally not read.
    """
    desired_count = max(1, int(desired_count or 1))
    max_bytes = max(1024, int(max_bytes or 1024))
    try:
        st = os.lstat(index_path)
    except FileNotFoundError:
        return [], False
    except Exception:
        return [], False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return [], False
    size = st.st_size
    if size <= 0:
        return [], False

    remaining = min(size, max_bytes)
    offset = size
    buffer = b""
    try:
        fd = _open_regular_no_symlink(index_path, os.O_RDONLY)
    except Exception:
        return [], False
    with os.fdopen(fd, "rb") as f:
        while remaining > 0:
            chunk_size = min(65536, remaining)
            offset -= chunk_size
            remaining -= chunk_size
            f.seek(offset)
            buffer = f.read(chunk_size) + buffer
            lines = [line for line in buffer.splitlines() if line.strip()]
            if len(lines) >= desired_count + 1:
                return lines[-desired_count:], (size > len(buffer))
    lines = [line for line in buffer.splitlines() if line.strip()]
    return lines[-desired_count:], (size > len(buffer))


def _last_index_entry_hash(index_path):
    try:
        st = os.lstat(index_path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return ""
    except FileNotFoundError:
        return ""
    except Exception:
        return ""
    try:
        lines, _truncated = _tail_index_lines(index_path, desired_count=1)
    except FileNotFoundError:
        return ""
    except Exception:
        return ""
    if not lines:
        return ""
    try:
        previous = json.loads(lines[-1].decode("utf-8"))
        prior_hash = str(previous.get("entry_sha256") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", prior_hash):
            return prior_hash
        return _index_entry_hash(previous)
    except Exception:
        return hashlib.sha256(lines[-1] + b"\n").hexdigest()


def _rotate_run_index_if_needed(index_path, lock):
    """Bound the run index by keeping only the most recent entries.

    Reads all current entries, and if the count exceeds the configured cap,
    rewrites the file with only the most recent N entries. The hash chain is
    recomputed for retained entries: the first retained entry gets
    ``previous_entry_sha256 = ""`` (as if it were the first entry) and each
    subsequent entry's ``previous_entry_sha256`` links to the prior retained
    entry's recomputed ``entry_sha256``.

    This is called under the index lock so concurrent appenders are safe.
    """
    try:
        lines, truncated = _tail_index_lines(
            index_path,
            desired_count=max(1, _SUBAGENT_MAX_INDEX_ENTRIES + 1),
        )
        if not truncated and len(lines) <= _SUBAGENT_MAX_INDEX_ENTRIES:
            return
        keep = lines[-_SUBAGENT_MAX_INDEX_ENTRIES:]
        # Parse retained entries and recompute the hash chain.
        rebuilt = []
        prev_hash = ""
        for raw_line in keep:
            entry = json.loads(raw_line.decode("utf-8"))
            entry["previous_entry_sha256"] = prev_hash
            new_hash = _index_entry_hash(entry)
            entry["entry_sha256"] = new_hash
            rebuilt.append(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            prev_hash = new_hash
        # Atomic rewrite via random mkstemp + os.replace. Avoid predictable
        # temp names such as ``index.jsonl.tmp.<pid>`` so a pre-created local
        # symlink cannot redirect the rewrite outside SUBAGENT_RUN_DIR.
        parent = os.path.dirname(index_path) or "."
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(index_path)}.", suffix=".tmp", dir=parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for line in rebuilt:
                    f.write(line)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, index_path)
            _fsync_parent_dir(index_path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass
    except Exception:
        # Rotation failure should not crash the append path; the index
        # remains append-only and unbounded, which is the safe default.
        pass


def _append_run_index(record):
    """Append a compact audit index entry for a finished subagent run.

    Full transcripts stay in per-run JSON files so parent context remains
    bounded. This append-only JSONL index gives operators a cheap local run list
    with transcript checksum/provenance, guarded by a sidecar lock for
    cross-process writers when fcntl is available. Each entry also carries a
    hash-chain link to the prior entry, making local truncation/rewrite drift
    cheap to detect during audit without expanding parent context.
    """
    if not record:
        return ""
    _ensure_regular_directory(SUBAGENT_RUN_DIR, "subagent run dir")
    index_path = os.path.join(SUBAGENT_RUN_DIR, "index.jsonl")
    lock_path = f"{index_path}.lock"
    _reject_nonregular_existing_path(index_path, "subagent run index")
    _reject_nonregular_existing_path(lock_path, "subagent run index lock")
    lock_fd = _open_regular_no_symlink(lock_path, os.O_RDWR | os.O_CREAT)
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _reject_nonregular_existing_path(index_path, "subagent run index")
            entry = {
                "run_id": record.get("run_id", ""),
                "persona_key": record.get("persona_key", ""),
                "status": record.get("status", ""),
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
                "transcript_path": record.get("transcript_path", ""),
                "transcript_sha256": record.get("transcript_sha256", ""),
                "previous_entry_sha256": _last_index_entry_hash(index_path),
            }
            entry["entry_sha256"] = _index_entry_hash(entry)
            line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            index_fd = _open_regular_no_symlink(
                index_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            )
            with os.fdopen(index_fd, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            # Bound the index file by retaining only the most recent
            # entries when a cap is configured. The hash chain is
            # recomputed for retained entries so verify_subagent_run_index
            # still passes on the retained portion.
            if _SUBAGENT_MAX_INDEX_ENTRIES and _SUBAGENT_MAX_INDEX_ENTRIES > 0:
                _rotate_run_index_if_needed(index_path, lock)
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return index_path


def _open_regular_no_symlink(path, flags, mode=0o600):
    """Open a local audit/control/lock file without following symlinks."""
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and (stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)):
        raise ValueError("path is not a regular non-symlink file")
    open_flags = flags
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    fd = os.open(path, open_flags, mode)
    try:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ValueError("path is not a regular non-symlink file")
        return fd
    except Exception:
        os.close(fd)
        raise


def _reject_nonregular_existing_path(path, label):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")


def verify_subagent_run_index(index_path=None):
    """Verify the local subagent run-index hash chain and transcript hashes.

    This is a read-only operator/parent audit helper. It validates the compact
    ``index.jsonl`` chain under ``SUBAGENT_RUN_DIR`` (or an explicit in-run-dir
    path), and for each entry with a local transcript path verifies that the
    transcript still hashes to the recorded SHA-256. It never repairs, rewrites,
    drains queues, calls a worker LLM, or expands transcripts into parent
    context.
    """
    try:
        run_dir = os.path.realpath(os.path.abspath(SUBAGENT_RUN_DIR))
        raw_path = index_path or os.path.join(run_dir, "index.jsonl")
        path = os.path.abspath(str(raw_path))
        resolved_path = os.path.realpath(path)
        if os.path.commonpath([run_dir, resolved_path]) != run_dir:
            raise ValueError(f"subagent run index path escapes run dir ({run_dir}): {index_path}")
        if os.path.basename(path) != "index.jsonl":
            raise ValueError("subagent run index path must be index.jsonl")
        if not os.path.exists(path):
            return json.dumps({
                "status": "index_missing",
                "summary": "subagent run index does not exist",
                "index_path": path,
                "entries_checked": 0,
                "next_action": "no finished subagent records to audit yet",
            }, ensure_ascii=False, sort_keys=True)
        try:
            st = os.lstat(path)
        except OSError as e:
            raise ValueError(f"subagent run index stat failed: {type(e).__name__}")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ValueError("subagent run index must be a regular non-symlink file")
        def _index_too_large_response(size_bytes):
            return json.dumps({
                "status": "index_audit_too_large",
                "summary": (
                    "subagent run index exceeds "
                    f"OMEGACLAW_SUBAGENT_MAX_INDEX_AUDIT_BYTES={_SUBAGENT_MAX_INDEX_AUDIT_BYTES}"
                ),
                "index_path": path,
                "index_size_bytes": size_bytes,
                "entries_checked": 0,
                "transcripts_checked": 0,
                "next_action": "raise audit cap, rotate index, or inspect a bounded copy",
            }, ensure_ascii=False, sort_keys=True)

        if _SUBAGENT_MAX_INDEX_AUDIT_BYTES and st.st_size > _SUBAGENT_MAX_INDEX_AUDIT_BYTES:
            return _index_too_large_response(st.st_size)

        issues = []
        previous_hash = ""
        entries_checked = 0
        transcripts_checked = 0
        index_bytes_read = 0
        index_fd = _open_regular_no_symlink(path, os.O_RDONLY)
        with os.fdopen(index_fd, "rb") as f:
            for line_no, raw_line in enumerate(f, start=1):
                index_bytes_read += len(raw_line)
                if _SUBAGENT_MAX_INDEX_AUDIT_BYTES and index_bytes_read > _SUBAGENT_MAX_INDEX_AUDIT_BYTES:
                    return _index_too_large_response(index_bytes_read)
                line = raw_line.decode("utf-8", errors="replace")
                if not line.strip():
                    continue
                entries_checked += 1
                try:
                    entry = json.loads(line)
                except Exception as e:
                    issues.append({"line": line_no, "issue": f"invalid_json:{type(e).__name__}"})
                    previous_hash = ""
                    continue
                actual_entry_hash = _index_entry_hash(entry)
                recorded_entry_hash = str(entry.get("entry_sha256") or "").strip().lower()
                recorded_previous = str(entry.get("previous_entry_sha256") or "").strip().lower()
                if recorded_entry_hash != actual_entry_hash:
                    issues.append({"line": line_no, "run_id": entry.get("run_id", ""), "issue": "entry_hash_mismatch"})
                if recorded_previous != previous_hash:
                    issues.append({"line": line_no, "run_id": entry.get("run_id", ""), "issue": "previous_hash_mismatch"})
                transcript_path = str(entry.get("transcript_path") or "")
                expected_transcript_hash = str(entry.get("transcript_sha256") or "").strip().lower()
                if transcript_path and expected_transcript_hash:
                    try:
                        resolved_transcript = _resolve_subagent_transcript_path(transcript_path)
                        if _SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES:
                            try:
                                transcript_stat = os.lstat(resolved_transcript)
                            except OSError as e:
                                raise ValueError(f"transcript size check failed: {type(e).__name__}")
                            if stat.S_ISLNK(transcript_stat.st_mode) or not stat.S_ISREG(transcript_stat.st_mode):
                                raise ValueError("transcript audit record must be a regular non-symlink file")
                            transcript_size = transcript_stat.st_size
                            if transcript_size > _SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES:
                                issues.append({
                                    "line": line_no,
                                    "run_id": entry.get("run_id", ""),
                                    "issue": "transcript_too_large",
                                    "transcript_size_bytes": transcript_size,
                                    "max_transcript_audit_bytes": _SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES,
                                })
                                previous_hash = recorded_entry_hash or actual_entry_hash
                                continue
                        actual_transcript_hash, _transcript_bytes = _sha256_file_bounded(
                            resolved_transcript,
                            max_bytes=_SUBAGENT_MAX_TRANSCRIPT_AUDIT_BYTES,
                        )
                        transcripts_checked += 1
                        if actual_transcript_hash != expected_transcript_hash:
                            issues.append({"line": line_no, "run_id": entry.get("run_id", ""), "issue": "transcript_hash_mismatch"})
                    except Exception as e:
                        issues.append({"line": line_no, "run_id": entry.get("run_id", ""), "issue": f"transcript_unverifiable:{type(e).__name__}"})
                previous_hash = recorded_entry_hash or actual_entry_hash
        status = "index_verified" if not issues else "index_tampered"
        return json.dumps({
            "status": status,
            "summary": "subagent run index audit completed",
            "index_path": path,
            "entries_checked": entries_checked,
            "transcripts_checked": transcripts_checked,
            "issues": issues[:20],
            "issue_count": len(issues),
            "last_entry_sha256": previous_hash,
            "next_action": "inspect run index/transcripts before trusting audit trail" if issues else "audit chain verified",
        }, ensure_ascii=False, sort_keys=True)
    except Exception as e:
        return json.dumps({
            "status": "index_audit_error",
            "summary": f"subagent run index audit failed: {type(e).__name__}: {e}",
            "index_path": str(index_path or ""),
            "next_action": "fix index path/integrity before audit",
        }, ensure_ascii=False, sort_keys=True)


def _dispatch_queue_dir():
    return os.path.join(SUBAGENT_RUN_DIR, "queue")


def _queued_dispatch_paths(run_id):
    queue_dir = _dispatch_queue_dir()
    return queue_dir, os.path.join(queue_dir, f"{_safe_slug(run_id, max_len=80)}.json")


def _resolve_run_control_file_path(value, label):
    """Validate a bounded stop/cancel token path under ``SUBAGENT_RUN_DIR``.

    Worker stop files and dispatch cancellation files are control-plane inputs.
    Keep them inside the local subagent run directory so queued task records or
    operator arguments cannot probe arbitrary host paths by checking whether a
    token "exists" elsewhere on the filesystem. Relative values are interpreted
    relative to ``SUBAGENT_RUN_DIR`` for convenience.
    """
    if not value:
        return ""
    raw_value = str(value)
    if (
        _contains_text_control(raw_value)
        or len(raw_value) > _SUBAGENT_MAX_PATH_ARG_CHARS
    ):
        raise ValueError(
            f"{label} must be a bounded path string without control characters "
            f"(max {_SUBAGENT_MAX_PATH_ARG_CHARS} chars)"
        )
    raw = raw_value.strip()
    if not raw:
        return ""
    base = os.path.realpath(os.path.abspath(SUBAGENT_RUN_DIR))
    candidate = raw if os.path.isabs(raw) else os.path.join(base, raw)
    candidate_abs = os.path.abspath(candidate)
    resolved = os.path.realpath(candidate_abs)
    if os.path.commonpath([base, resolved]) != base:
        raise ValueError(f"{label} must stay under subagent run dir ({base})")
    return candidate_abs


def _run_control_token_present(path):
    if not path:
        return False
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    except Exception:
        return False
    return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode)


def _is_pending_queue_task_name(name):
    """Return True only for live ``queue/*.json`` task records.

    Queue workers retain compact JSON result sidecars such as
    ``*.done.result.json`` and ``*.failed.result.json`` for audit. Those files
    live beside pending tasks but must not count as backpressure or be drained
    as new work.
    """
    return (
        name.endswith(".json")
        and not name.startswith(".")
        and not name.endswith(".result.json")
    )


def _is_regular_pending_queue_task_path(queue_dir, name):
    if not _is_pending_queue_task_name(name):
        return False
    try:
        st = os.lstat(os.path.join(queue_dir, name))
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode)


def _pending_dispatch_queue_count():
    try:
        queue_dir = _dispatch_queue_dir()
        _ensure_regular_directory(SUBAGENT_RUN_DIR, "subagent run dir")
        _ensure_regular_directory(queue_dir, "subagent dispatch queue")
        return len([
            name for name in os.listdir(queue_dir)
            if _is_regular_pending_queue_task_path(queue_dir, name)
        ])
    except FileNotFoundError:
        return 0
    except Exception:
        return _SUBAGENT_MAX_QUEUED_DISPATCHES


def _enqueue_dispatch_record(record, tool_names, max_turns, max_chars):
    """Persist a validated dispatch request for an external async worker.

    This is deliberately only an enqueue primitive: it performs the same setup
    validation as synchronous dispatch, writes a durable local task record, and
    returns a bounded parent digest without initializing or calling the worker
    LLM. A separate supervisor/worker can later consume ``queue/*.json`` and
    run the normal synchronous path under the same contracts and cancellation
    controls.
    """
    _ensure_regular_directory(SUBAGENT_RUN_DIR, "subagent run dir")
    if _pending_dispatch_queue_count() >= _SUBAGENT_MAX_QUEUED_DISPATCHES:
        summary = (
            f"subagent dispatch queue backpressure: "
            f"{_SUBAGENT_MAX_QUEUED_DISPATCHES} queued task(s) already pending"
        )
        _finish_run_record(record, "queue_backpressure", summary)
        return _structured_return(
            summary, record, status="error", uncertainty="medium",
            next_action="retry after queued subagent work drains", max_chars=max_chars,
        )

    queue_dir, queue_path = _queued_dispatch_paths(record.get("run_id"))
    _ensure_regular_directory(queue_dir, "subagent dispatch queue")
    queued_at = time.time()
    task = {
        "run_id": record.get("run_id", ""),
        "status": "queued",
        "queued_at": queued_at,
        "persona_key": record.get("persona_key", ""),
        "goal": record.get("goal", ""),
        "tool_subset": list(tool_names or []),
        "max_turns": max_turns,
        "max_chars": max_chars,
        "task_contract": dict(record.get("task_contract") or {}),
        "cancel_file": _resolve_run_control_file_path(_SUBAGENT_CANCEL_FILE, "cancel_file"),
    }
    queue_digest = _json_atomic_write(queue_path, task)
    queue_sidecar = _write_transcript_integrity_sidecar(queue_path, queue_digest)
    record["queue_path"] = queue_path
    record["queue_sha256"] = queue_digest
    record["queue_sha256_path"] = queue_sidecar
    record["queued_at"] = queued_at
    summary = f"subagent dispatch queued for async worker: {queue_path}"
    _finish_run_record(record, "queued", summary)
    return _structured_return(
        summary, record, status="queued", uncertainty="medium",
        next_action="async worker should claim queue_path or parent may cancel via cancel_file",
        max_chars=max_chars,
    )


def _read_json_file(path, max_bytes=None):
    if max_bytes is None:
        max_bytes = _SUBAGENT_MAX_JSON_FILE_BYTES
    fd = _open_regular_no_symlink(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as f:
        payload = f.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"JSON file exceeds {max_bytes} byte limit")
    return json.loads(payload.decode("utf-8")), hashlib.sha256(payload).hexdigest()


def _sha256_file_bounded(path, max_bytes=0, chunk_size=65536):
    """Hash a local file without reading it into memory all at once.

    ``max_bytes=0`` preserves the existing explicit opt-out semantics for audit
    caps, but still streams in fixed-size chunks. When a cap is set, enforce it
    during the read as well as via any caller-side size check so local file
    growth after stat/open validation does not turn an audit into an unbounded
    read.
    """
    cap_bytes = max(0, int(max_bytes or 0))
    chunk = max(1, int(chunk_size or 65536))
    digest = hashlib.sha256()
    total = 0
    fd = _open_regular_no_symlink(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as f:
        while True:
            raw = f.read(chunk)
            if not raw:
                break
            total += len(raw)
            if cap_bytes and total > cap_bytes:
                raise ValueError(f"file exceeds {cap_bytes} byte audit limit")
            digest.update(raw)
    return digest.hexdigest(), total


def _resolve_queue_task_path(queue_path):
    if not queue_path:
        raise ValueError("invalid queued dispatch path")
    raw_queue_path = str(queue_path)
    if (
        len(raw_queue_path) > _SUBAGENT_MAX_PATH_ARG_CHARS
        or _contains_text_control(raw_queue_path)
    ):
        raise ValueError(
            "queued dispatch path must be a bounded path string without control characters"
        )
    _ensure_regular_directory(SUBAGENT_RUN_DIR, "subagent run dir")
    queue_dir_path = _dispatch_queue_dir()
    _ensure_regular_directory(queue_dir_path, "subagent dispatch queue")
    queue_dir = os.path.realpath(os.path.abspath(queue_dir_path))
    raw_candidate = os.path.abspath(raw_queue_path)
    candidate = os.path.realpath(raw_candidate)
    if os.path.commonpath([queue_dir, candidate]) != queue_dir:
        raise ValueError(f"queued dispatch path escapes queue dir ({queue_dir}): {queue_path}")
    if not _is_pending_queue_task_name(os.path.basename(candidate)):
        raise ValueError("queued dispatch path must be a pending queue/*.json task record")
    try:
        st = os.lstat(raw_candidate)
    except FileNotFoundError:
        raise ValueError("queued dispatch task not found")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("queued dispatch path must be a regular non-symlink task record")
    return candidate


def _pending_queued_dispatch_paths():
    """Return pending queued dispatch task paths in deterministic oldest-first order.

    This is intentionally only a local listing helper. It ignores hidden,
    claimed, done, and result files so an operator-supervised worker can drain
    explicit queue records without broad filesystem scanning.
    """
    queue_dir = _dispatch_queue_dir()
    try:
        _ensure_regular_directory(SUBAGENT_RUN_DIR, "subagent run dir")
        _ensure_regular_directory(queue_dir, "subagent dispatch queue")
        names = [
            name for name in os.listdir(queue_dir)
            if _is_regular_pending_queue_task_path(queue_dir, name)
        ]
    except (FileNotFoundError, ValueError):
        return []
    paths = [os.path.join(queue_dir, name) for name in names]

    def _queue_sort_key(path):
        try:
            return (os.lstat(path).st_mtime, path)
        except OSError:
            return (float("inf"), path)

    return sorted(paths, key=_queue_sort_key)


def drain_queued_dispatches(max_tasks=1):
    """Run up to ``max_tasks`` queued subagent dispatches and return JSON.

    This is the bounded, operator-supervised wrapper around
    ``run_queued_dispatch``. It does not daemonize, sleep, poll forever, or start
    itself from dispatch. Supervisors can call it periodically or in a manually
    approved loop; each invocation drains a small, explicit number of existing
    ``queue/*.json`` tasks and returns compact result metadata.
    """
    try:
        limit = int(max_tasks)
    except (TypeError, ValueError):
        limit = 1
    limit = max(0, min(limit, _SUBAGENT_MAX_QUEUED_DISPATCHES or 1))
    results = []
    attempted = 0
    for queue_path in _pending_queued_dispatch_paths()[:limit]:
        attempted += 1
        try:
            results.append(json.loads(run_queued_dispatch(queue_path)))
        except Exception as e:
            results.append({
                "status": "queue_worker_error",
                "summary": f"queued dispatch drain error: {type(e).__name__}: {e}",
                "queue_path": queue_path,
            })
    remaining = len(_pending_queued_dispatch_paths())
    return json.dumps({
        "status": "drained" if results else "queue_empty",
        "tasks_attempted": attempted,
        "tasks_completed": sum(1 for item in results if item.get("status") not in ("queue_worker_error",)),
        "remaining_queue_tasks": remaining,
        "results": results,
    }, ensure_ascii=False, sort_keys=True)


def _worker_loop_lock_path():
    return os.path.join(SUBAGENT_RUN_DIR, ".async-worker.lock")


def _write_worker_loop_lock_metadata(lock, metadata):
    """Best-effort JSON metadata for operator visibility while the lock is held."""
    try:
        lock.seek(0)
        lock.truncate()
        lock.write(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        lock.write("\n")
        lock.flush()
        os.fsync(lock.fileno())
    except Exception:
        pass


def _read_worker_loop_lock_metadata(lock_path):
    try:
        st = os.lstat(lock_path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return {}
        fd = _open_regular_no_symlink(lock_path, os.O_RDONLY)
        with os.fdopen(fd, "rb") as f:
            payload = f.read(_SUBAGENT_ASYNC_WORKER_LOCK_METADATA_BYTES + 1)
        if len(payload) > _SUBAGENT_ASYNC_WORKER_LOCK_METADATA_BYTES:
            return {}
        raw = payload.decode("utf-8").strip()
        if not raw:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _open_worker_loop_lock(lock_path):
    """Open the async-worker lock without following symlinks."""
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("async worker lock path is not a regular file")
        return os.fdopen(fd, "a+", encoding="utf-8")
    except Exception:
        os.close(fd)
        raise


def _coerce_worker_limit(value, default, minimum=0, maximum=None):
    if value is None:
        value = default
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = default
    coerced = max(minimum, coerced)
    if maximum is not None:
        coerced = min(coerced, maximum)
    return coerced


def _validate_worker_limit_arg(value, default, name, minimum=0, maximum=None):
    """Resolve an async-worker integer bound with strict explicit-arg checks.

    Environment knobs are already parsed defensively at import time. Explicit
    Python/operator arguments are closer to tool-call inputs, so fail closed on
    booleans, floats, strings, or below-minimum values instead of silently
    coercing a malformed request into an unintended worker run.
    """
    if value is None:
        return _coerce_worker_limit(default, default, minimum=minimum, maximum=maximum)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None:
        return min(value, maximum)
    return value


def _coerce_worker_float(value, default, minimum=0.0):
    if value is None:
        value = default
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        coerced = default
    if not math.isfinite(coerced):
        coerced = default
    return max(minimum, coerced)


def _validate_worker_float_arg(value, default, name, minimum=0.0):
    """Resolve an async-worker float bound with strict explicit-arg checks."""
    if value is None:
        return _coerce_worker_float(default, default, minimum=minimum)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    coerced = float(value)
    if not math.isfinite(coerced) or coerced < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return coerced


def _validate_worker_stop_file(stop_file):
    return _resolve_run_control_file_path(stop_file, "worker stop_file")


def _worker_stop_requested(stop_file):
    return _run_control_token_present(stop_file)


def run_queued_worker_loop(max_tasks=None, poll_interval_s=None, max_idle_polls=None,
                           stop_file=None, max_runtime_s=None,
                           max_consecutive_errors=None):
    """Run a bounded async worker loop over queued subagent dispatches.

    This is the live worker-loop primitive corresponding to queue-only
    dispatch: it repeatedly claims pending ``queue/*.json`` records via
    ``run_queued_dispatch`` until one of several explicit bounds is reached
    (max tasks, idle polls, runtime, or a stop-file token). It does not start
    itself from ``dispatch`` and it is not a daemon; an operator/supervisor must
    launch it deliberately. A best-effort lock prevents two local worker loops
    from draining the same queue concurrently when ``fcntl`` is available.
    """
    try:
        _ensure_regular_directory(SUBAGENT_RUN_DIR, "subagent run dir")
    except Exception as e:
        now = time.time()
        return json.dumps({
            "status": "worker_config_invalid",
            "summary": f"queued subagent async worker loop config invalid: {type(e).__name__}: {e}",
            "started_at": now,
            "finished_at": now,
            "total_runtime_s": 0.0,
            "next_action": "fix local worker run directory before starting worker loop",
        }, ensure_ascii=False, sort_keys=True)
    started_at = time.time()
    # Defaults for structured config-invalid returns if an early explicit
    # argument check fails before all resolved bounds are assigned.
    task_limit = _coerce_worker_limit(
        _SUBAGENT_ASYNC_WORKER_MAX_TASKS, _SUBAGENT_ASYNC_WORKER_MAX_TASKS, minimum=0,
        maximum=_SUBAGENT_MAX_QUEUED_DISPATCHES or _SUBAGENT_ASYNC_WORKER_MAX_TASKS or None,
    )
    idle_limit = _coerce_worker_limit(
        _SUBAGENT_ASYNC_WORKER_MAX_IDLE_POLLS, _SUBAGENT_ASYNC_WORKER_MAX_IDLE_POLLS, minimum=0,
    )
    poll_interval = _coerce_worker_float(
        _SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S, _SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S, minimum=0.0,
    )
    runtime_limit = _coerce_worker_float(
        _SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S, _SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S, minimum=0.0,
    )
    consecutive_error_limit = _coerce_worker_limit(
        _SUBAGENT_ASYNC_WORKER_MAX_CONSECUTIVE_ERRORS, _SUBAGENT_ASYNC_WORKER_MAX_CONSECUTIVE_ERRORS, minimum=0,
    )
    try:
        task_limit = _validate_worker_limit_arg(
            max_tasks, _SUBAGENT_ASYNC_WORKER_MAX_TASKS, "max_tasks", minimum=0,
            maximum=_SUBAGENT_MAX_QUEUED_DISPATCHES or _SUBAGENT_ASYNC_WORKER_MAX_TASKS or None,
        )
        idle_limit = _validate_worker_limit_arg(
            max_idle_polls, _SUBAGENT_ASYNC_WORKER_MAX_IDLE_POLLS, "max_idle_polls", minimum=0,
        )
        poll_interval = _validate_worker_float_arg(
            poll_interval_s, _SUBAGENT_ASYNC_WORKER_POLL_INTERVAL_S, "poll_interval_s", minimum=0.0,
        )
        runtime_limit = _validate_worker_float_arg(
            max_runtime_s, _SUBAGENT_ASYNC_WORKER_MAX_RUNTIME_S, "max_runtime_s", minimum=0.0,
        )
        consecutive_error_limit = _validate_worker_limit_arg(
            max_consecutive_errors, _SUBAGENT_ASYNC_WORKER_MAX_CONSECUTIVE_ERRORS,
            "max_consecutive_errors", minimum=0,
        )
        stop_path = _validate_worker_stop_file(
            stop_file if stop_file is not None else _SUBAGENT_ASYNC_WORKER_STOP_FILE
        )
    except Exception as e:
        return json.dumps({
            "status": "worker_config_invalid",
            "summary": f"queued subagent async worker loop config invalid: {type(e).__name__}: {e}",
            "started_at": started_at,
            "finished_at": time.time(),
            "total_runtime_s": round(time.time() - started_at, 3),
            "max_tasks": task_limit,
            "poll_interval_s": poll_interval,
            "max_idle_polls": idle_limit,
            "max_runtime_s": runtime_limit,
            "max_consecutive_errors": consecutive_error_limit,
            "stop_file": "",
            "tasks_attempted": 0,
            "tasks_completed": 0,
            "results_truncated": 0,
            "remaining_queue_tasks": len(_pending_queued_dispatch_paths()),
            "results": [],
        }, ensure_ascii=False, sort_keys=True)
    results = []
    tasks_attempted = 0
    idle_polls = 0
    consecutive_errors = 0
    error_count = 0
    if task_limit == 0:
        finished_early = time.time()
        return json.dumps({
            "status": "worker_idle",
            "summary": "queued subagent async worker loop completed bounded run",
            "stop_reason": "max_tasks",
            "started_at": started_at,
            "finished_at": finished_early,
            "total_runtime_s": round(finished_early - started_at, 3),
            "max_tasks": task_limit,
            "poll_interval_s": poll_interval,
            "max_idle_polls": idle_limit,
            "max_runtime_s": runtime_limit,
            "max_consecutive_errors": consecutive_error_limit,
            "stop_file": stop_path,
            "stale_lock": None,
            "tasks_attempted": 0,
            "tasks_completed": 0,
            "results_truncated": 0,
            "remaining_queue_tasks": len(_pending_queued_dispatch_paths()),
            "results": [],
        }, ensure_ascii=False, sort_keys=True)
    stop_reason = ""
    lock_path = _worker_loop_lock_path()
    stale_lock = None

    # Read existing lock metadata before acquiring. If the file shows
    # ``status=running`` but we can acquire the flock, the previous worker
    # died without a clean shutdown (e.g. SIGKILL, OOM). Record this for
    # operator/supervisor audit so crashed workers are visible.
    pre_existing_lock = _read_worker_loop_lock_metadata(lock_path)
    if pre_existing_lock.get("status") == "running":
        stale_lock = pre_existing_lock

    try:
        lock_cm = _open_worker_loop_lock(lock_path)
    except Exception as e:
        return json.dumps({
            "status": "worker_config_invalid",
            "summary": f"queued subagent async worker loop lock invalid: {type(e).__name__}",
            "lock_path": lock_path,
            "started_at": started_at,
            "finished_at": time.time(),
            "total_runtime_s": round(time.time() - started_at, 3),
            "max_tasks": task_limit,
            "poll_interval_s": poll_interval,
            "max_idle_polls": idle_limit,
            "max_runtime_s": runtime_limit,
            "max_consecutive_errors": consecutive_error_limit,
            "stop_file": stop_path,
            "tasks_attempted": 0,
            "tasks_completed": 0,
            "results_truncated": 0,
            "remaining_queue_tasks": len(_pending_queued_dispatch_paths()),
            "results": [],
        }, ensure_ascii=False, sort_keys=True)

    with lock_cm as lock:
        if fcntl is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return json.dumps({
                    "status": "worker_already_running",
                    "summary": "queued subagent async worker loop is already running",
                    "lock_path": lock_path,
                    "worker_lock": _read_worker_loop_lock_metadata(lock_path),
                    "stale_lock": stale_lock,
                    "started_at": started_at,
                    "finished_at": time.time(),
                    "total_runtime_s": round(time.time() - started_at, 3),
                    "tasks_attempted": 0,
                    "tasks_completed": 0,
                    "results_truncated": 0,
                    "remaining_queue_tasks": len(_pending_queued_dispatch_paths()),
                }, ensure_ascii=False, sort_keys=True)
        _write_worker_loop_lock_metadata(lock, {
            "pid": os.getpid(),
            "started_at": started_at,
            "status": "running",
            "run_dir": SUBAGENT_RUN_DIR,
            "max_tasks": task_limit,
            "max_idle_polls": idle_limit,
            "max_runtime_s": runtime_limit,
            "max_consecutive_errors": consecutive_error_limit,
            "stop_file": stop_path,
            "tasks_attempted": 0,
            "tasks_completed": 0,
            "consecutive_errors": 0,
            "error_count": 0,
            "current_task_started_at": None,
            "current_task_queue_path": None,
        })
        # Register graceful signal handlers so supervisors can stop
        # the loop via SIGTERM/SIGINT without orphaning a claimed task or
        # leaving the lock file in "running" state. The handler only sets
        # a flag; the current task finishes normally and the loop exits at
        # the next iteration check.
        _prev_sigterm = None
        _prev_sigint = None
        try:
            _prev_sigterm = _signal_module.signal(
                _signal_module.SIGTERM, _worker_signal_handler
            )
            _prev_sigint = _signal_module.signal(
                _signal_module.SIGINT, _worker_signal_handler
            )
        except (ValueError, OSError):
            # Not in main thread or signals not supported; skip gracefully.
            pass
        try:
            while task_limit <= 0 or tasks_attempted < task_limit:
                if _worker_signal_state["stop_requested"]:
                    stop_reason = "signal"
                    break
                if _worker_stop_requested(stop_path):
                    stop_reason = "stop_file"
                    break
                if runtime_limit and (time.time() - started_at) >= runtime_limit:
                    stop_reason = "max_runtime"
                    break
                pending = _pending_queued_dispatch_paths()
                if not pending:
                    idle_polls += 1
                    if idle_polls > idle_limit:
                        stop_reason = "idle"
                        break
                    if poll_interval:
                        time.sleep(poll_interval)
                    continue
                idle_polls = 0
                queue_path = pending[0]
                tasks_attempted += 1
                task_started_at = time.time()
                # Write lock metadata before starting the task so operators
                # and stale-lock diagnostics can see what was being
                # processed when (if) the worker crashes mid-task.
                _write_worker_loop_lock_metadata(lock, {
                    "pid": os.getpid(),
                    "started_at": started_at,
                    "status": "running",
                    "run_dir": SUBAGENT_RUN_DIR,
                    "max_tasks": task_limit,
                    "max_idle_polls": idle_limit,
                    "max_runtime_s": runtime_limit,
                    "max_consecutive_errors": consecutive_error_limit,
                    "stop_file": stop_path,
                    "tasks_attempted": tasks_attempted,
                    "tasks_completed": sum(1 for item in results if item.get("status") not in ("queue_worker_error",)),
                    "consecutive_errors": consecutive_errors,
                    "error_count": error_count,
                    "remaining_queue_tasks": len(_pending_queued_dispatch_paths()),
                    "current_task_started_at": task_started_at,
                    "current_task_queue_path": queue_path,
                })
                try:
                    result_item = json.loads(run_queued_dispatch(queue_path))
                    if isinstance(result_item, dict):
                        result_item["task_duration_s"] = round(time.time() - task_started_at, 3)
                    results.append(result_item)
                    if isinstance(result_item, dict) and result_item.get("status") == "queue_worker_error":
                        consecutive_errors += 1
                        error_count += 1
                    else:
                        consecutive_errors = 0
                except Exception as e:
                    results.append({
                        "status": "queue_worker_error",
                        "summary": f"queued worker loop error: {type(e).__name__}: {e}",
                        "queue_path": queue_path,
                        "task_duration_s": round(time.time() - task_started_at, 3),
                    })
                    consecutive_errors += 1
                    error_count += 1
                # Update running lock metadata for operator visibility.
                # Clear current_task_* fields since the task is done.
                _write_worker_loop_lock_metadata(lock, {
                    "pid": os.getpid(),
                    "started_at": started_at,
                    "status": "running",
                    "run_dir": SUBAGENT_RUN_DIR,
                    "max_tasks": task_limit,
                    "max_idle_polls": idle_limit,
                    "max_runtime_s": runtime_limit,
                    "max_consecutive_errors": consecutive_error_limit,
                    "stop_file": stop_path,
                    "tasks_attempted": tasks_attempted,
                    "tasks_completed": sum(1 for item in results if item.get("status") not in ("queue_worker_error",)),
                    "consecutive_errors": consecutive_errors,
                    "error_count": error_count,
                    "remaining_queue_tasks": len(_pending_queued_dispatch_paths()),
                    "current_task_started_at": None,
                    "current_task_queue_path": None,
                })
                if (consecutive_error_limit and
                        consecutive_errors >= consecutive_error_limit):
                    stop_reason = "max_consecutive_errors"
                    break
            if not stop_reason:
                stop_reason = "max_tasks"
        finally:
            _write_worker_loop_lock_metadata(lock, {
                "pid": os.getpid(),
                "started_at": started_at,
                "finished_at": time.time(),
                "status": "finished",
                "stop_reason": stop_reason or "unknown",
                "tasks_attempted": tasks_attempted,
                "tasks_completed": sum(1 for item in results if item.get("status") not in ("queue_worker_error",)),
                "consecutive_errors": consecutive_errors,
                "error_count": error_count,
                "remaining_queue_tasks": len(_pending_queued_dispatch_paths()),
                "current_task_started_at": None,
                "current_task_queue_path": None,
            })
            # Restore prior signal handlers so the worker loop does not
            # leak its signal handler into the caller's context.
            if _prev_sigterm is not None:
                try:
                    _signal_module.signal(_signal_module.SIGTERM, _prev_sigterm)
                except (ValueError, OSError):
                    pass
            if _prev_sigint is not None:
                try:
                    _signal_module.signal(_signal_module.SIGINT, _prev_sigint)
                except (ValueError, OSError):
                    pass
            # The signal handler uses module-local state so it can stay
            # async-signal-safe. Clear that state after every bounded worker
            # run; otherwise an operator SIGTERM/SIGINT handled by one loop can
            # poison a later same-process supervised loop into stopping before
            # it checks the queue.
            _worker_signal_state["stop_requested"] = False
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    remaining = len(_pending_queued_dispatch_paths())
    tasks_completed = sum(1 for item in results if item.get("status") not in ("queue_worker_error",))
    results_truncated = 0
    if _SUBAGENT_ASYNC_WORKER_MAX_RESULTS and len(results) > _SUBAGENT_ASYNC_WORKER_MAX_RESULTS:
        results_truncated = len(results) - _SUBAGENT_ASYNC_WORKER_MAX_RESULTS
        results = results[-_SUBAGENT_ASYNC_WORKER_MAX_RESULTS:]
    if results:
        status = "worker_drained"
    elif stop_reason in ("stop_file", "signal"):
        status = "worker_stopped"
    else:
        status = "worker_idle"
    finished_at = time.time()
    return json.dumps({
        "status": status,
        "summary": "queued subagent async worker loop completed bounded run",
        "stop_reason": stop_reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_runtime_s": round(finished_at - started_at, 3),
        "max_tasks": task_limit,
        "poll_interval_s": poll_interval,
        "max_idle_polls": idle_limit,
        "max_runtime_s": runtime_limit,
        "max_consecutive_errors": consecutive_error_limit,
        "stop_file": stop_path,
        "lock_path": lock_path,
        "stale_lock": stale_lock,
        "tasks_attempted": tasks_attempted,
        "tasks_completed": tasks_completed,
        "consecutive_errors": consecutive_errors,
        "error_count": error_count,
        "results_truncated": results_truncated,
        "remaining_queue_tasks": remaining,
        "results": results,
    }, ensure_ascii=False, sort_keys=True)


def _resolve_subagent_transcript_path(transcript_path):
    if not transcript_path or "\x00" in str(transcript_path):
        raise ValueError("invalid subagent transcript path")
    run_dir = os.path.realpath(os.path.abspath(SUBAGENT_RUN_DIR))
    candidate = os.path.abspath(str(transcript_path))
    resolved_candidate = os.path.realpath(candidate)
    if os.path.commonpath([run_dir, resolved_candidate]) != run_dir:
        raise ValueError("subagent transcript path escapes run dir")
    if not candidate.endswith(".json"):
        raise ValueError("subagent transcript path must be a .json run record")
    return candidate


def review_subagent_candidate(transcript_path):
    """Return a non-mutating parent-review summary for a subagent transcript.

    This is a deliberately small parent-side harness for the existing
    ``patch_proposal_only`` and ``requires_adjudication`` contract modes. It
    reads one local transcript under ``SUBAGENT_RUN_DIR``, verifies the optional
    ``.sha256`` sidecar when present, and returns compact JSON that a parent or
    operator can use to decide whether to apply proposed patches or route a
    candidate answer to an adjudicator. It never applies patches, accepts final
    answers, calls an LLM, drains queues, or changes live runtime behavior.
    """
    try:
        path = _resolve_subagent_transcript_path(transcript_path)
        record, digest = _read_json_file(path)
        sidecar_path = f"{path}.sha256"
        sidecar_status = "missing"
        if os.path.exists(sidecar_path):
            sidecar_digest = _read_integrity_sidecar_digest(path)
            if sidecar_digest != digest:
                return json.dumps({
                    "status": "transcript_tampered",
                    "summary": "subagent transcript checksum mismatch",
                    "transcript_path": path,
                    "transcript_sha256": digest,
                    "expected_sha256": sidecar_digest,
                    "next_action": "inspect transcript before trusting child digest",
                }, ensure_ascii=False, sort_keys=True)
            sidecar_status = "verified"

        proposals = record.get("patch_proposals") or []
        adjudication = record.get("adjudication") or {}
        requires_adjudication = bool(
            adjudication.get("required") or
            (record.get("task_contract") or {}).get("requires_adjudication") is True or
            record.get("status") == "adjudication_required"
        )
        proposal_summary = [
            {"action": p.get("action", ""), "path": p.get("path", "")}
            for p in proposals[:20]
            if isinstance(p, dict)
        ]
        gates = []
        if proposal_summary:
            gates.append("patch_proposal_review")
        if requires_adjudication:
            gates.append("adjudication_required")
        status = "candidate_review_ready" if gates else "review_unneeded"
        return json.dumps({
            "status": status,
            "summary": "subagent candidate transcript reviewed without applying changes",
            "transcript_path": path,
            "transcript_sha256": digest,
            "checksum": sidecar_status,
            "run_status": record.get("status", ""),
            "gates": gates,
            "patch_proposals": proposal_summary,
            "adjudication": {
                "required": requires_adjudication,
                "status": adjudication.get("status", ""),
                "candidate_summary": cap(adjudication.get("candidate_summary", record.get("summary", "")), 300),
            },
            "next_action": "operator/parent should inspect transcript and explicitly apply/adjudicate outside this helper",
        }, ensure_ascii=False, sort_keys=True)
    except Exception as e:
        return json.dumps({
            "status": "candidate_review_error",
            "summary": f"subagent candidate review failed: {type(e).__name__}: {_sanitize_error_msg(e)}",
            "transcript_path": os.path.basename(str(transcript_path or "")),
            "next_action": "fix transcript path/integrity before review",
        }, ensure_ascii=False, sort_keys=True)


def _validate_queued_dispatch_task(task):
    if not isinstance(task, dict):
        raise ValueError("queued dispatch task must be a JSON object")
    allowed_keys = {
        "run_id", "status", "queued_at", "persona_key", "goal", "tool_subset",
        "max_turns", "max_chars", "task_contract", "cancel_file",
    }
    unknown_keys = sorted(set(task) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"queued dispatch task has unknown field(s): {unknown_keys}")
    if task.get("status") != "queued":
        raise ValueError("queued dispatch task status must be 'queued'")
    run_id = task.get("run_id", "")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", run_id):
        raise ValueError("queued dispatch task run_id must be a safe bounded identifier")
    queued_at = task.get("queued_at")
    if (
        isinstance(queued_at, bool)
        or not isinstance(queued_at, (int, float))
        or not math.isfinite(float(queued_at))
        or queued_at < 0
    ):
        raise ValueError("queued dispatch task queued_at must be a finite non-negative number")
    if _SUBAGENT_MAX_QUEUED_TASK_AGE_S and isinstance(queued_at, (int, float)) and not isinstance(queued_at, bool):
        task_age = time.time() - float(queued_at)
        if task_age > _SUBAGENT_MAX_QUEUED_TASK_AGE_S:
            raise ValueError(
                f"queued dispatch task expired: age {task_age:.1f}s exceeds max {_SUBAGENT_MAX_QUEUED_TASK_AGE_S}s"
            )
    cancel_file = task.get("cancel_file", "")
    if not isinstance(cancel_file, str):
        raise ValueError("queued dispatch task cancel_file must be a string")
    cancel_file = _resolve_run_control_file_path(cancel_file, "queued dispatch task cancel_file")
    goal = task.get("goal")
    persona_key = task.get("persona_key")
    tool_subset = task.get("tool_subset")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("queued dispatch task goal must be a non-empty string")
    if len(goal) > _SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS:
        raise ValueError(
            f"queued dispatch task goal exceeds {_SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS} characters"
        )
    if not isinstance(persona_key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", persona_key):
        raise ValueError("queued dispatch task persona_key must be a safe persona identifier")
    if not isinstance(tool_subset, list) or not tool_subset:
        raise ValueError("queued dispatch task tool_subset must be a non-empty list")
    if len(tool_subset) > _SUBAGENT_MAX_CONTRACT_ITEMS:
        raise ValueError(f"queued dispatch task tool_subset exceeds {_SUBAGENT_MAX_CONTRACT_ITEMS} items")
    for tool_name in tool_subset:
        if not isinstance(tool_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", tool_name):
            raise ValueError("queued dispatch task tool names must be safe identifiers")
    raw_max_turns = task.get("max_turns", SUBAGENT_MAX_TURNS_HARD_CAP)
    if isinstance(raw_max_turns, bool) or not isinstance(raw_max_turns, int):
        raise ValueError("queued dispatch task max_turns must be an integer")
    max_turns = raw_max_turns
    raw_max_chars = task.get("max_chars", SUBAGENT_MAX_DIGEST_CHARS)
    if isinstance(raw_max_chars, bool) or not isinstance(raw_max_chars, int):
        raise ValueError("queued dispatch task max_chars must be an integer")
    max_chars = raw_max_chars
    task_contract = task.get("task_contract") or {}
    if not isinstance(task_contract, dict):
        raise ValueError("queued dispatch task task_contract must be a JSON object")
    task_contract = dict(task_contract)
    task_contract.setdefault("objective", goal)
    contract_error = _validate_task_contract(task_contract)
    if contract_error:
        raise ValueError(f"queued dispatch task contract invalid: {contract_error}")
    max_turns = max(1, min(max_turns, SUBAGENT_MAX_TURNS_HARD_CAP))
    max_chars = max(100, min(max_chars, SUBAGENT_MAX_DIGEST_CHARS))
    return goal, ",".join(tool_subset), persona_key, max_turns, max_chars, task_contract, cancel_file


def run_queued_dispatch(queue_path):
    """Claim and run one queued subagent dispatch task.

    Queue-only dispatch intentionally writes durable task records but does not
    start a worker. This helper is the corresponding small worker primitive for
    an external supervisor: atomically rename one ``queue/*.json`` task to a
    claimed path, revalidate the task shape, run normal synchronous dispatch
    with queue-only mode suppressed, and write a compact ``*.result.json``
    record. The original task is left as ``*.done`` for audit so a task is not
    silently re-run. If validation or execution fails after a claim, the claimed
    task is retained as ``*.failed`` with a compact ``*.failed.result.json``
    sidecar instead of being left in limbo.
    """
    task_path = None
    claimed_path = None
    task_sha256 = None
    expected_task_sha256 = None
    try:
        task_path = _resolve_queue_task_path(queue_path)
        claimed_path = f"{task_path}.claimed"
        try:
            os.replace(task_path, claimed_path)
        except FileNotFoundError:
            raise ValueError(f"queued dispatch task not found: {task_path}")
        expected_task_sha256 = _read_integrity_sidecar_digest(task_path)
        task, task_sha256 = _read_json_file(claimed_path)
        if task_sha256 != expected_task_sha256:
            raise ValueError("queued dispatch task checksum mismatch")
        try:
            os.unlink(f"{task_path}.sha256")
        except FileNotFoundError:
            pass
        goal, tool_subset_csv, persona_key, max_turns, max_chars, task_contract, cancel_file = _validate_queued_dispatch_task(task)
        dispatch_goal = json.dumps({
            "objective": goal,
            "task_contract": task_contract,
        }, ensure_ascii=False, sort_keys=True)
        previous_queue_only = os.environ.pop("OMEGACLAW_SUBAGENT_QUEUE_ONLY", None)
        previous_cancel_file = globals().get("_SUBAGENT_CANCEL_FILE", "")
        if cancel_file:
            globals()["_SUBAGENT_CANCEL_FILE"] = cancel_file
        try:
            result_text = dispatch(dispatch_goal, tool_subset_csv, persona_key, max_turns=max_turns, max_chars=max_chars)
        finally:
            globals()["_SUBAGENT_CANCEL_FILE"] = previous_cancel_file
            if previous_queue_only is not None:
                os.environ["OMEGACLAW_SUBAGENT_QUEUE_ONLY"] = previous_queue_only
        try:
            result_payload = json.loads(result_text)
            status = result_payload.get("status", "unknown") if isinstance(result_payload, dict) else "unknown"
        except Exception:
            result_payload = {"raw_result": result_text}
            status = "unknown"
        result_record = {
            "queue_path": task_path,
            "claimed_path": claimed_path,
            "task_sha256": task_sha256,
            "finished_at": time.time(),
            "status": status,
            "result": result_payload,
        }
        done_path = f"{task_path}.done"
        os.replace(claimed_path, done_path)
        result_record["task_done_path"] = done_path
        result_record["task_sha256_path"] = _write_transcript_integrity_sidecar(done_path, task_sha256)
        result_sha256 = _json_atomic_write(f"{done_path}.result.json", result_record)
        result_record["result_sha256"] = result_sha256
        return json.dumps(result_record, ensure_ascii=False, sort_keys=True)
    except Exception as e:
        error_record = {
            "status": "queue_worker_error",
            "summary": f"queued dispatch worker error: {type(e).__name__}: {e}",
        }
        if task_path:
            error_record["queue_path"] = task_path
        if claimed_path:
            error_record["claimed_path"] = claimed_path
        if task_sha256:
            error_record["task_sha256"] = task_sha256
        if expected_task_sha256:
            error_record["expected_task_sha256"] = expected_task_sha256
        if claimed_path and os.path.exists(claimed_path):
            failed_path = f"{task_path}.failed"
            try:
                os.replace(claimed_path, failed_path)
                error_record["task_failed_path"] = failed_path
                if task_sha256:
                    error_record["task_sha256_path"] = _write_transcript_integrity_sidecar(failed_path, task_sha256)
                try:
                    os.unlink(f"{task_path}.sha256")
                except FileNotFoundError:
                    pass
                result_sha256 = _json_atomic_write(f"{failed_path}.result.json", error_record)
                error_record["result_sha256"] = result_sha256
            except Exception as retain_error:
                error_record["retention_error"] = f"{type(retain_error).__name__}: {retain_error}"
        return json.dumps(error_record, ensure_ascii=False, sort_keys=True)


def _rate_limit_state_path(label):
    safe = _safe_slug(label or "worker", max_len=32)
    return os.path.join(SUBAGENT_RUN_DIR, f".llm-rate-{safe}.json")


def _concurrency_state_path(label):
    safe = _safe_slug(label or "worker", max_len=32)
    return os.path.join(SUBAGENT_RUN_DIR, f".llm-inflight-{safe}.json")


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _read_json_state(f):
    f.seek(0)
    try:
        raw = f.read(_SUBAGENT_MAX_LLM_STATE_BYTES + 1)
        if len(raw) > _SUBAGENT_MAX_LLM_STATE_BYTES:
            return {}
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _write_json_state(f, data):
    f.seek(0)
    f.truncate()
    json.dump(data, f, ensure_ascii=False, sort_keys=True)
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())


def _ensure_workspace_parent_directory(resolved_path, label):
    """Create/validate a workspace file parent without symlink ancestors.

    ``_resolve_workspace_path`` checks containment before file-tool writes, but
    the parent directory can still be swapped locally before the lock/temp-file
    open. Walk the parent path relative to the real workspace root and reject
    any symlink/non-directory component before writing inside it.
    """
    parent = os.path.dirname(resolved_path)
    if not parent:
        return
    root = _subagent_workspace_root()
    real_parent = os.path.realpath(os.path.abspath(parent))
    if os.path.commonpath([root, real_parent]) != root:
        raise ValueError(f"{label} parent escapes subagent workspace")
    try:
        os.makedirs(root, exist_ok=True)
        root_st = os.lstat(root)
    except OSError:
        raise ValueError(f"{label} workspace root stat failed")
    if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
        raise ValueError(f"{label} workspace root must be a real non-symlink directory")
    rel = os.path.relpath(parent, root)
    current = root
    if rel == ".":
        rel_parts = []
    else:
        rel_parts = [part for part in rel.split(os.sep) if part and part != "."]
    for part in rel_parts:
        current = os.path.join(current, part)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            st = os.lstat(current)
        except OSError:
            raise ValueError(f"{label} parent directory stat failed")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"{label} parent must be a real non-symlink directory")


@contextlib.contextmanager
def _workspace_file_lock(resolved_path):
    """Serialize updates to one workspace file when fcntl is available.

    Atomic rename protects readers from torn writes, but append-style updates
    also need a per-target critical section to avoid lost updates when multiple
    parent/worker processes append to the same artifact concurrently. The lock
    file lives beside the target and is itself inside the sandbox-resolved
    parent directory.
    """
    parent = os.path.dirname(resolved_path)
    if parent:
        _ensure_workspace_parent_directory(resolved_path, "workspace file lock")
    if fcntl is None:
        yield
        return
    lock_path = os.path.join(parent or ".", f".{os.path.basename(resolved_path)}.lock")
    lock_fd = _open_regular_no_symlink(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def _atomic_replace_text(resolved_path, content):
    parent = os.path.dirname(resolved_path)
    if parent:
        _ensure_workspace_parent_directory(resolved_path, "workspace file write")
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(resolved_path)}.", suffix=".tmp", dir=parent or None
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, resolved_path)
        _fsync_parent_dir(resolved_path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _subagent_llm_concurrency_acquire(label):
    """Reserve one in-flight worker LLM slot across parent processes.

    This complements the calls/minute rate guard: rate limiting bounds spend over
    time, while concurrency limiting prevents several long worker calls from
    piling up at once. Set OMEGACLAW_SUBAGENT_MAX_CONCURRENT_LLM_CALLS=0 to
    disable locally.
    """
    limit = max(0, int(_SUBAGENT_MAX_CONCURRENT_LLM_CALLS))
    if limit == 0:
        return (True, "disabled", "")
    if fcntl is None:
        return (False, "fcntl unavailable for atomic concurrency state", "")
    path = _concurrency_state_path(label)
    parent = os.path.dirname(path)
    try:
        if parent:
            _ensure_regular_directory(parent, "LLM concurrency state parent")
    except Exception as e:
        return (False, f"concurrency state error: {type(e).__name__}: {e}", "")
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    now = time.time()
    stale_before = now - 3600.0
    try:
        state_fd = _open_regular_no_symlink(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(state_fd, "r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            data = _read_json_state(f)
            inflight = []
            for entry in data.get("inflight", []):
                try:
                    pid = int(entry.get("pid"))
                    ts = float(entry.get("ts", 0))
                except Exception:
                    continue
                if ts >= stale_before and _pid_alive(pid):
                    inflight.append(entry)
            if len(inflight) >= limit:
                _write_json_state(f, {"inflight": inflight})
                return (False, f"{len(inflight)}/{limit} worker LLM calls already in flight", "")
            inflight.append({"token": token, "pid": os.getpid(), "ts": now})
            _write_json_state(f, {"inflight": inflight})
        return (True, f"{len(inflight)}/{limit} worker LLM calls in flight", token)
    except Exception as e:
        return (False, f"concurrency state error: {type(e).__name__}: {e}", "")


def _subagent_llm_concurrency_release(label, token):
    if not token or fcntl is None:
        return
    path = _concurrency_state_path(label)
    try:
        state_fd = _open_regular_no_symlink(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(state_fd, "r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            data = _read_json_state(f)
            inflight = [e for e in data.get("inflight", []) if e.get("token") != token]
            _write_json_state(f, {"inflight": inflight})
    except Exception:
        pass


def _subagent_llm_rate_limit_acquire(label):
    """Atomically reserve one worker LLM call for the current minute.

    This is a small cross-process backpressure guard for ThreadKeeper workers:
    even if several parent loops invoke subagents at once, each configured
    endpoint label has a bounded calls/minute budget. Set
    OMEGACLAW_SUBAGENT_LLM_CALLS_PER_MINUTE=0 to disable locally.
    """
    limit = max(0, int(_SUBAGENT_LLM_CALLS_PER_MINUTE))
    if limit == 0:
        return (True, "disabled")
    if fcntl is None:
        return (False, "fcntl unavailable for atomic rate-limit state")
    path = _rate_limit_state_path(label)
    parent = os.path.dirname(path)
    try:
        if parent:
            _ensure_regular_directory(parent, "LLM rate-limit state parent")
    except Exception as e:
        return (False, f"rate-limit state error: {type(e).__name__}: {e}")
    now = time.time()
    window_start = now - 60.0
    try:
        state_fd = _open_regular_no_symlink(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(state_fd, "r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            data = _read_json_state(f)
            calls = [float(ts) for ts in data.get("calls", []) if float(ts) >= window_start]
            if len(calls) >= limit:
                return (False, f"{len(calls)}/{limit} calls already used in the last 60s")
            calls.append(now)
            _write_json_state(f, {"calls": calls})
        return (True, f"{len(calls)}/{limit} calls used in the last 60s")
    except Exception as e:
        return (False, f"rate-limit state error: {type(e).__name__}: {e}")


def _new_run_record(persona_key, goal):
    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:10]}"
    path = os.path.join(SUBAGENT_RUN_DIR, f"{run_id}-{_safe_slug(persona_key)}.json")
    return {
        "run_id": run_id,
        "persona_key": persona_key,
        "goal": str(goal or ""),
        "started_at": time.time(),
        "finished_at": None,
        "status": "running",
        "history_digest": [],
        "turns": [],
        "files_changed": [],
        "patch_proposals": [],
        "tests_run": [],
        "transcript_path": path,
        "task_contract": {},
    }


def _bound_transcript_turns(record):
    """Cap the number of turns and per-field sizes in the transcript record.

    This runs just before the transcript is written to disk. It keeps the most
    recent turns up to _SUBAGENT_MAX_TRANSCRIPT_TURNS (0 = no cap) and truncates
    individual turn field strings to _SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS
    (0 = no cap). A `transcript_truncated` marker is added when truncation
    occurs so audit readers know the transcript is a bounded view.
    """
    turns = record.get("turns")
    if not isinstance(turns, list) or not turns:
        return
    max_turns = _SUBAGENT_MAX_TRANSCRIPT_TURNS
    max_field = _SUBAGENT_MAX_TRANSCRIPT_FIELD_CHARS
    if max_turns <= 0 and max_field <= 0:
        return
    truncated = False
    if max_turns > 0 and len(turns) > max_turns:
        dropped = len(turns) - max_turns
        record["turns"] = turns[-max_turns:]
        record["transcript_truncated"] = {
            "turns_dropped": dropped,
            "reason": "exceeds OMEGACLAW_SUBAGENT_MAX_TRANSCRIPT_TURNS",
        }
        truncated = True
    if max_field > 0:
        for t in record.get("turns", []):
            for key in ("prompt", "raw_response", "tool_results"):
                val = t.get(key)
                if isinstance(val, str) and len(val) > max_field:
                    t[key] = val[:max_field] + f"\n[...truncated at {max_field} chars...]"
                    truncated = True
    if truncated and "transcript_truncated" not in record:
        record["transcript_truncated"] = {"reason": "field size cap"}


def _finish_run_record(record, status, summary=None):
    if not record:
        return ""
    record["status"] = status
    raw_summary = summary or ""
    cap_summary = _SUBAGENT_MAX_TRANSCRIPT_SUMMARY_CHARS
    if cap_summary > 0 and len(raw_summary) > cap_summary:
        raw_summary = raw_summary[:cap_summary] + f"\n[...summary truncated at {cap_summary} chars...]"
    record["summary"] = raw_summary
    record["finished_at"] = time.time()
    _bound_transcript_turns(record)
    try:
        digest = _json_atomic_write(record["transcript_path"], record)
        sidecar = _write_transcript_integrity_sidecar(record["transcript_path"], digest)
        record["transcript_sha256"] = digest
        record["transcript_sha256_path"] = sidecar
        record["run_index_path"] = _append_run_index(record)
    except Exception as e:
        record["record_write_error"] = f"{type(e).__name__}: {e}"
    return record.get("transcript_path", "")


def _structured_return(summary, record=None, status="ok", uncertainty="low",
                       next_action="return to parent", max_chars=None):
    limit = max_chars or SUBAGENT_MAX_DIGEST_CHARS
    token_usage = (record or {}).get("worker_token_usage")
    patch_proposals = (record or {}).get("patch_proposals") or []
    payload = {
        "summary": cap(summary, max(100, limit // 2)),
        "files_changed": list(dict.fromkeys((record or {}).get("files_changed", []))),
        "patch_proposals": [
            {"action": p.get("action", ""), "path": p.get("path", "")}
            for p in patch_proposals[:20]
        ],
        "tests_run": list(dict.fromkeys((record or {}).get("tests_run", []))),
        "uncertainty": uncertainty,
        "next_action": next_action,
        "transcript_path": (record or {}).get("transcript_path", ""),
        "transcript_sha256": (record or {}).get("transcript_sha256", ""),
        "status": status,
    }
    if (record or {}).get("queue_path"):
        payload["queue_path"] = (record or {}).get("queue_path", "")
        payload["queue_sha256"] = (record or {}).get("queue_sha256", "")
        if (record or {}).get("queue_sha256_path"):
            payload["queue_sha256_path"] = (record or {}).get("queue_sha256_path", "")
    adjudication = (record or {}).get("adjudication")
    if adjudication:
        payload["adjudication"] = {
            "required": bool(adjudication.get("required")),
            "status": adjudication.get("status", ""),
            "candidate_summary": cap(adjudication.get("candidate_summary", ""), 300),
        }
    if token_usage:
        payload["worker_token_usage"] = token_usage
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    # Preserve valid JSON by shrinking variable-length fields instead of
    # truncating the serialized object mid-token.
    payload["summary"] = cap(summary, 200)
    payload["files_changed"] = payload["files_changed"][:20]
    payload["patch_proposals"] = payload["patch_proposals"][:20]
    payload["tests_run"] = payload["tests_run"][:10]
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    payload["summary"] = cap(summary, 80)
    payload["files_changed"] = []
    payload["patch_proposals"] = []
    payload["tests_run"] = []
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _digest_history_entry(entry):
    t, raw, res = entry
    return f"turn {t}: response={_clip(cap(raw, 500), 240)}; results={_clip(cap(res, 500), 240)}"


def _append_bounded_history(history, entry, history_digest):
    history.append(entry)
    max_turns = max(1, _SUBAGENT_HISTORY_MAX_TURNS)
    while len(history) > max_turns:
        evicted = history.pop(0)
        history_digest.append(_digest_history_entry(evicted))
    # Keep the digest bounded too; this is for prompt context, not audit.
    if len(history_digest) > max_turns:
        del history_digest[:-max_turns]


def _shell_enabled():
    return os.environ.get("OMEGACLAW_SUBAGENT_ENABLE_SHELL", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _shell_allowlist():
    raw = os.environ.get("OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


# ----------------------------------------------------------------------
# Persona config loading
# ----------------------------------------------------------------------

def _validate_persona_key(persona_key):
    """Keep persona lookup on named configs, not paths.

    Persona keys come from model/tool-facing `(delegate ...)` calls. Even though
    persona files are deployment-controlled, the lookup key itself should be a
    simple identifier so a child/parent prompt cannot traverse arbitrary JSON
    files via `../` or absolute paths.
    """
    key = str(persona_key or "").strip()
    if not key or not re.match(r"^[A-Za-z0-9_.-]+$", key) or key in (".", ".."):
        raise ValueError("persona key must be a simple identifier")
    return key


def load_persona_config(persona_key):
    """Read memory/personas-subagent/<key>.json. Returns dict with the
    fields documented in docs/subagent-design.md §4.4.1."""
    persona_key = _validate_persona_key(persona_key)
    path = os.path.join(PERSONA_DIR, f"{persona_key}.json")
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"persona config '{persona_key}.json' not found"
        )
    except OSError as e:
        raise ValueError(
            f"persona config '{persona_key}.json' stat failed: {type(e).__name__}"
        )
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(
            f"persona config '{persona_key}.json' must be a regular non-symlink file"
        )
    try:
        fd = _open_regular_no_symlink(path, os.O_RDONLY)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(_SUBAGENT_MAX_PERSONA_CONFIG_BYTES + 1)
    except OSError as e:
        raise ValueError(
            f"persona config '{persona_key}.json' read failed: {type(e).__name__}"
        )
    if len(raw) > _SUBAGENT_MAX_PERSONA_CONFIG_BYTES:
        raise ValueError(
            f"persona config '{persona_key}.json' exceeds "
            f"OMEGACLAW_SUBAGENT_MAX_PERSONA_CONFIG_BYTES={_SUBAGENT_MAX_PERSONA_CONFIG_BYTES}"
        )
    try:
        cfg = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError(
            f"persona config '{persona_key}.json' is not valid UTF-8"
        )
    except json.JSONDecodeError as e:
        raise ValueError(
            f"persona config '{persona_key}.json' is malformed JSON: {e}"
        )
    if not isinstance(cfg, dict):
        raise ValueError(
            f"persona config '{persona_key}.json' must be a JSON object"
        )
    if "task_contract" in cfg and not isinstance(cfg.get("task_contract"), dict):
        raise ValueError(
            f"persona config '{persona_key}.json' field 'task_contract' must be a JSON object"
        )
    required = ["persona_file", "provider", "model", "api_key_env", "node_role", "endpoint_kind"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(
            f"persona config '{persona_key}.json' missing required field(s): {missing}"
        )
    for field in ["persona_file", "provider", "model", "api_key_env", "node_role", "endpoint_kind"]:
        value = cfg.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"persona config '{persona_key}.json' field '{field}' must be a non-empty string"
            )
        if len(value) > _SUBAGENT_MAX_PERSONA_SCALAR_CHARS:
            raise ValueError(
                f"persona config '{persona_key}.json' field '{field}' exceeds "
                f"OMEGACLAW_SUBAGENT_MAX_PERSONA_SCALAR_CHARS={_SUBAGENT_MAX_PERSONA_SCALAR_CHARS}"
            )
        if _contains_text_control(value):
            raise ValueError(
                f"persona config '{persona_key}.json' field '{field}' must not contain control characters"
            )
    if "base_url" in cfg and cfg.get("base_url") is not None:
        value = cfg.get("base_url")
        if not isinstance(value, str):
            raise ValueError(
                f"persona config '{persona_key}.json' field 'base_url' must be a string"
            )
        if len(value) > _SUBAGENT_MAX_PERSONA_SCALAR_CHARS:
            raise ValueError(
                f"persona config '{persona_key}.json' field 'base_url' exceeds "
                f"OMEGACLAW_SUBAGENT_MAX_PERSONA_SCALAR_CHARS={_SUBAGENT_MAX_PERSONA_SCALAR_CHARS}"
            )
        if _contains_text_control(value):
            raise ValueError(
                f"persona config '{persona_key}.json' field 'base_url' must not contain control characters"
            )
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cfg.get("api_key_env", "")):
        raise ValueError(
            f"persona config '{persona_key}.json' field 'api_key_env' must be a safe environment-variable name"
        )
    if "persona_sha256" in cfg and cfg.get("persona_sha256"):
        value = cfg.get("persona_sha256")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError(
                f"persona config '{persona_key}.json' field 'persona_sha256' must be a 64-character hex SHA-256"
            )
    if "max_output_tokens" in cfg:
        value = cfg.get("max_output_tokens")
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            or value > SUBAGENT_MAX_OUTPUT_TOKENS_HARD_CAP
        ):
            raise ValueError(
                f"persona config '{persona_key}.json' field 'max_output_tokens' "
                f"must be an integer from 1 to {SUBAGENT_MAX_OUTPUT_TOKENS_HARD_CAP}"
            )
    if "default_tool_subset" in cfg:
        value = cfg.get("default_tool_subset")
        if not isinstance(value, list) or not value:
            raise ValueError(
                f"persona config '{persona_key}.json' field 'default_tool_subset' "
                "must be a non-empty list"
            )
        if len(value) > _SUBAGENT_MAX_CONTRACT_ITEMS:
            raise ValueError(
                f"persona config '{persona_key}.json' field 'default_tool_subset' "
                f"exceeds {_SUBAGENT_MAX_CONTRACT_ITEMS} items"
            )
        if any(
            not isinstance(item, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", item)
            for item in value
        ):
            raise ValueError(
                f"persona config '{persona_key}.json' field 'default_tool_subset' "
                "must contain only safe tool-name strings"
            )
        parse_subset(",".join(value))
    role = _node_role(cfg)
    if role not in (_LOCAL_NODE_ROLES | _CLOUD_NODE_ROLES):
        raise ValueError(
            f"persona config '{persona_key}.json' has invalid node_role '{cfg.get('node_role')}'; "
            f"expected one of {sorted(_LOCAL_NODE_ROLES | _CLOUD_NODE_ROLES)}"
        )
    kind = _endpoint_kind(cfg)
    if kind not in {"ollama_native", "openai_compatible"}:
        raise ValueError(
            f"persona config '{persona_key}.json' has invalid endpoint_kind/provider metadata '{kind}'; "
            "expected ollama_native or openai_compatible"
        )
    cfg["_persona_key"] = persona_key
    cfg["_node_role"] = role
    cfg["_endpoint_kind"] = kind
    return cfg


def _resolve_persona_prompt_path(persona_file, persona_key):
    """Resolve a persona prompt path inside PERSONA_DIR.

    Persona configs are deployment-controlled, but the config file is still part
    of the subagent trust boundary. Do not let a malformed/malicious config read
    arbitrary absolute paths or escape the persona directory via `..` segments.
    """
    rel = str(persona_file or "").strip()
    if not rel:
        raise ValueError(f"persona prompt for key '{persona_key}' is empty")
    base = os.path.realpath(PERSONA_DIR)
    candidate = rel if os.path.isabs(rel) else os.path.join(base, rel)
    candidate_abs = os.path.abspath(candidate)
    path = os.path.realpath(candidate_abs)
    try:
        common = os.path.commonpath([base, path])
    except ValueError:
        common = ""
    if common != base:
        raise ValueError(
            f"persona prompt '{persona_file}' for key '{persona_key}' escapes persona directory"
        )
    try:
        st = os.lstat(candidate_abs)
    except FileNotFoundError:
        return candidate_abs
    except OSError as e:
        raise ValueError(
            f"persona prompt '{persona_file}' for key '{persona_key}' stat failed: {type(e).__name__}"
        )
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(
            f"persona prompt '{persona_file}' for key '{persona_key}' must be a regular non-symlink file"
        )
    return candidate_abs


def load_persona_prompt(persona_file, persona_key, expected_sha256=""):
    """Read the persona text and optionally verify its SHA-256.

    Persona JSON may include `persona_sha256` to pin the prompt file. When set,
    a missing/mismatched prompt hash fails closed before any worker call.
    """
    path = _resolve_persona_prompt_path(persona_file, persona_key)
    try:
        fd = _open_regular_no_symlink(path, os.O_RDONLY)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"persona prompt '{persona_file}' for key '{persona_key}' not found"
        )
    try:
        with os.fdopen(fd, "rb") as f:
            if _SUBAGENT_MAX_PERSONA_PROMPT_BYTES:
                prompt_size = os.fstat(f.fileno()).st_size
                if prompt_size > _SUBAGENT_MAX_PERSONA_PROMPT_BYTES:
                    raise ValueError(
                        f"persona prompt '{persona_file}' for key '{persona_key}' exceeds "
                        f"OMEGACLAW_SUBAGENT_MAX_PERSONA_PROMPT_BYTES={_SUBAGENT_MAX_PERSONA_PROMPT_BYTES}"
                    )
                read_limit = _SUBAGENT_MAX_PERSONA_PROMPT_BYTES + 1
            else:
                read_limit = -1
            raw = f.read(read_limit)
    except ValueError:
        raise
    except OSError as e:
        raise ValueError(
            f"persona prompt '{persona_file}' for key '{persona_key}' read failed: {type(e).__name__}"
        )
    if _SUBAGENT_MAX_PERSONA_PROMPT_BYTES and len(raw) > _SUBAGENT_MAX_PERSONA_PROMPT_BYTES:
        raise ValueError(
            f"persona prompt '{persona_file}' for key '{persona_key}' exceeds "
            f"OMEGACLAW_SUBAGENT_MAX_PERSONA_PROMPT_BYTES={_SUBAGENT_MAX_PERSONA_PROMPT_BYTES}"
        )
    expected = str(expected_sha256 or "").strip().lower()
    if expected:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError(
                f"persona prompt '{persona_file}' for key '{persona_key}' "
                "failed sha256 integrity check"
            )
    return raw.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------
# Tool subset parsing + validation
# ----------------------------------------------------------------------

# v1 tool registry. Keys are skill names exposed to subagents; values
# are (callable, category) pairs. Categories: "endpoint_independent"
# (works regardless of where the subagent loop runs);
# "parent_env_bound" (requires parent process state — none in v1).
# Tools NOT in this dict are unknown to the subagent. Tools in
# _V1_EXCLUDED are deliberately forbidden.
_V1_EXCLUDED = frozenset([
    "remember", "pin", "metta", "send", "delegate", "query", "episodes",
])


def _bound_tool_output(result, cap=None):
    """Cap external tool output size as defense-in-depth against large
    API responses. Returns (result_string, truncated_bool)."""
    if cap is None:
        cap = _SUBAGENT_MAX_SEARCH_OUTPUT_CHARS
    text = str(result)
    if cap > 0 and len(text) > cap:
        return text[:cap] + f"\n...(tool output truncated at {cap} chars)..."
    return text


def _build_tool_registry():
    """Construct the per-process tool registry once. Imports are inline
    so that import failures don't break dispatch — instead the affected
    tool simply isn't registered."""
    registry = {}

    # File I/O — pure stdlib
    registry["read-file"] = (_tool_read_file, "endpoint_independent")
    registry["write-file"] = (_tool_write_file, "endpoint_independent")
    registry["append-file"] = (_tool_append_file, "endpoint_independent")

    # Shell — restricted subprocess
    registry["shell"] = (_tool_shell, "endpoint_independent")

    # Web search — reuses channels/websearch.py
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "channels"
        ))
        import websearch
        registry["search"] = (
            lambda q: _bound_tool_output(websearch.search(q)),
            "endpoint_independent",
        )
    except Exception as e:
        # Search not registered if websearch import fails. Diagnostic
        # available through error path if the subagent tries to use it.
        registry["_search_import_error"] = str(e)

    # Remote-agent skills via src/agentverse.py
    try:
        import agentverse
        registry["tavily-search"] = (
            lambda q: _bound_tool_output(agentverse.tavily_search(q)),
            "endpoint_independent",
        )
        registry["technical-analysis"] = (
            lambda t: _bound_tool_output(agentverse.technical_analysis(t)),
            "endpoint_independent",
        )
    except Exception:
        # Agentverse-backed skills unavailable if uagents isn't
        # importable. Subagent gets a clear error if it tries.
        pass

    return registry


_TOOL_REGISTRY = None  # initialized lazily

def _tool_registry():
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is None:
        _TOOL_REGISTRY = _build_tool_registry()
    return _TOOL_REGISTRY


def parse_subset(tool_subset_csv):
    """Validate a CSV of tool names against the registry. Returns the
    list of tool names. Raises ValueError on unknown / v1-excluded."""
    if not tool_subset_csv:
        raise ValueError("tool subset is empty")
    names = [n.strip() for n in tool_subset_csv.split(",") if n.strip()]
    reg = _tool_registry()
    excluded = [n for n in names if n in _V1_EXCLUDED]
    if excluded:
        raise ValueError(
            f"skill(s) {excluded} are not callable by subagents in v1 "
            "(see docs/subagent-design.md §4.5.2)"
        )
    unknown = [n for n in names if n not in reg]
    if unknown:
        raise ValueError(
            f"unknown skill(s) {unknown}; registered subagent tools: "
            f"{sorted(k for k in reg.keys() if not k.startswith('_'))}"
        )
    return names


def validate_endpoint_compat(tool_names, cfg):
    """Placeholder for forward-compatible Option C runner-vs-tool
    validation. In Option B v1, the loop is always in-process, so all
    non-excluded tools are reachable regardless of where the subagent's
    LLM endpoint lives. Always passes."""
    return True


# ----------------------------------------------------------------------
# Provider resolution
# ----------------------------------------------------------------------

def resolve_or_instantiate_provider(provider_name, model_name, base_url, var_name, endpoint_kind=None):
    """Build a provider handle scoped to this dispatch.

    A fresh handle per dispatch ensures each persona's endpoint binding is
    honored exactly. OpenAI-compatible endpoints must have an importable client
    before the worker loop starts; otherwise dispatch fails as a structured
    provider setup error instead of burning turns on repeated ``no cloud client``
    pseudo-responses. Native Ollama endpoints deliberately do not need the
    OpenAI SDK because _call_subagent_llm uses urllib against /api/chat.
    """
    api_key = os.environ.get(var_name)
    if not api_key:
        raise RuntimeError(
            f"env var '{var_name}' is unset; cannot reach endpoint for "
            f"provider '{provider_name}'"
        )
    kind = endpoint_kind or _endpoint_kind({"provider": provider_name})
    client = None
    if kind == "openai_compatible":
        try:
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=(base_url or None))
        except Exception as e:
            raise RuntimeError(
                f"OpenAI-compatible provider '{provider_name}' cannot be initialized: "
                f"{type(e).__name__}: {e}"
            )
    return {
        "provider": client,
        "model": model_name,
        "provider_name": provider_name,
        "base_url": base_url or "",
        "var_name": var_name,
        "endpoint_kind": kind,
    }


# ----------------------------------------------------------------------
# LLM call — uses AIProvider.chat from lib_llm_ext.
# ----------------------------------------------------------------------

def _call_with_retries(call_once, label):
    """Run one bounded worker call with retry/backoff. Returns text or error."""
    attempts = max(1, _SUBAGENT_LLM_RETRIES + 1)
    last_exc = None
    for attempt in range(1, attempts + 1):
        in_flight, concurrency_reason, concurrency_token = _subagent_llm_concurrency_acquire(label)
        if not in_flight:
            return f"(subagent LLM call concurrency-limited via {label}: {concurrency_reason})"
        try:
            allowed, reason = _subagent_llm_rate_limit_acquire(label)
            if not allowed:
                return f"(subagent LLM call rate-limited via {label}: {reason})"
            try:
                return call_once()
            except Exception as e:
                last_exc = e
                if attempt < attempts:
                    base = max(0.0, _SUBAGENT_LLM_BACKOFF_S) * (2 ** (attempt - 1))
                    # Add jitter (up to 25% of the base delay) to avoid
                    # thundering-herd retries when multiple subagents
                    # hit the same endpoint simultaneously.
                    jitter = random.uniform(0.0, max(0.0, base * 0.25))
                    delay = base + jitter
                    if delay:
                        time.sleep(delay)
        finally:
            _subagent_llm_concurrency_release(label, concurrency_token)
    return (
        f"(subagent LLM call failed after {attempts} attempt(s) "
        f"via {label}: {type(last_exc).__name__}: {last_exc})"
    )


def _call_subagent_llm(provider_handle, content, max_tokens):
    """Call the subagent's worker LLM and return (text, in_tokens, out_tokens).

    For LOCAL Ollama endpoints we use the NATIVE /api/chat path with
    {"think": false} — the OpenAI /v1 path on this Ollama build returns
    EMPTY content for reasoning models (qwen/gemma/gpt-oss/granite) because
    hidden <think> tokens consume the whole budget. The native path with
    thinking disabled returns real content. For non-Ollama (cloud) endpoints
    we fall back to AIProvider.chat (/v1), which is correct there.

    Never raises into the MeTTa interpreter — returns a (subagent ...) string
    on failure, with zero token counts.
    """
    base_url = (provider_handle.get("base_url") or "").rstrip("/")
    model = provider_handle["model"]
    endpoint_kind = (provider_handle.get("endpoint_kind") or "").strip().lower()

    if base_url and endpoint_kind == "ollama_native":
        import json as _json
        import urllib.request as _u
        root = base_url[:-3] if base_url.endswith("/v1") else base_url
        def call_once():
            body = _json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": False,
                "think": False,
                "options": {"num_predict": max_tokens},
            }).encode()
            req = _u.Request(root + "/api/chat", data=body,
                             headers={"Content-Type": "application/json"})
            with _u.urlopen(req, timeout=_SUBAGENT_LLM_TIMEOUT_S) as r:
                if _SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES:
                    raw = r.read(_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES + 1)
                    if len(raw) > _SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES:
                        return (
                            "(subagent error: native provider HTTP response exceeds "
                            "OMEGACLAW_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES="
                            f"{_SUBAGENT_MAX_LLM_HTTP_RESPONSE_BYTES})"
                        )
                else:
                    raw = r.read()
                data = _json.loads(raw.decode("utf-8", errors="replace"))
            _log_worker_usage(model, data.get("prompt_eval_count", 0),
                              data.get("eval_count", 0))
            in_tok = data.get("prompt_eval_count", 0) or 0
            out_tok = data.get("eval_count", 0) or 0
            return ((data.get("message") or {}).get("content", "") or "", in_tok, out_tok)
        result = _call_with_retries(call_once, "ollama")
        if isinstance(result, tuple):
            return result
        return (result, 0, 0)

    # Cloud endpoint — standard OpenAI /v1 chat (GLM/DeepSeek separate
    # reasoning from content correctly here).
    client = provider_handle["provider"]
    if client is None:
        return ("(subagent error: no cloud client available)", 0, 0)
    def call_once():
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            timeout=_SUBAGENT_LLM_TIMEOUT_S,
        )
        try:
            u = resp.usage
            _log_worker_usage(model, getattr(u, "prompt_tokens", 0),
                              getattr(u, "completion_tokens", 0))
            in_tok = getattr(u, "prompt_tokens", 0) or 0
            out_tok = getattr(u, "completion_tokens", 0) or 0
        except Exception:
            in_tok, out_tok = 0, 0
            pass
        return (resp.choices[0].message.content or "", in_tok, out_tok)
    result = _call_with_retries(call_once, "openai-compatible")
    if isinstance(result, tuple):
        return result
    return (result, 0, 0)


# ----------------------------------------------------------------------
# Prompt construction
# ----------------------------------------------------------------------

# Tool catalogue descriptions — these are the strings the subagent
# sees so it knows what's callable. Mirrors src/skills.metta:getSkills
# but narrowed per dispatch.
_TOOL_DESCRIPTIONS = {
    "search":
        "- Search the web; returns titles + snippets: search query",
    "read-file":
        "- Read file to string: read-file filename",
    "write-file":
        "- Write string to file: write-file filename string",
    "append-file":
        "- Append line to file: append-file filename string",
    "shell":
        "- Execute shell command without apostrophe in string; "
        "returns command output: shell string",
    "tavily-search":
        "- Search the web via Tavily Search Agent: tavily-search query",
    "technical-analysis":
        "- Technical analysis for a stock ticker: technical-analysis ticker",
}

_TASK_CONTRACT_FIELDS = frozenset({
    "objective",
    "allowed_paths",
    "forbidden_actions",
    "done_criteria",
    "max_tool_calls",
    "patch_proposal_only",
    "requires_adjudication",
})


def _normalize_task_contract(goal, cfg=None):
    """Return (objective_text, contract_dict) for optional task contracts.

    Contracts are intentionally data-only and may be supplied either in the
    persona JSON as `task_contract` or inline as a JSON goal object containing
    `objective`, `allowed_paths`, `forbidden_actions`, `done_criteria`,
    `max_tool_calls`, `patch_proposal_only`, and/or `requires_adjudication`.
    This keeps the existing `(delegate goal tools persona max_turns)` API while
    giving parent agents a concrete way to narrow a child task.
    """
    contract = dict((cfg or {}).get("task_contract") or {})
    objective = str(goal or "")
    try:
        parsed = json.loads(goal) if isinstance(goal, str) else goal
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        nested_contract = parsed.get("task_contract")
        if "task_contract" in parsed and not isinstance(nested_contract, dict):
            # Preserve the malformed value in the run record and let strict
            # validation fail closed instead of silently treating the outer
            # JSON object as an inline contract.
            contract["task_contract"] = nested_contract
        inline = nested_contract if isinstance(nested_contract, dict) else parsed
        if isinstance(inline, dict):
            keys = inline.keys() if isinstance(nested_contract, dict) else _TASK_CONTRACT_FIELDS
            for key in keys:
                if key in inline:
                    contract[key] = inline[key]
            if "objective" in inline:
                objective = inline["objective"]
            elif "objective" in parsed:
                objective = parsed["objective"]
    # Preserve an explicitly supplied non-string objective in the persisted
    # contract so validation can fail closed instead of silently stringifying
    # typed JSON (for example, a list or object) into worker prompt text.
    contract["objective"] = objective.strip() if isinstance(objective, str) else objective
    contract["allowed_paths"] = _contract_string_list(contract.get("allowed_paths"))
    contract["forbidden_actions"] = _contract_string_list(contract.get("forbidden_actions"))
    contract["done_criteria"] = _contract_string_list(contract.get("done_criteria"))
    objective_text = contract["objective"] if isinstance(contract["objective"], str) else ""
    return objective_text, contract


def _validate_task_contract(contract):
    """Fail closed on oversized or unsafe task-contract data.

    Contracts are prompt-visible and persisted in transcripts, so they need the
    same bounded-shape treatment as tool arguments. `allowed_paths` also gets a
    dry-run workspace resolution now, rather than waiting until a tool call.
    """
    if "task_contract" in (contract or {}):
        return "task contract field must be a JSON object"
    unknown_fields = sorted(set(contract or {}) - _TASK_CONTRACT_FIELDS)
    if unknown_fields:
        return f"task contract contains unknown field(s): {unknown_fields}"
    objective = (contract or {}).get("objective", "")
    if not isinstance(objective, str):
        return "task contract objective must be a string"
    if not objective.strip():
        return "task contract objective must be a non-empty string"
    if len(objective) > _SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS:
        return (
            "task contract objective exceeds "
            f"{_SUBAGENT_MAX_CONTRACT_OBJECTIVE_CHARS} characters"
        )
    if _contains_text_control(objective):
        return "task contract objective must not contain control characters"
    for field in ("allowed_paths", "forbidden_actions", "done_criteria"):
        values = (contract or {}).get(field) or []
        if not isinstance(values, list):
            return f"task contract {field} must be a list of strings"
        if len(values) > _SUBAGENT_MAX_CONTRACT_ITEMS:
            return f"task contract {field} has {len(values)} item(s), max {_SUBAGENT_MAX_CONTRACT_ITEMS}"
        for value in values:
            if not isinstance(value, str):
                return f"task contract {field} entries must be strings"
            if not value.strip():
                return f"task contract {field} entries must be non-empty strings"
            if len(value) > _SUBAGENT_MAX_CONTRACT_ITEM_CHARS:
                return (
                    f"task contract {field} item exceeds "
                    f"{_SUBAGENT_MAX_CONTRACT_ITEM_CHARS} characters"
                )
            if _contains_text_control(value):
                return f"task contract {field} entries must not contain control characters"
    if "max_tool_calls" in (contract or {}):
        raw_quota = (contract or {}).get("max_tool_calls")
        if isinstance(raw_quota, bool):
            return f"task contract max_tool_calls entry '{raw_quota}' is not an integer"
        if isinstance(raw_quota, int):
            quota = raw_quota
        elif isinstance(raw_quota, str) and re.match(r"^\d+$", raw_quota.strip()):
            quota_text = raw_quota.strip()
            # Avoid sending attacker-controlled, arbitrarily long decimal
            # strings through int(); Python may reject them at its conversion
            # limit, which would otherwise escape dispatch's structured-error
            # path. A task contract can only narrow the global quota, so a
            # larger decimal representation is invalid without conversion.
            max_quota_text = str(_SUBAGENT_MAX_TOOL_CALLS)
            if (
                len(quota_text) > len(max_quota_text)
                or (
                    len(quota_text) == len(max_quota_text)
                    and quota_text > max_quota_text
                )
            ):
                return (
                    "task contract max_tool_calls must not exceed global limit "
                    f"{_SUBAGENT_MAX_TOOL_CALLS}"
                )
            quota = int(quota_text)
        else:
            return f"task contract max_tool_calls entry '{raw_quota}' is not an integer"
        if quota < 0:
            return "task contract max_tool_calls must be non-negative"
        if quota > _SUBAGENT_MAX_TOOL_CALLS:
            return (
                "task contract max_tool_calls must not exceed global limit "
                f"{_SUBAGENT_MAX_TOOL_CALLS}"
            )
        contract["max_tool_calls"] = quota
    for bool_field in ("patch_proposal_only", "requires_adjudication"):
        if bool_field in (contract or {}):
            value = (contract or {}).get(bool_field)
            if not isinstance(value, bool):
                return f"task contract {bool_field} must be a boolean"
    for action in (contract or {}).get("forbidden_actions") or []:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$", str(action)):
            return f"task contract forbidden_actions entry '{action}' is not a safe action identifier"
    for prefix in (contract or {}).get("allowed_paths") or []:
        path_error = _validate_relative_workspace_path_arg(prefix, "task contract allowed_paths entry")
        if path_error:
            return path_error
        try:
            _resolve_workspace_path(prefix)
        except Exception as e:
            return f"task contract allowed_paths entry '{prefix}' is outside workspace: {e}"
    return ""



def _contract_string_list(value):
    """Normalize optional string-list contract fields without type coercion.

    Earlier versions accepted scalar/non-string values by stringifying them,
    which made malformed inline/persona contracts look valid before the strict
    validator ran. Keep the ergonomic empty-value-to-empty-list behavior, but
    otherwise preserve bad shapes so _validate_task_contract can fail closed
    before any worker call.
    """
    if value is None or value == "":
        return []
    if not isinstance(value, (list, tuple)):
        return value
    out = []
    for item in value:
        if isinstance(item, str):
            # Preserve blank entries after trimming so strict validation can
            # reject malformed contracts instead of silently dropping data.
            out.append(item.strip())
        else:
            out.append(item)
    return out


def _path_within_contract(path, contract):
    allowed = (contract or {}).get("allowed_paths") or []
    if not allowed:
        return True
    try:
        resolved = _resolve_workspace_path(path)
    except Exception:
        return False
    for prefix in allowed:
        try:
            allowed_path = _resolve_workspace_path(prefix)
        except Exception:
            continue
        if os.path.commonpath([allowed_path, resolved]) == allowed_path:
            return True
    return False


def _tool_forbidden_by_contract(name, contract):
    forbidden = {x.strip().lower() for x in ((contract or {}).get("forbidden_actions") or [])}
    aliases = {
        name.lower(),
        name.lower().replace("-", "_"),
    }
    if name in ("write-file", "append-file"):
        aliases.update({"write", "file-write", "modify-files"})
    if name == "shell":
        aliases.update({"exec", "execute", "run-command", "shell-exec"})
    return bool(forbidden & aliases)


def _contract_patch_proposal_only(contract):
    return bool((contract or {}).get("patch_proposal_only") is True)


def _contract_requires_adjudication(contract):
    return bool((contract or {}).get("requires_adjudication") is True)


def tools_catalog(tool_names):
    """Build the subagent's SKILLS block — narrowed to the subset."""
    lines = []
    for name in tool_names:
        desc = _TOOL_DESCRIPTIONS.get(name)
        if desc:
            lines.append(desc)
    # emit is always available — it is how the subagent terminates
    lines.append(
        "- Emit your final digest to the parent and end the loop: "
        "emit string"
    )
    return "\n".join(lines)


def build_subagent_prompt(persona, catalog, last_results, history, goal,
                          iteration, max_iterations, history_digest=None,
                          task_contract=None):
    """Build the subagent's per-turn prompt. Shape mirrors the parent's
    getContext but with smaller per-component caps appropriate to a
    short-lived helper."""
    history_snippet = ""
    if history:
        # Keep the tail of the subagent's own history under the cap
        joined = "\n".join(
            f"[turn {t}] response: {_clip(r, 800)} | results: {_clip(res, 800)}"
            for (t, r, res) in history
        )
        history_snippet = joined[-_SUBAGENT_HISTORY_CAP:]
    parts = [
        f"PERSONA: {persona.strip()}",
        f"TOOLS:\n{catalog}",
        "OUTPUT_FORMAT: Emit one s-expression per line, each starting with '('. "
        "Use the tools above. When you have your final answer, emit "
        "(emit \"<digest>\") on its own line and stop. Do not narrate; "
        "do not wrap output in markdown fences; do not use <think> blocks. "
        "No more than 3 tool calls per turn.",
        f"GOAL: {goal}",
        f"ITERATION: {iteration} of {max_iterations} maximum",
    ]
    if task_contract:
        parts.append(
            "TASK_CONTRACT:\n"
            f"objective: {task_contract.get('objective') or goal}\n"
            f"allowed_paths: {task_contract.get('allowed_paths') or []}\n"
            f"forbidden_actions: {task_contract.get('forbidden_actions') or []}\n"
            f"done_criteria: {task_contract.get('done_criteria') or []}\n"
            f"max_tool_calls: {task_contract.get('max_tool_calls', 'global-default')}\n"
            f"patch_proposal_only: {task_contract.get('patch_proposal_only', False)}\n"
            f"requires_adjudication: {task_contract.get('requires_adjudication', False)}"
        )
        if _contract_patch_proposal_only(task_contract):
            parts.append(
                "PATCH_PROPOSAL_MODE: File mutation tools record proposed changes "
                "in the transcript but do not write workspace files. Emit a digest "
                "that tells the parent what to review/test/apply."
            )
        if _contract_requires_adjudication(task_contract):
            parts.append(
                "ADJUDICATION_REQUIRED: Treat your final emit as a candidate output "
                "only. It will be persisted for parent/supervisor review and must not "
                "be considered accepted until an adjudicator clears it."
            )
    if last_results:
        parts.append(f"LAST_RESULTS:\n{last_results[-_SUBAGENT_RESULTS_CAP:]}")
    if history_digest:
        parts.append(f"HISTORY_DIGEST:\n{_clip(' | '.join(history_digest), _SUBAGENT_HISTORY_CAP)}")
    if history_snippet:
        parts.append(f"HISTORY:\n{history_snippet}")
    return "\n\n".join(parts)


def _clip(s, n):
    s = s if s is not None else ""
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


# ----------------------------------------------------------------------
# Response parsing — strips <think> blocks, markdown fences, finds
# s-expressions starting at line beginnings. Self-contained; does NOT
# depend on lib_llm_ext.
# ----------------------------------------------------------------------

_THINK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n|^\s*```\s*$", re.MULTILINE)


def _strip_thinking(text):
    return _THINK_RE.sub("", text)


def _strip_fences(text):
    return _FENCE_RE.sub("", text)


def parse_calls(adapted_text):
    """Find lines starting with '(' and parse each as one s-expression.
    Returns list of (skill_name, [args]) tuples. Best-effort — bad
    lines are skipped, not raised on."""
    text = _strip_thinking(adapted_text)
    text = _strip_fences(text)
    calls = []
    # Split only on the protocol's ASCII newline. ``str.splitlines()`` also
    # treats Unicode line/paragraph separators as record boundaries, which can
    # hide an unsafe separator inside an otherwise parseable tool argument.
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or not line.startswith("("):
            continue
        if not line.endswith(")"):
            continue
        # Strip outer parens
        inner = line[1:-1].strip()
        if not inner:
            continue
        # Parse: skill_name <args>; first whitespace-separated token is
        # the skill name; the rest is the argument (which may itself
        # be quoted). For v1 we only support single-arg skills and
        # two-arg write-file/append-file.
        m = re.match(r"^([A-Za-z][A-Za-z0-9_\-]*)\s*(.*)$", inner, re.DOTALL)
        if not m:
            continue
        name = m.group(1)
        rest = m.group(2).strip()
        args = _parse_args(name, rest)
        calls.append((name, args))
    return calls


def _parse_args(skill_name, rest):
    """Tolerant arg parser. Handles quoted strings and bare tokens.
    For the small v1 skill set we don't need a real lexer."""
    if not rest:
        return []
    # Two-arg skills: filename then content
    if skill_name in ("write-file", "append-file"):
        # Pull the filename (first quoted string or first whitespace
        # token), then everything else is content
        if rest.startswith('"'):
            end = _find_close_quote(rest, 1)
            if end == -1:
                return [rest]
            filename = rest[1:end]
            content = rest[end + 1:].strip()
            if content.startswith('"'):
                content_end = _find_close_quote(content, 1)
                if content_end == -1:
                    return [filename, content, ""]
                trailing = content[content_end + 1:].strip()
                if trailing:
                    return [filename, content[1:content_end], trailing]
                content = content[1:content_end]
            return [filename, content]
        parts = rest.split(None, 1)
        if len(parts) == 1:
            return [parts[0], ""]
        filename, content = parts[0], parts[1].strip()
        if content.startswith('"'):
            content_end = _find_close_quote(content, 1)
            if content_end == -1:
                return [filename, content, ""]
            trailing = content[content_end + 1:].strip()
            if trailing:
                return [filename, content[1:content_end], trailing]
            content = content[1:content_end]
        else:
            # Legacy unquoted content remains supported, but do not let an
            # ambiguous same-line trailing call be persisted as file content.
            # Returning a third argument makes validation fail closed before
            # write-file/append-file can mutate the workspace.
            trailing_call = re.search(r"\)\s*\(", content)
            if trailing_call:
                return [
                    filename,
                    content[:trailing_call.start()].strip(),
                    content[trailing_call.start() + 1:].strip(),
                ]
        return [filename, content]
    # Single-arg skills. If the worker starts a quoted single argument,
    # require that the closing quote ends the argument (modulo whitespace).
    # Otherwise same-line payloads such as `(emit "done") (write-file ...)`
    # are parsed as one successful emit instead of a malformed final response.
    if rest.startswith('"'):
        end = _find_close_quote(rest, 1)
        if end == -1:
            # Surface malformed quoted calls as an argument-count violation.
            # Returning the raw text as one argument would let single-argument
            # provider tools execute with an unterminated worker payload.
            return [rest, ""]
        trailing = rest[end + 1:].strip()
        if trailing:
            return [rest[1:end], trailing]
        return [rest[1:end]]
    # Unquoted arguments remain supported for older workers, but a closing
    # call followed by another opening parenthesis is an ambiguous same-line
    # multi-call payload. Reject it for every single-argument skill before a
    # provider, subprocess, file read, or final emit can consume the text.
    # No whitespace is required: compact payloads such as `done)(emit hidden`
    # are just as ambiguous as `done) (emit hidden`. Ordinary balanced prose
    # remains valid unless a close parenthesis is followed by an open one.
    trailing_call = re.search(r"\)\s*\(", rest)
    if trailing_call:
        return [rest[:trailing_call.start()].strip(), rest[trailing_call.start() + 1:].strip()]
    return [rest]


def _find_close_quote(s, start):
    i = start
    while i < len(s):
        if s[i] == '\\':
            i += 2
            continue
        if s[i] == '"':
            return i
        i += 1
    return -1


# ----------------------------------------------------------------------
# Tool execution
# ----------------------------------------------------------------------

def _sanitize_error_msg(e):
    """Strip absolute paths from error messages before returning to worker LLM.

    Tool error strings are visible to the worker LLM and persisted in
    transcripts. Raw exception messages often include absolute filesystem
    paths (e.g. FileNotFoundError with the full resolved path), which could
    leak the host filesystem layout to the model or to a parent agent
    receiving a structured digest.
    """
    msg = str(e)
    try:
        root = _subagent_workspace_root()
    except Exception:
        root = ""
    if root and root in msg:
        msg = msg.replace(root, "<workspace>")
    # Strip any remaining absolute Unix paths (e.g. /tmp, /home, /etc)
    msg = re.sub(r"(?<![\w./-])/(?:[\w.-]+/)+[\w.-]+", "<path>", msg)
    return msg


def _tool_read_file(path):
    try:
        resolved = _resolve_workspace_path(path)
        limit = max(1, int(_SUBAGENT_MAX_READ_FILE_CHARS))
        fd = _open_workspace_file_read(resolved)
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(limit + 1)
        if len(text) > limit:
            return text[:limit] + f"\n...(read-file truncated at {limit} chars)..."
        return text
    except Exception as e:
        return f"(read-file error: {_sanitize_error_msg(e)})"


def _tool_write_file(path, content):
    try:
        resolved = _resolve_workspace_path(path)
        content = str(content)
        cap_size = _SUBAGENT_MAX_FILE_SIZE_CHARS
        if cap_size > 0 and len(content) > cap_size:
            return (
                f"(write-file error: content size {len(content)} exceeds "
                f"max file size {cap_size} chars)"
            )
        with _workspace_file_lock(resolved):
            _atomic_replace_text(resolved, content)
        return "WRITE-FILE-SUCCESS"
    except Exception as e:
        return f"(write-file error: {_sanitize_error_msg(e)})"


def _tool_append_file(path, content):
    try:
        resolved = _resolve_workspace_path(path)
        content = str(content)
        cap_size = _SUBAGENT_MAX_FILE_SIZE_CHARS
        with _workspace_file_lock(resolved):
            existing = ""
            existing_size = 0
            if os.path.exists(resolved):
                fd = _open_workspace_file_read(resolved)
                try:
                    existing_size = os.fstat(fd).st_size
                    # Check existing file size before reading to avoid memory exhaustion.
                    # Use the already-open no-follow fd so a local symlink swap between
                    # path resolution and size inspection cannot redirect the append check.
                    if cap_size > 0 and existing_size > cap_size:
                        os.close(fd)
                        return (
                            f"(append-file error: existing file size {existing_size} exceeds "
                            f"max file size {cap_size} chars)"
                        )
                    if cap_size > 0 and existing_size + len(content) + 1 > cap_size:
                        os.close(fd)
                        return (
                            f"(append-file error: resulting file size "
                            f"{existing_size + len(content) + 1} would exceed "
                            f"max file size {cap_size} chars)"
                        )
                    with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
                        # Re-enforce the cap while reading: the file can grow after
                        # fstat, and an initial size check alone must not permit an
                        # unbounded read or an oversized atomic replacement.
                        existing = f.read(cap_size + 1) if cap_size > 0 else f.read()
                    if cap_size > 0 and len(existing) > cap_size:
                        return (
                            f"(append-file error: existing file exceeds "
                            f"max file size {cap_size} chars during read)"
                        )
                    if cap_size > 0 and len(existing) + len(content) + 1 > cap_size:
                        return (
                            f"(append-file error: resulting file size "
                            f"{len(existing) + len(content) + 1} would exceed "
                            f"max file size {cap_size} chars)"
                        )
                except Exception:
                    with contextlib.suppress(Exception):
                        os.close(fd)
                    raise
            elif cap_size > 0 and len(content) + 1 > cap_size:
                return (
                    f"(append-file error: resulting file size {len(content) + 1} "
                    f"would exceed max file size {cap_size} chars)"
                )
            new_content = existing
            if new_content and not new_content.endswith("\n"):
                new_content += "\n"
            new_content += content + "\n"
            _atomic_replace_text(resolved, new_content)
        return "APPEND-FILE-SUCCESS"
    except Exception as e:
        return f"(append-file error: {_sanitize_error_msg(e)})"


def _shell_safe_path_env(workspace):
    """Return a PATH that cannot resolve executables from the workspace/cwd.

    The shell tool already requires command-name-only executable tokens and an
    explicit allowlist. Because commands run with cwd fixed to the workspace,
    inherited PATH entries such as '.', '', or the workspace itself could still
    make an allowlisted basename resolve to a workspace-controlled executable.
    Drop those entries before launching the argv-list subprocess.
    """
    root = os.path.realpath(os.path.abspath(workspace))
    safe_parts = []
    for part in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not part or part == ".":
            continue
        resolved = os.path.realpath(os.path.abspath(part))
        try:
            if os.path.commonpath([root, resolved]) == root:
                continue
        except Exception:
            continue
        safe_parts.append(part)
    return os.pathsep.join(safe_parts) or os.defpath


def _shell_safe_env(workspace):
    """Return a minimal environment for optional subagent shell commands.

    Even when the shell tool is explicitly enabled, the child process should
    not inherit API keys, tokens, or arbitrary operator/session variables from
    the parent agent. Keep only locale-ish process settings plus a sanitized
    PATH, and pin HOME/PWD-style behavior to the subagent workspace.
    """
    env = {
        "PATH": _shell_safe_path_env(workspace),
        "HOME": workspace,
    }
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def _tool_shell(cmd):
    """Restricted command runner: disabled unless explicitly enabled and
    executable-allowlisted. Uses shell=False so metacharacters are arguments,
    not command separators, and runs from the subagent workspace root."""
    if not _shell_enabled():
        return "(shell error: disabled by default; set OMEGACLAW_SUBAGENT_ENABLE_SHELL=1 and OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST)"
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return f"(shell error: invalid command: {e})"
    if not argv:
        return "(shell error: empty command)"
    if len(argv) > _SHELL_MAX_ARGV:
        return f"(shell error: too many arguments; max {_SHELL_MAX_ARGV} argv tokens)"
    if any("\x00" in str(arg) for arg in argv):
        return "(shell error: arguments must not contain NUL bytes)"
    allow = _shell_allowlist()
    exe_token = argv[0]
    exe = os.path.basename(exe_token)
    if exe_token != exe:
        return "(shell error: executable must be an allowlisted command name, not a path)"
    if not allow or exe not in allow:
        return f"(shell error: executable '{exe}' is not allowlisted)"
    try:
        workspace = _subagent_workspace_root()
    except ValueError as e:
        return f"(shell error: {_sanitize_error_msg(e)})"
    if not os.path.isdir(workspace):
        return "(shell error: subagent workspace does not exist)"
    env = _shell_safe_env(workspace)
    try:
        with tempfile.TemporaryFile() as output:
            subprocess.run(
                argv,
                shell=False,
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=_SHELL_TIMEOUT_S,
            )
            output.seek(0)
            raw = output.read(_SHELL_OUTPUT_CAP + 1)
        text = raw.decode("utf-8", errors="replace")
        if len(raw) > _SHELL_OUTPUT_CAP:
            return text[:_SHELL_OUTPUT_CAP] + f"\n...(shell output truncated at {_SHELL_OUTPUT_CAP} chars)..."
        return text
    except subprocess.TimeoutExpired:
        return f"(shell error: timed out after {_SHELL_TIMEOUT_S}s)"
    except Exception as e:
        return f"(shell error: {_sanitize_error_msg(e)})"


def _validate_relative_workspace_path_arg(path, label="path argument"):
    """Validate prompt/contract file paths before workspace resolution.

    File-tool paths and task-contract ``allowed_paths`` are prompt-visible local
    control data. Keep them workspace-relative, traversal-free, bounded, and
    free of control characters before they reach audit records, contract checks,
    or filesystem helpers.
    """
    raw_path = str(path)
    if not raw_path.strip():
        return f"{label} must not be empty"
    if len(raw_path) > _SUBAGENT_MAX_PATH_ARG_CHARS:
        return f"{label} exceeds {_SUBAGENT_MAX_PATH_ARG_CHARS} characters"
    if _contains_text_control(raw_path):
        return f"{label} must not contain control characters"
    if os.path.isabs(raw_path):
        return f"{label} must be relative to the subagent workspace"
    raw_parts = raw_path.split(os.sep)
    normalized = os.path.normpath(raw_path)
    normalized_parts = normalized.split(os.sep)
    if os.pardir in raw_parts or normalized == os.pardir or os.pardir in normalized_parts:
        return f"{label} must not contain parent-directory traversal"
    return ""


def _contains_text_control(value):
    """Reject controls, separators, surrogates, and unsafe format characters.

    ``str`` arguments can contain non-ASCII separators that split rendered log
    or prompt lines even though they are not covered by the usual ``ord < 32``
    check. Invisible formatting characters can make paths, commands, or audit
    text misleading, while lone surrogates can fail later during UTF-8 encoding.
    Treat Unicode ``Cc``/``Cs``/``Zl``/``Zp`` and ``Cf`` characters as control
    data at tool/control boundaries, except for the common linguistic joiners
    U+200C/U+200D.
    """
    allowed_format_characters = {"\u200c", "\u200d"}
    return any(
        unicodedata.category(ch) in ("Cc", "Cs", "Zl", "Zp")
        or (
            unicodedata.category(ch) == "Cf"
            and ch not in allowed_format_characters
        )
        for ch in str(value)
    )


def _escape_text_controls(value):
    """Render unsafe text controls visibly for failed-input audit records."""
    if isinstance(value, dict):
        return {key: _escape_text_controls(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_escape_text_controls(item) for item in value]
    if not isinstance(value, str):
        return value
    return "".join(
        f"\\u{ord(ch):04x}" if _contains_text_control(ch) else ch
        for ch in value
    )


def _validate_tool_args(name, args):
    expected = {
        "read-file": 1,
        "shell": 1,
        "search": 1,
        "tavily-search": 1,
        "technical-analysis": 1,
        "write-file": 2,
        "append-file": 2,
    }
    if name not in expected:
        return None
    want = expected[name]
    if len(args) != want:
        return f"expected {want} arg(s), got {len(args)}"
    if any(a is None for a in args):
        return "arguments must not be null"
    if any(not isinstance(a, str) for a in args):
        return "arguments must be strings"
    if name in ("read-file", "write-file", "append-file"):
        path_error = _validate_relative_workspace_path_arg(args[0])
        if path_error:
            return path_error
    if name in ("search", "tavily-search", "technical-analysis"):
        query = str(args[0])
        if not query.strip():
            return "query argument must not be empty or whitespace-only"
        if len(query) > _SUBAGENT_MAX_QUERY_ARG_CHARS:
            return f"query argument exceeds {_SUBAGENT_MAX_QUERY_ARG_CHARS} characters"
        if _contains_text_control(query):
            return "query argument must not contain control characters"
    if name == "technical-analysis":
        ticker = args[0]
        if not re.fullmatch(r"[A-Za-z0-9^][A-Za-z0-9.^=_-]{0,31}", ticker):
            return "ticker argument must be a 1-32 character market symbol"
    if name == "shell":
        command = str(args[0])
        if not command.strip():
            return "shell command must not be empty or whitespace-only"
        if len(command) > _SUBAGENT_MAX_SHELL_ARG_CHARS:
            return f"shell command exceeds {_SUBAGENT_MAX_SHELL_ARG_CHARS} characters"
        if _contains_text_control(command):
            return "shell command must not contain control characters"
    too_long = [i + 1 for i, arg in enumerate(args) if len(str(arg)) > _SUBAGENT_MAX_TOOL_ARG_CHARS]
    if too_long:
        return f"argument(s) {too_long} exceed {_SUBAGENT_MAX_TOOL_ARG_CHARS} characters"
    if any("\x00" in str(a) for a in args):
        return "arguments must not contain NUL bytes"
    return None


def _cancel_requested():
    try:
        path = _resolve_run_control_file_path(_SUBAGENT_CANCEL_FILE, "cancel_file")
    except ValueError:
        return True
    return _run_control_token_present(path)


def _dispatch_timeout_exceeded(start_time):
    """Check if the dispatch-level wall-clock timeout has been exceeded.

    Returns True if the timeout is enabled and the elapsed time since
    start_time exceeds the configured limit.
    """
    limit = _SUBAGENT_DISPATCH_TIMEOUT_S
    if limit <= 0:
        return False
    return (time.time() - start_time) >= limit


def _dispatch_timeout_remaining(start_time):
    """Return remaining seconds before dispatch timeout, or None if disabled."""
    limit = _SUBAGENT_DISPATCH_TIMEOUT_S
    if limit <= 0:
        return None
    remaining = limit - (time.time() - start_time)
    return max(0.0, remaining)


def run_tools(calls, allowed_names, record=None, quota=None, task_contract=None):
    """Execute each call against the registry, return aggregated result
    string for the next turn's prompt."""
    if not calls:
        return "(no parseable tool calls in last response)"
    reg = _tool_registry()
    out_parts = []
    remaining = None if quota is None else max(0, int(quota))
    turn_calls_seen = 0
    per_turn_limit = max(1, int(_SUBAGENT_MAX_TOOL_CALLS_PER_TURN))
    for (name, args) in calls:
        if _cancel_requested():
            out_parts.append("(CANCELLED: subagent cancellation token present)")
            break
        if name != "emit":
            turn_calls_seen += 1
            if turn_calls_seen > per_turn_limit:
                out_parts.append(
                    f"(TURN_QUOTA_EXCEEDED: subagent tool-call per-turn limit {per_turn_limit} exhausted)"
                )
                break
            if remaining is not None and remaining <= 0:
                out_parts.append("(QUOTA_EXCEEDED: subagent tool-call quota exhausted)")
                break
            if remaining is not None:
                remaining -= 1
        if name == "emit":
            # emit is the loop terminator; handled by the caller
            continue
        if _tool_forbidden_by_contract(name, task_contract):
            out_parts.append(f"(CONTRACT_VIOLATION: {name} is forbidden by task contract)")
            continue
        if name not in allowed_names:
            out_parts.append(
                f"(SKILL_REJECTED: {name} not in this dispatch's tool subset)"
            )
            continue
        tool = reg.get(name)
        if tool is None:
            out_parts.append(f"(SKILL_UNAVAILABLE: {name} not registered)")
            continue
        arg_error = _validate_tool_args(name, args)
        if arg_error:
            out_parts.append(f"(SKILL_ARG_ERROR: {name}: {arg_error})")
            continue
        if name in ("read-file", "write-file", "append-file") and not _path_within_contract(args[0], task_contract):
            out_parts.append(
                f"(CONTRACT_VIOLATION: {name} path '{args[0]}' outside allowed_paths)"
            )
            continue
        if name in ("write-file", "append-file") and _contract_patch_proposal_only(task_contract):
            if record is not None:
                record.setdefault("patch_proposals", []).append({
                    "action": name,
                    "path": str(args[0]),
                    "content": _bound_patch_proposal_content(args[1]),
                })
            out_parts.append(
                f"(PATCH_PROPOSAL_RECORDED: {name} path '{args[0]}' not applied; "
                "parent must review/test/apply)"
            )
            continue
        fn, _category = tool
        try:
            result = fn(*args)
        except TypeError as e:
            out_parts.append(f"(SKILL_ARG_ERROR: {name}: {_sanitize_error_msg(e)})")
            continue
        except Exception as e:
            out_parts.append(f"(SKILL_RUNTIME_ERROR: {name}: {_sanitize_error_msg(e)})")
            continue
        if record is not None and name in ("write-file", "append-file") and str(result).endswith("SUCCESS"):
            record.setdefault("files_changed", []).append(str(args[0]))
        if record is not None and name == "shell" and args and _looks_like_test_command(str(args[0])):
            record.setdefault("tests_run", []).append(str(args[0]))
        out_parts.append(f"(COMMAND_RETURN: ({name} {args[0] if args else ''}) "
                         f"{_clip(str(result), 2000)})")
    if record is not None and remaining is not None:
        record["tool_calls_remaining"] = remaining
    return " ".join(out_parts)


def _looks_like_test_command(cmd):
    try:
        argv = shlex.split(cmd) if cmd else []
    except ValueError:
        return False
    names = {os.path.basename(a) for a in argv[:3]}
    return bool(names & {"pytest", "unittest", "tox", "nox", "make"}) or " test" in f" {cmd} "


def _bound_patch_proposal_content(content):
    text = str(content)
    cap = max(1, int(_SUBAGENT_MAX_PATCH_PROPOSAL_CHARS))
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n[...patch proposal content truncated at {cap} chars...]"


def _extract_final_emit(calls):
    """Return (emit_value, protocol_error) for a final-only emit response.

    Earlier prototypes accepted the first `(emit ...)` anywhere in a worker
    response. That let a malformed or adversarial response hide later tool calls
    or conflicting emits from the parent. Harden the contract: a final digest is
    accepted only when the parsed response contains exactly one call, and that
    call is a single-argument `emit`.
    """
    emit_calls = [(name, args) for (name, args) in calls if name == "emit"]
    if not emit_calls:
        return (None, "")
    if len(calls) != 1 or len(emit_calls) != 1:
        return (None, "EMIT_PROTOCOL_VIOLATION: emit must be the only parsed call in a final response")
    _name, args = emit_calls[0]
    if len(args) != 1 or args[0] is None:
        return (None, "EMIT_PROTOCOL_VIOLATION: emit requires exactly one non-null argument")
    if not isinstance(args[0], str):
        return (None, "EMIT_PROTOCOL_VIOLATION: emit argument must be a string")
    if not args[0].strip():
        return (
            None,
            "EMIT_PROTOCOL_VIOLATION: emit argument must not be empty or whitespace-only",
        )
    if _contains_text_control(args[0]):
        return (
            None,
            "EMIT_PROTOCOL_VIOLATION: emit argument must not contain control characters",
        )
    if len(args[0]) > _SUBAGENT_MAX_EMIT_CHARS:
        return (
            None,
            "EMIT_PROTOCOL_VIOLATION: emit argument exceeds "
            f"{_SUBAGENT_MAX_EMIT_CHARS} characters",
        )
    return (args[0], "")


def _validate_final_emit_envelope(raw_response, emit_value):
    """Require a successful emit to be the response's only protocol record.

    ``parse_calls`` intentionally skips prose and malformed lines so ordinary
    tool turns can recover.  That tolerance must not let a final response hide
    ignored text or a malformed second call beside an otherwise valid emit.
    Thinking blocks and markdown fences are stripped using the same rules as
    the call parser; after that, exactly one non-empty ASCII-newline record is
    allowed.
    """
    if emit_value is None:
        return ""
    text = _strip_fences(_strip_thinking(str(raw_response)))
    records = [line.strip() for line in text.split("\n") if line.strip()]
    if len(records) != 1:
        return (
            "EMIT_PROTOCOL_VIOLATION: emit response must not contain "
            "unparsed text or additional records"
        )
    return ""


# ----------------------------------------------------------------------
# Result post-processing
# ----------------------------------------------------------------------

def cap(text, max_chars):
    """Newline-to-space + hard length cap. Ensures the digest lands
    cleanly inside the parent's LAST_SKILL_USE_RESULTS."""
    s = (text or "").replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())
    if len(s) > max_chars:
        s = s[: max_chars - 3] + "..."
    return s


def error(msg):
    """Wrap an error into the legacy setup-error string.

    Dispatch uses _structured_setup_error where possible so failures also get
    transcript records; keep this helper for direct callers and exceptional
    pre-dispatch paths.
    """
    return f"(subagent error: {msg})"


def _structured_setup_error(msg, persona_key, goal, max_chars,
                            record_status="setup_error", task_contract=None,
                            next_action="fix setup/config before retry"):
    """Return a structured error digest and persist a minimal run record.

    Early failures used to return only `(subagent error: ...)`, which made them
    invisible to transcript/audit tooling. Persist enough local context for the
    parent/operator to debug without making any worker LLM call.
    """
    safe_goal = _escape_text_controls(goal)
    record = _new_run_record(persona_key or "unknown", safe_goal)
    if task_contract is not None:
        record["task_contract"] = _escape_text_controls(dict(task_contract))
    summary = error(msg)
    _finish_run_record(record, record_status, summary)
    return _structured_return(
        summary, record, status="error", uncertainty="high",
        next_action=next_action, max_chars=max_chars,
    )


# ----------------------------------------------------------------------
# The dispatch entry point — called from MeTTa via py-call
# ----------------------------------------------------------------------

def _parse_dispatch_integer_limit(value, default, label):
    """Parse a direct-dispatch integer limit without lossy coercion.

    MeTTa/Python callers may supply decimal strings, but booleans, floats, and
    malformed or pathologically long strings must not silently become a
    different worker run. Clamping valid integers to the configured safety
    floor/cap remains the caller's responsibility.
    """
    if value is None:
        return default, ""
    if isinstance(value, bool):
        return default, f"{label} must be an integer"
    if isinstance(value, int):
        return value, ""
    if isinstance(value, str):
        text = value.strip()
        if len(text) > 64:
            return default, f"{label} integer is too long"
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text), ""
    return default, f"{label} must be an integer"


def dispatch(goal, tool_subset_csv, persona_key, max_turns=None,
             max_chars=None):
    """Entry point invoked by (delegate ...) in src/skills.metta.

    Returns a single-line string (length ≤ max_chars or
    SUBAGENT_MAX_DIGEST_CHARS) suitable for inclusion in the
    parent's LAST_SKILL_USE_RESULTS.

    Failure path always returns a (subagent error: ...) string;
    never raises into the MeTTa interpreter."""
    # 1. Bound the per-call caps
    max_turns, turns_error = _parse_dispatch_integer_limit(
        max_turns, SUBAGENT_MAX_TURNS_HARD_CAP, "max_turns"
    )
    bounded_turns = max(1, min(max_turns, SUBAGENT_MAX_TURNS_HARD_CAP))

    max_chars, chars_error = _parse_dispatch_integer_limit(
        max_chars, SUBAGENT_MAX_DIGEST_CHARS, "max_chars"
    )
    bounded_chars = max(100, min(max_chars, SUBAGENT_MAX_DIGEST_CHARS))
    # Direct Python/MeTTa inputs are tool-boundary data. Do not stringify
    # malformed scalar values or let ``.strip()`` raise after partial setup.
    # Inline task contracts remain JSON encoded strings and are validated
    # separately below.
    scalar_errors = []
    if not isinstance(goal, str):
        scalar_errors.append("goal must be a string")
    elif not goal.strip():
        scalar_errors.append("goal must be a non-empty string")
    if not isinstance(persona_key, str):
        scalar_errors.append("persona_key must be a string")
    if tool_subset_csv is not None and not isinstance(tool_subset_csv, str):
        scalar_errors.append("tool_subset_csv must be a string or null")
    argument_errors = [
        item for item in (turns_error, chars_error, *scalar_errors) if item
    ]
    if argument_errors:
        return _structured_setup_error(
            "; ".join(argument_errors),
            persona_key if isinstance(persona_key, str) else "invalid",
            goal if isinstance(goal, str) else "",
            bounded_chars,
            record_status="dispatch_args_invalid",
            next_action="fix dispatch arguments before retry",
        )

    # 2. Load persona config
    try:
        cfg = load_persona_config(persona_key)
    except (FileNotFoundError, ValueError) as e:
        return _structured_setup_error(str(e), persona_key, goal, bounded_chars)
    objective, task_contract = _normalize_task_contract(goal, cfg)
    contract_error = _validate_task_contract(task_contract)
    if contract_error:
        return _structured_setup_error(
            contract_error, persona_key, objective, bounded_chars,
            record_status="contract_invalid", task_contract=task_contract,
            next_action="fix task contract before retry",
        )

    # 2b. ThreadKeeper escalation gate. A delegation to a CLOUD specialist is
    # the expensive node — consult the budget policy (src/escalation.metta via
    # PeTTa) before spending. If denied, refuse the dispatch and return the
    # [metta]-tagged reason so the parent loop sees WHY (and can finish on cheap
    # nodes). Local delegations are free and pass through. Gate errors fail
    # closed by default unless an operator explicitly sets fail-open fallback.
    gate_allowed, gate_reason = _escalation_gate(cfg)
    if not gate_allowed:
        denial = (
            f"(escalation denied) {gate_reason} — "
            f"cloud delegation to persona '{persona_key}' refused by the "
            f"ThreadKeeper budget policy; finish on local/cheap nodes or stop."
        )
        record = _new_run_record(persona_key, objective)
        record["task_contract"] = dict(task_contract)
        _finish_run_record(record, "escalation_denied", denial)
        return _structured_return(
            denial, record, status="error", uncertainty="high",
            next_action="finish on local/cheap nodes or stop", max_chars=bounded_chars,
        )

    # 3. Resolve tool subset
    subset_csv = (tool_subset_csv or "").strip()
    if not subset_csv:
        default_subset = cfg.get("default_tool_subset", [])
        if not default_subset:
            return _structured_setup_error(
                f"no tool subset given and persona '{persona_key}' has no "
                "default_tool_subset",
                persona_key, objective, bounded_chars,
                record_status="tool_subset_invalid", task_contract=task_contract,
                next_action="provide an explicit tool subset or persona default_tool_subset",
            )
        subset_csv = ",".join(default_subset)
    try:
        tool_names = parse_subset(subset_csv)
    except ValueError as e:
        return _structured_setup_error(
            str(e), persona_key, objective, bounded_chars,
            record_status="tool_subset_invalid", task_contract=task_contract,
            next_action="fix delegate tool subset before retry",
        )

    validate_endpoint_compat(tool_names, cfg)  # always passes in v1

    # 4. Load persona prompt
    try:
        persona_text = load_persona_prompt(
            cfg["persona_file"], persona_key, cfg.get("persona_sha256")
        )
    except (FileNotFoundError, ValueError) as e:
        return _structured_setup_error(
            str(e), persona_key, objective, bounded_chars,
            record_status="persona_prompt_invalid", task_contract=task_contract,
        )

    if _queue_only_enabled():
        queued_record = _new_run_record(persona_key, objective)
        queued_record["task_contract"] = dict(task_contract)
        if _cancel_requested():
            _finish_run_record(queued_record, "cancelled", "subagent cancellation token present before queue")
            return _structured_return(
                "subagent cancellation token present before queue", queued_record,
                status="cancelled", uncertainty="low", next_action="return to parent",
                max_chars=bounded_chars,
            )
        return _enqueue_dispatch_record(queued_record, tool_names, bounded_turns, bounded_chars)

    # 5. Resolve provider
    try:
        provider_handle = resolve_or_instantiate_provider(
            provider_name=cfg["provider"],
            model_name=cfg["model"],
            base_url=cfg.get("base_url"),
            var_name=cfg["api_key_env"],
            endpoint_kind=cfg.get("_endpoint_kind"),
        )
    except RuntimeError as e:
        return _structured_setup_error(
            str(e), persona_key, objective, bounded_chars,
            record_status="provider_invalid", task_contract=task_contract,
        )

    # 6. Run the mini-loop
    catalog = tools_catalog(tool_names)
    history = []
    history_digest = []
    last_results = ""
    tool_calls_remaining = max(0, _SUBAGENT_MAX_TOOL_CALLS)
    if "max_tool_calls" in task_contract:
        tool_calls_remaining = min(tool_calls_remaining, max(0, int(task_contract["max_tool_calls"])))
    max_out_tok = int(cfg.get("max_output_tokens", SUBAGENT_DEFAULT_OUTPUT_TOKENS))
    run_record = _new_run_record(persona_key, objective)
    run_record["task_contract"] = dict(task_contract)
    dispatch_start = time.time()
    total_in_tokens = 0
    total_out_tokens = 0

    def _stamp_token_usage():
        run_record["worker_token_usage"] = {
            "input_tokens": total_in_tokens,
            "output_tokens": total_out_tokens,
            "total_tokens": total_in_tokens + total_out_tokens,
        }

    for turn in range(bounded_turns):
        if _cancel_requested():
            _finish_run_record(run_record, "cancelled", "subagent cancellation token present before LLM call")
            return _structured_return(
                "subagent cancellation token present before LLM call", run_record,
                status="cancelled", uncertainty="low", next_action="return to parent",
                max_chars=bounded_chars,
            )
        if _dispatch_timeout_exceeded(dispatch_start):
            timeout_msg = (
                f"(subagent: dispatch wall-clock timeout "
                f"({_SUBAGENT_DISPATCH_TIMEOUT_S:.0f}s) exceeded at turn {turn + 1})"
            )
            _stamp_token_usage()
            _finish_run_record(run_record, "dispatch_timeout", timeout_msg)
            return _structured_return(
                timeout_msg, run_record, status="error", uncertainty="high",
                next_action="dispatch a narrower task or raise OMEGACLAW_SUBAGENT_DISPATCH_TIMEOUT_S",
                max_chars=bounded_chars,
            )
        prompt = build_subagent_prompt(
            persona_text, catalog, last_results, history, objective,
            turn + 1, bounded_turns, history_digest, task_contract,
        )
        raw, in_tok, out_tok = _call_subagent_llm(provider_handle, prompt, max_out_tok)
        total_in_tokens += in_tok
        total_out_tokens += out_tok
        if _SUBAGENT_MAX_TOKENS_PER_DISPATCH and (total_in_tokens + total_out_tokens) > _SUBAGENT_MAX_TOKENS_PER_DISPATCH:
            budget_msg = (
                f"(subagent: dispatch token budget "
                f"({_SUBAGENT_MAX_TOKENS_PER_DISPATCH}) exceeded at turn {turn + 1} "
                f"with {total_in_tokens + total_out_tokens} total tokens)"
            )
            _stamp_token_usage()
            _finish_run_record(run_record, "token_budget_exceeded", budget_msg)
            return _structured_return(
                budget_msg, run_record, status="error", uncertainty="high",
                next_action="dispatch a narrower task or raise OMEGACLAW_SUBAGENT_MAX_TOKENS_PER_DISPATCH",
                max_chars=bounded_chars,
            )
        turn_record = {"turn": turn + 1, "prompt": prompt, "raw_response": raw, "tool_calls": []}
        # If the call failed catastrophically, _call_subagent_llm
        # already returned a (subagent ...) string; surface as digest.
        if (
            raw.startswith("(subagent LLM call failed")
            or raw.startswith("(subagent LLM call rate-limited")
            or raw.startswith("(subagent LLM call concurrency-limited")
        ):
            run_record.setdefault("turns", []).append(turn_record)
            failed_status = "rate_limited" if "rate-limited" in raw else "llm_failed"
            if "concurrency-limited" in raw:
                failed_status = "concurrency_limited"
            _stamp_token_usage()
            _finish_run_record(run_record, failed_status, raw)
            return _structured_return(
                raw, run_record, status="error", uncertainty="high",
                next_action="inspect transcript_path or retry later", max_chars=bounded_chars,
            )

        if len(str(raw)) > _SUBAGENT_MAX_RESPONSE_CHARS:
            response_msg = (
                "(subagent: worker response exceeded "
                f"OMEGACLAW_SUBAGENT_MAX_RESPONSE_CHARS={_SUBAGENT_MAX_RESPONSE_CHARS}; "
                "refusing to parse or execute it)"
            )
            turn_record["raw_response"] = cap(str(raw), _SUBAGENT_MAX_RESPONSE_CHARS)
            turn_record["tool_results"] = response_msg
            run_record.setdefault("turns", []).append(turn_record)
            _stamp_token_usage()
            _finish_run_record(run_record, "response_too_large", response_msg)
            return _structured_return(
                response_msg, run_record, status="error", uncertainty="high",
                next_action="dispatch a narrower task or raise OMEGACLAW_SUBAGENT_MAX_RESPONSE_CHARS",
                max_chars=bounded_chars,
            )

        calls = parse_calls(raw)
        turn_record["tool_calls"] = [{"name": n, "args": a} for (n, a) in calls]
        emit_value, emit_error = _extract_final_emit(calls)
        if not emit_error:
            emit_error = _validate_final_emit_envelope(raw, emit_value)
        if emit_error:
            turn_record["tool_results"] = emit_error
            # Malformed worker arguments can contain characters that are not
            # safe to encode or render in transcript/audit JSON. Preserve the
            # evidence visibly rather than letting transcript persistence fail.
            turn_record = _escape_text_controls(turn_record)
            run_record.setdefault("turns", []).append(turn_record)
            _stamp_token_usage()
            _finish_run_record(run_record, "emit_protocol_violation", emit_error)
            return _structured_return(
                emit_error, run_record, status="error", uncertainty="high",
                next_action="retry with a well-formed final emit or inspect transcript_path",
                max_chars=bounded_chars,
            )
        if emit_value is not None:
            run_record.setdefault("turns", []).append(turn_record)
            _stamp_token_usage()
            if _contract_requires_adjudication(task_contract):
                run_record["adjudication"] = {
                    "required": True,
                    "status": "pending",
                    "candidate_summary": cap(emit_value, SUBAGENT_MAX_DIGEST_CHARS),
                    "candidate_turn": turn + 1,
                }
                summary = f"subagent candidate output requires adjudication: {cap(emit_value, 600)}"
                _finish_run_record(run_record, "adjudication_required", summary)
                return _structured_return(
                    summary, run_record, status="needs_adjudication", uncertainty="medium",
                    next_action="route transcript_path/candidate_summary to an adjudicator before accepting",
                    max_chars=bounded_chars,
                )
            _finish_run_record(run_record, "ok", emit_value)
            return _structured_return(emit_value, run_record, max_chars=bounded_chars)

        last_results = run_tools(
            calls, tool_names, run_record, quota=tool_calls_remaining,
            task_contract=task_contract,
        )
        tool_calls_remaining = run_record.get("tool_calls_remaining", tool_calls_remaining)
        if "QUOTA_EXCEEDED" in last_results:
            turn_record["tool_results"] = last_results
            run_record.setdefault("turns", []).append(turn_record)
            record_status = "turn_quota_exceeded" if "TURN_QUOTA_EXCEEDED" in last_results else "quota_exceeded"
            _stamp_token_usage()
            _finish_run_record(run_record, record_status, last_results)
            return _structured_return(
                last_results, run_record, status="error", uncertainty="medium",
                next_action="dispatch with a narrower task or higher explicit quota",
                max_chars=bounded_chars,
            )
        if "CANCELLED:" in last_results:
            turn_record["tool_results"] = last_results
            run_record.setdefault("turns", []).append(turn_record)
            _stamp_token_usage()
            _finish_run_record(run_record, "cancelled", last_results)
            return _structured_return(
                last_results, run_record, status="cancelled", uncertainty="low",
                next_action="return to parent", max_chars=bounded_chars,
            )
        turn_record["tool_results"] = last_results
        run_record.setdefault("turns", []).append(turn_record)
        _append_bounded_history(history, (turn + 1, raw, last_results), history_digest)
        run_record["history_digest"] = list(history_digest)

    # Loop exhausted without (emit ...)
    fallback = (
        f"(subagent: max_turns ({bounded_turns}) reached without emit; "
        f"last_results: {_clip(last_results, 500)})"
    )
    _stamp_token_usage()
    _finish_run_record(run_record, "max_turns", fallback)
    return _structured_return(
        fallback, run_record, status="incomplete", uncertainty="medium",
        next_action="review transcript_path or dispatch a narrower follow-up", max_chars=bounded_chars,
    )
