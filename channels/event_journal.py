"""Step 11: Structured privacy-safe event journaling for ProtoMegaBot.

Records correlation IDs, generation/component IDs, timestamps, state
transitions, queue outcomes, send outcomes, and exit classifications.

NEVER records: message bodies, tokens, session credentials, or
secret-bearing environment values.
"""
import json
import os
import time
import threading
from pathlib import Path
from typing import Optional

_JOURNAL_LOCK = threading.Lock()
_JOURNAL_PATH: Optional[str] = None
_COMPONENT_ID = "unknown"
_GENERATION_ID = "unknown"


def configure(journal_path: str, component_id: str = "telegram", generation_id: str = "") -> None:
    """Set the journal file path and component identifiers."""
    global _JOURNAL_PATH, _COMPONENT_ID, _GENERATION_ID
    _JOURNAL_PATH = journal_path
    _COMPONENT_ID = component_id
    _GENERATION_ID = generation_id or os.environ.get("OMEGACLAW_GENERATION_ID", "unknown")
    Path(journal_path).parent.mkdir(parents=True, exist_ok=True)


def _write_event(event: dict) -> None:
    """Write one event as a JSON line to the journal."""
    if _JOURNAL_PATH is None:
        return
    event["timestamp"] = time.time()
    event["component"] = _COMPONENT_ID
    event["generation_id"] = _GENERATION_ID
    line = json.dumps(event, default=str, sort_keys=True) + "\n"
    with _JOURNAL_LOCK:
        with open(_JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(line)


def log_message_received(correlation_id: str, source_chat_id: str, update_id: int) -> None:
    _write_event({
        "event": "message_received",
        "correlation_id": correlation_id,
        "source_chat_id": source_chat_id,
        "update_id": update_id,
    })


def log_message_dequeued(correlation_id: str) -> None:
    _write_event({
        "event": "message_dequeued",
        "correlation_id": correlation_id,
    })


def log_send_attempt(correlation_id: str, target_chat_id: str, success: bool, error: str = "") -> None:
    _write_event({
        "event": "send_attempt",
        "correlation_id": correlation_id,
        "target_chat_id": target_chat_id,
        "success": success,
        "error": error,
    })


def log_queue_overflow(depth: int, max_depth: int, overflow_count: int) -> None:
    _write_event({
        "event": "queue_overflow",
        "depth": depth,
        "max_depth": max_depth,
        "overflow_count": overflow_count,
    })


def log_state_transition(from_state: str, to_state: str, reason: str = "") -> None:
    _write_event({
        "event": "state_transition",
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
    })


def log_exit(signum: Optional[int] = None, exit_code: Optional[int] = None, classification: str = "unknown") -> None:
    _write_event({
        "event": "exit",
        "signal": signum,
        "exit_code": exit_code,
        "classification": classification,
    })


def log_bridge_started(pid: int, parent_pid: int) -> None:
    _write_event({
        "event": "bridge_started",
        "pid": pid,
        "parent_pid": parent_pid,
    })


def log_bridge_authenticated(username: str, bot_id: int) -> None:
    _write_event({
        "event": "bridge_authenticated",
        "username": username,
        "bot_id": bot_id,
    })


def log_canary_result(correlation_id: str, phase: str, success: bool, detail: str = "") -> None:
    _write_event({
        "event": "canary",
        "correlation_id": correlation_id,
        "phase": phase,
        "success": success,
        "detail": detail,
    })


def classify_exit(exit_code: int, signum: Optional[int] = None) -> str:
    """Classify an exit into a named category."""
    if signum == 11:
        return "SIGSEGV"
    if signum == 9 or exit_code == 137:
        return "OOM_KILL"
    if signum == 15:
        return "SIGTERM_operator_stop"
    if exit_code == 0:
        return "normal_exit"
    if exit_code == 124:
        return "timeout"
    if exit_code < 0:
        return f"signal_{-exit_code}"
    return f"nonzero_exit_{exit_code}"


def read_journal(path: str) -> list:
    """Read all events from a journal file."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def is_secret_safe(event: dict) -> bool:
    """Check that an event dict contains no secret-bearing keys."""
    secret_keys = {"token", "api_key", "password", "secret", "credential", "session_string"}
    for k in event:
        if k.lower() in secret_keys:
            return False
        if any(s in k.lower() for s in secret_keys):
            return False
    return True
