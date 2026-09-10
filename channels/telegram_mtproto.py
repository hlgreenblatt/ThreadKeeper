"""
Telethon MTProto receive adapter for OmegaClaw Telegram — subprocess mode.

Launches a separate Python process (telegram_mtproto_bridge.py) that connects
to Telegram via Telethon/MTProto and forwards messages as JSON lines.
This avoids GIL contention with SWI-Prolog's Janus/Python bridge.
"""

import json
import os
import subprocess
import sys
import threading

_mtproto_proc = None
_mtproto_thread = None
_mtproto_running = False
_mtproto_ready = threading.Event()
_mtproto_start_error = ""

_BRIDGE_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "telegram_mtproto_bridge.py",
)


def _reader_loop():
    """Read JSON lines from the subprocess stdout and feed into telegram adapter."""
    global _mtproto_running, _mtproto_start_error

    try:
        import telegram as tg_adapter
    except ImportError:
        print("[TELEGRAM-MTPROTO] Cannot import telegram adapter module")
        return

    proc = _mtproto_proc
    if not proc:
        return

    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                update_dict = json.loads(line)
                if update_dict.get("type") == "status":
                    msg = update_dict.get("message", "")
                    print(f"[TELEGRAM-MTPROTO] {msg}")
                    if "Connected as" in msg:
                        tg_adapter._connected = True
                        _mtproto_running = True
                    elif "Event handler registered" in msg and _mtproto_running:
                        _mtproto_ready.set()
                    elif "disconnected" in msg.lower():
                        tg_adapter._connected = False
                        _mtproto_running = False
                elif update_dict.get("type") == "message":
                    msg_dict = update_dict.get("update")
                    if msg_dict:
                        tg_adapter._handle_updates([msg_dict])
                elif update_dict.get("type") == "error":
                    msg = update_dict.get("message", "")
                    if not _mtproto_running:
                        _mtproto_start_error = msg
                        _mtproto_ready.set()
                    print(f"[TELEGRAM-MTPROTO] Bridge error: {msg}")
            except (json.JSONDecodeError, KeyError) as exc:
                print(f"[TELEGRAM-MTPROTO] Parse error: {exc} on: {line[:200]}")
    except Exception as exc:
        print(f"[TELEGRAM-MTPROTO] Reader loop error: {exc}")
    finally:
        if not _mtproto_running:
            _mtproto_start_error = _mtproto_start_error or "bridge exited before readiness"
            _mtproto_ready.set()
        _mtproto_running = False
        try:
            tg_adapter._connected = False
        except Exception:
            pass
        print("[TELEGRAM-MTPROTO] Reader loop ended")


def start_mtproto():
    """Start the Telethon MTProto bridge subprocess."""
    global _mtproto_proc, _mtproto_thread, _mtproto_running, _mtproto_start_error

    # Load env file
    env_file = os.environ.get(
        "OMEGACLAW_TELEGRAM_ENV",
        "/home/openclaw/.openclaw/omegaclaw-telegram.env",
    )
    env = os.environ.copy()
    if os.path.isfile(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

    api_id = env.get("TELEGRAM_API_ID", "").strip()
    api_hash = env.get("TELEGRAM_API_HASH", "").strip()
    bot_token = (env.get("TG_BOT_TOKEN") or env.get("OMEGACLAW_TG_BOT_TOKEN") or "").strip()

    if not api_id or not api_hash:
        print("[TELEGRAM-MTPROTO] Missing TELEGRAM_API_ID or TELEGRAM_API_HASH; falling back to Bot API")
        return None
    if not bot_token:
        print("[TELEGRAM-MTPROTO] Missing bot token; cannot start")
        return None

    if _mtproto_proc is not None and _mtproto_proc.poll() is None:
        raise RuntimeError(f"MTProto bridge is already running as pid {_mtproto_proc.pid}")

    _mtproto_ready.clear()
    _mtproto_start_error = ""
    _mtproto_running = False

    python_bin = env.get(
        "OPENCLAW_SUBPROCESS_PYTHON",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            ".venv", "bin", "python",
        ),
    )
    if not os.path.isfile(python_bin):
        python_bin = sys.executable

    # Create FIFO for backup IPC
    fifo_path = env.get("TG_MTPROTO_FIFO", "/tmp/protomega_mtproto_fifo")
    try:
        os.mkfifo(fifo_path)
    except FileExistsError:
        pass

    stderr_path = "/home/openclaw/research-agent/projects/omegaclaw/artifacts/telegram-private-supervisor/mtproto_bridge_stderr.log"
    os.makedirs(os.path.dirname(stderr_path), exist_ok=True)

    env["TG_MTPROTO_PARENT_PID"] = str(os.getpid())
    print(f"[TELEGRAM-MTPROTO] Starting owned bridge: {python_bin} -u {_BRIDGE_SCRIPT}")

    # Keep the bridge in the service's process group/cgroup.  The bridge also
    # arms Linux PR_SET_PDEATHSIG and checks the expected parent PID, so a
    # fatal SWI/Janus exit cannot leave a detached Telegram consumer behind.
    try:
        stderr_handle = open(stderr_path, "a")
        _mtproto_proc = subprocess.Popen(
            [python_bin, "-u", _BRIDGE_SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_handle,
            env=env,
            text=True,
            bufsize=1,
            close_fds=True,
        )
        stderr_handle.close()
    except Exception as exc:
        print(f"[TELEGRAM-MTPROTO] Failed to start subprocess: {exc}")
        return None

    _mtproto_thread = threading.Thread(target=_reader_loop, daemon=True)
    _mtproto_thread.start()

    try:
        startup_timeout = max(0.1, float(env.get("TG_MTPROTO_START_TIMEOUT", "15")))
    except ValueError:
        startup_timeout = 15.0
    if not _mtproto_ready.wait(startup_timeout) or not _mtproto_running:
        error = _mtproto_start_error or f"bridge did not become ready within {startup_timeout:g}s"
        stop_mtproto()
        raise RuntimeError(error)

    return _mtproto_thread


def stop_mtproto():
    """Stop the Telethon MTProto bridge subprocess."""
    global _mtproto_proc, _mtproto_running

    _mtproto_running = False
    if _mtproto_proc:
        try:
            _mtproto_proc.terminate()
            _mtproto_proc.wait(timeout=5)
        except Exception:
            try:
                _mtproto_proc.kill()
            except Exception:
                pass
        _mtproto_proc = None


def mtproto_status():
    """Return process/connection state without claiming end-to-end health."""
    proc = _mtproto_proc
    return {
        "process_running": bool(proc is not None and proc.poll() is None),
        "connected": bool(_mtproto_running),
        "pid": proc.pid if proc is not None and proc.poll() is None else None,
    }
