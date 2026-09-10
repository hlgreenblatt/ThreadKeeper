"""Step 12: Native crash instrumentation for ProtoMegaBot.

Enables controlled core-dump capture, records exact runtime versions,
captures backtraces for signal 11, and classifies OOM/SIGKILL exits.

Usage:
  instrument.enable_core_dumps(limit_mb=100)
  instrument.record_runtime_versions()
  instrument.capture_crash(pid, signum, exit_code)
"""
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_CORE_DIR = os.path.expanduser(
    os.environ.get("OMEGACLAW_CORE_DIR", "~/research-agent/projects/omegaclaw/artifacts/cores")
)
DEFAULT_VERSIONS_PATH = os.path.expanduser(
    os.environ.get(
        "OMEGACLAW_VERSIONS_PATH",
        "~/research-agent/projects/omegaclaw/artifacts/runtime-versions.json",
    )
)
DEFAULT_CRASH_LOG = os.path.expanduser(
    os.environ.get(
        "OMEGACLAW_CRASH_LOG",
        "~/research-agent/projects/omegaclaw/artifacts/crash-log.jsonl",
    )
)

RLIMIT_CORE = getattr(resource, "RLIMIT_CORE", None)


def enable_core_dumps(limit_mb: int = 100, core_dir: str = DEFAULT_CORE_DIR) -> dict:
    """Enable core dumps with a disk-space-bounded limit.

    Returns a dict with the applied limits and core pattern.
    """
    Path(core_dir).mkdir(parents=True, exist_ok=True)

    result = {
        "core_dir": core_dir,
        "limit_mb": limit_mb,
        "applied": False,
        "rlimit": None,
        "core_pattern": None,
    }

    # Set RLIMIT_CORE
    if RLIMIT_CORE is not None:
        soft, hard = resource.getrlimit(RLIMIT_CORE)
        new_limit = limit_mb * 1024 * 1024
        try:
            resource.setrlimit(RLIMIT_CORE, (new_limit, new_limit))
            result["applied"] = True
            result["rlimit"] = {"soft": new_limit, "hard": new_limit}
        except (ValueError, PermissionError):
            result["rlimit"] = {"soft": soft, "hard": hard}

    # Try to set core pattern (requires root, may fail gracefully)
    try:
        pattern_path = "/proc/sys/kernel/core_pattern"
        if os.path.exists(pattern_path) and os.access(pattern_path, os.W_OK):
            with open(pattern_path, "w") as f:
                f.write(f"{core_dir}/core.%p.%t\n")
            with open(pattern_path, "r") as f:
                result["core_pattern"] = f.read().strip()
    except PermissionError:
        result["core_pattern"] = "permission denied (expected without root)"

    return result


def record_runtime_versions(path: str = DEFAULT_VERSIONS_PATH) -> dict:
    """Record exact versions of all relevant runtime components."""
    versions = {
        "timestamp": time.time(),
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "kernel": {
            "release": platform.release(),
            "version": platform.version(),
        },
    }

    # SWI-Prolog
    try:
        r = subprocess.run(
            ["swipl", "--version"], capture_output=True, text=True, timeout=5
        )
        versions["swipl"] = r.stdout.strip()
    except Exception as e:
        versions["swipl"] = f"error: {e}"

    # Telethon
    try:
        import telethon
        versions["telethon"] = telethon.__version__
    except Exception:
        versions["telethon"] = "not installed"

    # Janus (SWI-Prolog Python interface)
    try:
        import janus_swi
        versions["janus_swi"] = "installed"
    except Exception:
        versions["janus_swi"] = "not installed"

    # Numpy (common native extension)
    try:
        import numpy
        versions["numpy"] = numpy.__version__
    except Exception:
        versions["numpy"] = "not installed"

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(versions, f, indent=2, default=str)

    return versions


def capture_backtrace(pid: int) -> Optional[str]:
    """Capture all-thread native backtrace for a process.

    Uses gdb if available, falls back to /proc/<pid>/stack.
    """
    # Try gdb
    try:
        r = subprocess.run(
            [
                "gdb", "--batch", "--nx",
                "-ex", "thread apply all bt full",
                "-p", str(pid),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: /proc/<pid>/stack
    try:
        with open(f"/proc/{pid}/stack") as f:
            return f.read()
    except Exception:
        pass

    return None


def check_oom_evidence(pid: int) -> Optional[dict]:
    """Check cgroup/kernel OOM evidence for a process."""
    evidence = {}

    # Check dmesg for OOM killer
    try:
        r = subprocess.run(
            ["dmesg"], capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if "Out of memory" in line or "Killed process" in line:
                    if str(pid) in line:
                        evidence["dmesg_oom"] = line
                        break
    except Exception:
        pass

    # Check cgroup memory events
    cgroup_paths = [
        f"/proc/{pid}/cgroup",
    ]
    for cgp in cgroup_paths:
        try:
            with open(cgp) as f:
                evidence["cgroup"] = f.read().strip()
        except Exception:
            pass

    return evidence if evidence else None


def capture_crash(
    pid: int,
    signum: Optional[int] = None,
    exit_code: Optional[int] = None,
    crash_log: str = DEFAULT_CRASH_LOG,
) -> dict:
    """Capture and classify a crash event."""
    from event_journal import classify_exit

    classification = classify_exit(exit_code or 0, signum)
    record = {
        "timestamp": time.time(),
        "pid": pid,
        "signal": signum,
        "exit_code": exit_code,
        "classification": classification,
        "backtrace": None,
        "oom_evidence": None,
    }

    # Capture backtrace for SIGSEGV
    if signum == 11:
        record["backtrace"] = capture_backtrace(pid)

    # Check OOM evidence for SIGKILL/exit 137
    if signum == 9 or exit_code == 137:
        record["oom_evidence"] = check_oom_evidence(pid)

    # Write to crash log
    Path(crash_log).parent.mkdir(parents=True, exist_ok=True)
    with open(crash_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")

    return record


def get_loaded_libraries(pid: int) -> list:
    """Get list of loaded shared libraries for a process."""
    try:
        with open(f"/proc/{pid}/maps") as f:
            libs = set()
            for line in f:
                if ".so" in line:
                    parts = line.split()
                    if len(parts) >= 6:
                        libs.add(parts[-1])
            return sorted(libs)
    except Exception:
        return []
