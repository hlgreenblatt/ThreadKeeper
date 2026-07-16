"""ThreadKeeper — cost-awareness & escalation seam.

ThreadKeeper's thesis is that *reasoning quality* should be decoupled
from *reasoning frequency*: the cheap local control loop runs every
iteration, and an expensive cloud specialist is invoked only for hard
subproblems. That "only when justified" needs an enforceable policy,
not a hope. This module is that policy seam.

It does three things:

  1. RECORD   — append one usage record per LLM call to a JSONL log
                (carried forward from OmegaClaw's memory/usage.jsonl).
  2. ACCOUNT  — sum tokens (and an example-rate cost estimate) per
                node-role and per thread.
  3. DECIDE   — `should_escalate(...)` weighs cumulative spend against
                the budget thresholds in threadkeeper.config.yaml and
                returns an auditable allow/deny decision.

It is intentionally small and dependency-light (stdlib + optional
PyYAML, which OmegaClaw already requires). The decision logic is a
working v1, but the SEAM is the point: swap in a richer policy
(per-provider rate cards, sliding windows, RL-tuned thresholds)
without touching the call sites.

Wiring: the worker/control loops call `BudgetTracker.record(...)`
after each LLM call, and the control loop calls
`BudgetTracker.should_escalate(...)` before issuing a `(delegate ...)`
to a cloud specialist. `src/subagent.py` already logs usage to the
same JSONL today; this module reads and reasons over it. Integrating
the `record()` call directly into `lib_llm_ext.AIProvider.chat` is the
natural next step and is marked in the README's roadmap.

This module never raises into the agent's reasoning path — every
public method degrades to a safe default if config or logs are
missing.
"""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    import yaml  # PyYAML — already in OmegaClaw requirements.txt
except Exception:  # pragma: no cover - degrade gracefully
    yaml = None


