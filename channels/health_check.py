"""Step 10: Liveness, readiness, and end-to-end health for ProtoMegaBot.

Three distinct health layers:

  Liveness: the authoritative owner process exists and is not restart-looping.
  Readiness: SWI initialized; exactly one owned bridge authenticated with
             handler installed; ingress consumer connected; outbound
             provider/send probes pass.
  End-to-end health: a bounded synthetic canary traverses ingress, queue,
                     stub/provider, and source-chat send.

A bridge PID or stale last-message timestamp alone is NOT health.
"""
import os
import subprocess
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class LivenessResult:
    alive: bool
    reason: str
    owner_pid: Optional[int] = None
    restart_count: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ReadinessResult:
    ready: bool
    reason: str
    swi_initialized: bool = False
    bridge_authenticated: bool = False
    bridge_handler_installed: bool = False
    ingress_connected: bool = False
    outbound_probe: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class HealthResult:
    healthy: bool
    reason: str
    liveness: Optional[LivenessResult] = None
    readiness: Optional[ReadinessResult] = None
    e2e_canary: bool = False
    timestamp: float = field(default_factory=time.time)


# --- Restart loop detection ---
_restart_timestamps: list = []
_restart_lock = threading.Lock()
RESTART_LOOP_WINDOW = 60.0  # seconds
RESTART_LOOP_THRESHOLD = 5  # restarts within window


def record_restart(timestamp: Optional[float] = None) -> None:
    """Call on every supervisor restart to track restart frequency."""
    ts = timestamp or time.time()
    with _restart_lock:
        _restart_timestamps.append(ts)
        # Prune old entries
        cutoff = ts - RESTART_LOOP_WINDOW
        while _restart_timestamps and _restart_timestamps[0] < cutoff:
            _restart_timestamps.pop(0)


def _check_restart_loop() -> tuple[bool, int]:
    with _restart_lock:
        now = time.time()
        cutoff = now - RESTART_LOOP_WINDOW
        recent = [t for t in _restart_timestamps if t >= cutoff]
        return len(recent) >= RESTART_LOOP_THRESHOLD, len(recent)


def check_liveness(owner_pid: Optional[int] = None) -> LivenessResult:
    """Check if the authoritative owner exists and is not restart-looping."""
    is_looping, restart_count = _check_restart_loop()

    if is_looping:
        return LivenessResult(
            alive=False,
            reason=f"restart loop: {restart_count} restarts in {RESTART_LOOP_WINDOW}s",
            restart_count=restart_count,
        )

    if owner_pid is None or owner_pid <= 1:
        return LivenessResult(
            alive=False,
            reason="no owner PID provided",
        )

    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return LivenessResult(
            alive=False,
            reason=f"owner PID {owner_pid} does not exist",
            owner_pid=owner_pid,
        )
    except PermissionError:
        # Process exists but we can't signal it — still alive
        pass

    return LivenessResult(
        alive=True,
        reason="owner exists and not restart-looping",
        owner_pid=owner_pid,
        restart_count=restart_count,
    )


def check_readiness(
    swi_initialized: bool,
    bridge_authenticated: bool,
    bridge_handler_installed: bool,
    ingress_connected: bool,
    outbound_probe: bool,
) -> ReadinessResult:
    """Check all readiness conditions."""
    conditions = {
        "swi_initialized": swi_initialized,
        "bridge_authenticated": bridge_authenticated,
        "bridge_handler_installed": bridge_handler_installed,
        "ingress_connected": ingress_connected,
        "outbound_probe": outbound_probe,
    }
    failed = [k for k, v in conditions.items() if not v]
    ready = not failed
    reason = "all readiness conditions met" if ready else f"failed: {', '.join(failed)}"
    return ReadinessResult(
        ready=ready,
        reason=reason,
        swi_initialized=swi_initialized,
        bridge_authenticated=bridge_authenticated,
        bridge_handler_installed=bridge_handler_installed,
        ingress_connected=ingress_connected,
        outbound_probe=outbound_probe,
    )


def check_e2e_health(
    canary_sent: bool,
    canary_received: bool,
    canary_replied: bool,
    correct_chat: bool,
) -> tuple[bool, str]:
    """Verify a synthetic canary traversed the full path."""
    if not canary_sent:
        return False, "canary was not sent"
    if not canary_received:
        return False, "canary was not received by bridge"
    if not canary_replied:
        return False, "canary did not produce a reply"
    if not correct_chat:
        return False, "canary reply went to wrong chat"
    return True, "canary completed end-to-end"


def check_health(
    owner_pid: Optional[int],
    swi_initialized: bool,
    bridge_authenticated: bool,
    bridge_handler_installed: bool,
    ingress_connected: bool,
    outbound_probe: bool,
    e2e_canary: bool = False,
) -> HealthResult:
    """Full health check: liveness ∧ readiness ∧ (optionally) e2e."""
    liveness = check_liveness(owner_pid)
    if not liveness.alive:
        return HealthResult(
            healthy=False,
            reason=f"liveness failed: {liveness.reason}",
            liveness=liveness,
        )

    readiness = check_readiness(
        swi_initialized,
        bridge_authenticated,
        bridge_handler_installed,
        ingress_connected,
        outbound_probe,
    )
    if not readiness.ready:
        return HealthResult(
            healthy=False,
            reason=f"readiness failed: {readiness.reason}",
            liveness=liveness,
            readiness=readiness,
        )

    if not e2e_canary:
        return HealthResult(
            healthy=False,
            reason="liveness and readiness OK but end-to-end canary not proven",
            liveness=liveness,
            readiness=readiness,
            e2e_canary=False,
        )

    return HealthResult(
        healthy=True,
        reason="liveness, readiness, and e2e canary all pass",
        liveness=liveness,
        readiness=readiness,
        e2e_canary=True,
    )


def parse_supervisor_status(status_text: str) -> dict:
    """Parse the supervisor status output into a dict."""
    result = {}
    for line in status_text.strip().splitlines():
        line = line.strip()
        if line.startswith("owner-active pid "):
            result["owner_pid"] = int(line.split("pid ")[1])
        elif line.startswith("topology "):
            parts = line[len("topology "):].split()
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    result[k] = int(v) if v.isdigit() else v
        elif line.startswith("readiness="):
            result["readiness"] = line[len("readiness="):]
    return result