# ----------------------------------------------------------------------
# Defaults — used when threadkeeper.config.yaml is absent or unreadable.
# Mirror the values documented in threadkeeper.config.yaml so behavior
# is predictable even without the file.
# ----------------------------------------------------------------------
_DEFAULTS = {
    "thread_token_ceiling": 2_000_000,
    "escalation_soft_fraction": 0.5,
    "min_local_iterations_before_escalation": 2,
    "rates_per_1k_tokens": {
        "control_loop": {"input": 0.0, "output": 0.0},
        "worker_loop": {"input": 0.0, "output": 0.0},
        "cloud_specialist": {"input": 0.015, "output": 0.075},
        "adjudicator": {"input": 0.015, "output": 0.075},
    },
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(_REPO_ROOT, "threadkeeper.config.yaml")
_DEFAULT_USAGE_LOG = os.path.join(_REPO_ROOT, "memory", "usage.jsonl")
_DEFAULT_ESCALATION_LOG = os.path.join(_REPO_ROOT, "memory", "escalations.jsonl")
_DEFAULT_ESCALATION_METTA = os.path.join(_REPO_ROOT, "src", "escalation.metta")


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except Exception:
        return default


_MAX_BUDGET_LOG_BYTES = _int_env("THREADKEEPER_MAX_BUDGET_LOG_BYTES", 1024 * 1024, 1024)
_MAX_BUDGET_CONFIG_BYTES = _int_env("THREADKEEPER_MAX_BUDGET_CONFIG_BYTES", 64 * 1024, 1024)


def _open_regular_no_symlink(path: str, flags: int, mode: int = 0o600):
    """Open a local budget/accounting file without following symlinks."""
    open_flags = flags
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    fd = os.open(path, open_flags, mode)
    try:
        st = os.fstat(fd)
        if not os.path.isfile(path) or not stat.S_ISREG(st.st_mode):
            raise ValueError("path is not a regular non-symlink file")
        return fd
    except Exception:
        os.close(fd)
        raise


def _reject_unsafe_existing_file(path: str, label: str) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as e:
        raise ValueError(f"{label} stat failed: {e}")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")


def _ensure_real_directory(path: str) -> None:
    """Create/use a local directory tree without following symlink ancestors."""
    if not path:
        return
    target = os.path.abspath(path)
    parts = []
    cur = target
    while cur and cur != os.path.dirname(cur):
        parts.append(cur)
        cur = os.path.dirname(cur)
    parts.reverse()
    for component in parts:
        try:
            st = os.lstat(component)
        except FileNotFoundError:
            os.mkdir(component, 0o700)
            st = os.lstat(component)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError("directory must be a real non-symlink directory")


def _fsync_parent_dir(path: str) -> None:
    """Best-effort fsync for audit-log parent directory metadata."""
    parent = os.path.dirname(os.path.abspath(path))
    if not parent:
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open(parent, flags)
        os.fsync(fd)
    except Exception:
        return
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass


# ----------------------------------------------------------------------
# Data records
# ----------------------------------------------------------------------
@dataclass
class UsageRecord:
    """One LLM call's token accounting."""
    ts: float
    thread_id: str
    node_role: str          # control_loop | worker_loop | cloud_specialist | adjudicator
    model: str
    input_tokens: int
    output_tokens: int

    def cost_estimate(self, rates: dict) -> float:
        r = rates.get(self.node_role) or rates.get("cloud_specialist") or {}
        return (
            (self.input_tokens / 1000.0) * float(r.get("input", 0.0))
            + (self.output_tokens / 1000.0) * float(r.get("output", 0.0))
        )


@dataclass
class EscalationDecision:
    """The auditable result of a should_escalate() call."""
    allowed: bool
    reason: str
    thread_id: str
    spent_tokens: int = 0
    ceiling_tokens: int = 0
    soft_threshold_tokens: int = 0
    local_iterations: int = 0
    ts: float = field(default_factory=time.time)


# ----------------------------------------------------------------------
# The escalation POLICY engine — MeTTa-first, Python-fallback.
#
# ThreadKeeper's routing decision lives in src/escalation.metta as Atomspace
# rules (so the agent can read/rewrite it — the OmegaClaw self-modification
# story). This wrapper loads that policy into OmegaClaw's MeTTa runtime (PeTTa)
# once and evaluates `(tk-escalate ...)` against live facts. If PeTTa or the
# policy file is unavailable (e.g. on a host/CI without the runtime), it stays
# inert and the caller falls back to the equivalent Python rules — so behavior
# is identical either way and the reasoning path never breaks.
# ----------------------------------------------------------------------
class _MettaPolicy:
    """Loads escalation.metta into PeTTa and evaluates the verdict.

    `verdict(...)` returns (allowed: bool, reason: str) or None if the MeTTa
    runtime / policy file could not be used (caller then uses Python rules).
    """

    def __init__(self, metta_path: str):
        self._path = metta_path
        self._engine = None
        self._tried = False

    # Conventional locations of PeTTa's Python binding inside the runtime.
    # `petta.py` lives at <PeTTa>/python/petta.py; the agent process is usually
    # launched from /PeTTa, but we don't rely on that — we put the binding dir
    # on sys.path ourselves so the import works regardless of cwd/launcher.
    _PETTA_PYTHON_DIRS = (
        os.environ.get("PETTA_PYTHON_DIR", ""),
        "/PeTTa/python",
        os.path.join(os.path.dirname(_REPO_ROOT), "python"),  # repo under <PeTTa>/repos/*
    )

    def _import_petta(self):
        """Import PeTTa, adding its binding dir to sys.path if needed."""
        try:
            from petta import PeTTa  # already importable
            return PeTTa
        except Exception:
            pass
        import sys
        for d in self._PETTA_PYTHON_DIRS:
            if d and os.path.isfile(os.path.join(d, "petta.py")):
                if d not in sys.path:
                    sys.path.insert(0, d)
                try:
                    from petta import PeTTa
                    return PeTTa
                except Exception:
                    continue
        return None

    def _ensure(self):
        """Lazily import PeTTa and load the policy file. Never raises."""
        if self._tried:
            return self._engine
        self._tried = True
        try:
            st = os.lstat(self._path)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                return None
            PeTTa = self._import_petta()
            if PeTTa is None:
                return None
            eng = PeTTa(verbose=False)
            eng.load_metta_file(self._path)
            # Smoke-test one query so a broken load disables the path cleanly.
            probe = eng.process_metta_string(
                "!(tk-escalate 0 1 1 1 0 True)"
            )
            if not probe:
                return None
            self._engine = eng
        except Exception:
            self._engine = None
        return self._engine

    @staticmethod
    def _parse(results) -> Optional[tuple]:
        """Map PeTTa output like ['(deny "reason")'] -> (allowed, reason)."""
        if not results:
            return None
        s = str(results[0]).strip()
        low = s.lower()
        if low.startswith("(allow"):
            allowed = True
        elif low.startswith("(deny"):
            allowed = False
        else:
            return None
        # reason = the quoted string inside the tuple, if present
        reason = ""
        if '"' in s:
            try:
                reason = s.split('"', 1)[1].rsplit('"', 1)[0]
            except Exception:
                reason = ""
        return (allowed, reason)

    def verdict(
        self, spent, ceiling, soft, min_local, iters, hard
    ) -> Optional[tuple]:
        """Evaluate the MeTTa policy. Returns (allowed, reason) or None."""
        eng = self._ensure()
        if eng is None:
            return None
        try:
            hard_atom = "True" if hard else "False"
            call = (
                f"!(tk-escalate {int(spent)} {int(ceiling)} {int(soft)} "
                f"{int(min_local)} {int(iters)} {hard_atom})"
            )
            return self._parse(eng.process_metta_string(call))
        except Exception:
            return None


# ----------------------------------------------------------------------
# The tracker
# ----------------------------------------------------------------------
class BudgetTracker:
    """Tracks token usage per loop and decides escalation against budget.

    Construct once per process (or per thread). Cheap to construct: it
    reads config lazily and never touches the network.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        usage_log: Optional[str] = None,
        escalation_log: Optional[str] = None,
    ):
        self._config_path = config_path or os.environ.get(
            "THREADKEEPER_CONFIG", _DEFAULT_CONFIG_PATH
        )
        self._budget = self._load_budget()
        gov = self._load_governance()
        self.usage_log = usage_log or gov.get("usage_log_abs", _DEFAULT_USAGE_LOG)
        self.escalation_log = escalation_log or gov.get(
            "escalation_log_abs", _DEFAULT_ESCALATION_LOG
        )
        self._record_decisions = gov.get("record_escalation_decisions", True)

        # Escalation policy lives in MeTTa (src/escalation.metta); the path is
        # configurable via governance.escalation_policy_metta. The engine is
        # lazy + degrades to None, so constructing it here is free and safe.
        policy_path = gov.get("escalation_metta_abs", _DEFAULT_ESCALATION_METTA)
        self._policy = _MettaPolicy(policy_path)

    # -- config loading -------------------------------------------------
    def _load_raw_config(self) -> dict:
        if yaml is None:
            return {}
        try:
            st = os.lstat(self._config_path)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                return {}
            if st.st_size > _MAX_BUDGET_CONFIG_BYTES:
                return {}
            fd = _open_regular_no_symlink(self._config_path, os.O_RDONLY)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode):
                    os.close(fd)
                    return {}
                if opened.st_size > _MAX_BUDGET_CONFIG_BYTES:
                    os.close(fd)
                    return {}
                with os.fdopen(fd, "rb") as f:
                    raw = f.read(_MAX_BUDGET_CONFIG_BYTES + 1)
                if len(raw) > _MAX_BUDGET_CONFIG_BYTES:
                    return {}
                return yaml.safe_load(raw.decode("utf-8")) or {}
            except UnicodeDecodeError:
                return {}
        except Exception:
            return {}

    def _load_budget(self) -> dict:
        cfg = self._load_raw_config().get("budget", {})
        merged = dict(_DEFAULTS)
        merged.update({k: v for k, v in cfg.items() if v is not None})
        # ensure rates always present
        if "rates_per_1k_tokens" not in merged or not merged["rates_per_1k_tokens"]:
            merged["rates_per_1k_tokens"] = _DEFAULTS["rates_per_1k_tokens"]
        return merged

    def _load_governance(self) -> dict:
        gov = self._load_raw_config().get("governance", {}) or {}
        out = dict(gov)
        # resolve relative log paths against repo root
        if gov.get("usage_log"):
            out["usage_log_abs"] = self._abs(gov["usage_log"])
        if gov.get("escalation_log"):
            out["escalation_log_abs"] = self._abs(gov["escalation_log"])
        if gov.get("escalation_policy_metta"):
            out["escalation_metta_abs"] = self._abs(gov["escalation_policy_metta"])
        return out

    @staticmethod
    def _abs(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(_REPO_ROOT, p)

    # -- recording ------------------------------------------------------
    def record(
        self,
        node_role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        thread_id: str = "default",
    ) -> UsageRecord:
        """Append one usage record to the JSONL log. Never raises."""
        rec = UsageRecord(
            ts=time.time(),
            thread_id=thread_id,
            node_role=node_role,
            model=model,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
        )
        try:
            _ensure_real_directory(os.path.dirname(self.usage_log))
            _reject_unsafe_existing_file(self.usage_log, "usage log")
            fd = _open_regular_no_symlink(
                self.usage_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND
            )
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            _fsync_parent_dir(self.usage_log)
        except Exception:
            pass  # accounting must never break the response path
        return rec

    def record_from_openai_response(self, node_role, model, resp, thread_id="default"):
        """Convenience: pull usage straight off an OpenAI-style response
        object (`resp.usage.prompt_tokens` / `.completion_tokens`)."""
        u = getattr(resp, "usage", None)
        return self.record(
            node_role=node_role,
            model=model,
            input_tokens=int(getattr(u, "prompt_tokens", 0) or 0) if u else 0,
            output_tokens=int(getattr(u, "completion_tokens", 0) or 0) if u else 0,
            thread_id=thread_id,
        )

    # -- accounting -----------------------------------------------------
    def _iter_records(self, thread_id: Optional[str] = None):
        try:
            st = os.lstat(self.usage_log)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                return
            if st.st_size > _MAX_BUDGET_LOG_BYTES:
                return
            fd = _open_regular_no_symlink(self.usage_log, os.O_RDONLY)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode):
                    os.close(fd)
                    return
                if opened.st_size > _MAX_BUDGET_LOG_BYTES:
                    os.close(fd)
                    return
                with os.fdopen(fd, "rb") as f:
                    raw = f.read(_MAX_BUDGET_LOG_BYTES + 1)
                if len(raw) > _MAX_BUDGET_LOG_BYTES:
                    return
            except Exception:
                try:
                    os.close(fd)
                except Exception:
                    pass
                return
            for line in raw.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if thread_id is not None:
                    rec_tid = d.get("thread_id")
                    # Records written without a thread_id (the worker/loop
                    # usage log format + the dashboard's own records) belong
                    # to the default thread — otherwise the per-thread
                    # filter would exclude ALL real usage and the gate would
                    # always see spent=0 (deny forever).
                    if rec_tid is None:
                        if thread_id != "default":
                            continue
                    elif rec_tid != thread_id:
                        continue
                yield d
        except Exception:
            return

    def spent_tokens(self, thread_id: str = "default") -> int:
        total = 0
        for d in self._iter_records(thread_id):
            total += int(d.get("input_tokens", 0) or 0)
            total += int(d.get("output_tokens", 0) or 0)
        return total

    def spent_cost_estimate(self, thread_id: str = "default") -> float:
        rates = self._budget["rates_per_1k_tokens"]
        total = 0.0
        for d in self._iter_records(thread_id):
            rec = UsageRecord(
                ts=d.get("ts", 0.0),
                thread_id=d.get("thread_id", thread_id),
                node_role=d.get("node_role", "cloud_specialist"),
                model=d.get("model", ""),
                input_tokens=int(d.get("input_tokens", 0) or 0),
                output_tokens=int(d.get("output_tokens", 0) or 0),
            )
            total += rec.cost_estimate(rates)
        return round(total, 6)

    # Model-name fragments that mark a record as a CLOUD call when the record
    # has no explicit node_role (the bare worker/loop log format omits it).
    _CLOUD_MODEL_HINTS = (
        "glm", "deepseek", "minimax", "gpt-", "gpt4", "gpt-4", "claude",
        "fireworks", "openai", "o1", "o3", "mistral", "gemini",
    )

    def _is_local_record(self, d: dict) -> bool:
        """Best-effort: is this usage record a cheap local (control/worker)
        call? Explicit node_role wins; otherwise infer from the model name
        (bare records from the worker loop have no node_role and run on local
        Ollama models)."""
        role = d.get("node_role")
        if role in ("control_loop", "worker_loop", "local"):
            return True
        if role in ("cloud_specialist", "adjudicator", "cloud"):
            return False
        model = str(d.get("model", "")).lower()
        return not any(h in model for h in self._CLOUD_MODEL_HINTS)

    def local_iterations(self, thread_id: str = "default") -> int:
        """Count cheap (control/worker) calls logged for this thread. Records
        without an explicit node_role are classified by model name so the real
        agent usage log (which omits node_role) is counted correctly."""
        n = 0
        for d in self._iter_records(thread_id):
            if self._is_local_record(d):
                n += 1
        return n

    # -- the decision ---------------------------------------------------
    def should_escalate(
        self,
        thread_id: str = "default",
        subproblem_is_hard: bool = True,
    ) -> EscalationDecision:
        """Decide whether escalation to a cloud specialist is permitted.

        Policy v1:
          * Below `min_local_iterations_before_escalation` cheap loops →
            deny (iterate cheaply first).
          * At/over the hard token ceiling → deny (budget exhausted).
          * Below the soft threshold → allow (escalation is cheap relative
            to the budget).
          * Between soft and hard → allow only if the caller marks the
            subproblem hard.

        The returned decision is auditable and (optionally) logged.
        """
        ceiling = int(self._budget["thread_token_ceiling"])
        soft = int(ceiling * float(self._budget["escalation_soft_fraction"]))
        min_local = int(self._budget["min_local_iterations_before_escalation"])

        spent = self.spent_tokens(thread_id)
        local_iters = self.local_iterations(thread_id)

        def decide(allowed, reason):
            d = EscalationDecision(
                allowed=allowed,
                reason=reason,
                thread_id=thread_id,
                spent_tokens=spent,
                ceiling_tokens=ceiling,
                soft_threshold_tokens=soft,
                local_iterations=local_iters,
            )
            self._maybe_log_decision(d)
            return d

        # MeTTa-FIRST: the policy lives in src/escalation.metta (Atomspace rules).
        # We evaluate it against the live facts; the verdict decides allow/deny.
        # The reason is tagged [metta] so the audit trail shows the decision came
        # from the symbolic policy, not from these Python lines. If the MeTTa
        # runtime/policy is unavailable, `verdict` is None and we fall through to
        # the equivalent Python rules below (identical behavior, never raises).
        mv = self._policy.verdict(
            spent=spent,
            ceiling=ceiling,
            soft=soft,
            min_local=min_local,
            iters=local_iters,
            hard=subproblem_is_hard,
        )
        if mv is not None:
            allowed, m_reason = mv
            return decide(
                allowed,
                f"[metta] {m_reason} "
                f"(spent={spent}, soft={soft}, ceiling={ceiling}, "
                f"local_iters={local_iters}/{min_local}, "
                f"hard={subproblem_is_hard})",
            )

        # ---- Python fallback (used only when MeTTa is unavailable) ----------
        if local_iters < min_local:
            return decide(
                False,
                f"iterate cheaply first: {local_iters}/{min_local} local "
                "iterations before escalation is allowed",
            )
        if spent >= ceiling:
            return decide(
                False,
                f"budget exhausted: {spent} >= ceiling {ceiling} tokens; "
                "finish on cheap nodes or stop",
            )
        if spent < soft:
            return decide(
                True,
                f"under soft threshold ({spent} < {soft}); escalation freely "
                "permitted",
            )
        if subproblem_is_hard:
            return decide(
                True,
                f"between soft ({soft}) and hard ({ceiling}); escalating "
                "because subproblem flagged hard",
            )
        return decide(
            False,
            f"between soft ({soft}) and hard ({ceiling}); subproblem not "
            "hard enough to justify spend",
        )

    def _maybe_log_decision(self, d: EscalationDecision) -> None:
        if not self._record_decisions:
            return
        try:
            _ensure_real_directory(os.path.dirname(self.escalation_log))
            _reject_unsafe_existing_file(self.escalation_log, "escalation log")
            fd = _open_regular_no_symlink(
                self.escalation_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND
            )
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(d)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            _fsync_parent_dir(self.escalation_log)
        except Exception:
            pass

    # -- summary --------------------------------------------------------
    def summary(self, thread_id: str = "default") -> dict:
        """A compact, human/audit-readable snapshot for the dashboard."""
        ceiling = int(self._budget["thread_token_ceiling"])
        spent = self.spent_tokens(thread_id)
        return {
            "thread_id": thread_id,
            "spent_tokens": spent,
            "ceiling_tokens": ceiling,
            "fraction_used": round(spent / ceiling, 4) if ceiling else None,
            "estimated_cost": self.spent_cost_estimate(thread_id),
            "local_iterations": self.local_iterations(thread_id),
        }


# ----------------------------------------------------------------------
# CLI: `python src/threadkeeper_budget.py [thread_id]` prints a summary
# and the current escalation verdict. Handy for the Quickstart demo.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    tid = sys.argv[1] if len(sys.argv) > 1 else "default"
    bt = BudgetTracker()
    print("=== ThreadKeeper budget summary ===")
    print(json.dumps(bt.summary(tid), indent=2))
    verdict = bt.should_escalate(tid)
    print("=== escalation verdict ===")
    print(json.dumps(asdict(verdict), indent=2))
