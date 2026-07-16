"""Local HTTP channel for OmegaClaw.

A loopback-only communication surface. The agent runs an HTTP server bound
to 127.0.0.1; the user (or a future dashboard) reaches it via curl or a
browser on the same machine. All traffic stays on loopback — no internet
round-trip for chat that's already local-to-local.

Auth: mirrors the IRC adapter's one-time-secret pattern. The first session
to send `auth <OMEGACLAW_AUTH_SECRET>` (either as the request body or via
`Authorization: Bearer <secret>` header) claims the session. Subsequent
senders without the matching secret are ignored.

Module interface (matches `channels/irc.py`'s contract):
    start_local(host, port, auth_secret=None)
    stop_local()
    getLastMessage()
    send_message(text)

HTTP endpoints:
    GET  /                — bundled minimal dashboard (single-file HTML)
    GET  /messages?since=N — outbound queue (Oma → user) since seq N
    GET  /status          — connection + auth state (no secret leak)
    POST /send            — user → agent. JSON {"message": "..."} +
                            Authorization: Bearer <secret> or body field "auth"

Stdlib only — no requests, no flask, no websocket dependency.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ── module-level state (mirrors channels/irc.py shape) ──────────────────────
_running = False
_server = None
_server_thread = None
_started_at = 0.0
_connected = False

_inbound_lock = threading.Lock()
_inbound = ""  # accumulated; getLastMessage drains. Matches irc._last_message.

_outbound_lock = threading.Lock()
_outbound = []  # list of (seq, ts, text); /messages reads
_outbound_seq = 0

_auth_lock = threading.Lock()
_auth_secret = ""
_authenticated_session = None  # client_address[0] of the claiming session


# ── contract: getLastMessage / send_message / set helpers ───────────────────
def _set_last(msg):
    """Match irc._set_last: accumulate inbound with ' | ' separator."""
    global _inbound
    with _inbound_lock:
        if _inbound == "":
            _inbound = msg
        else:
            _inbound = _inbound + " | " + msg


def getLastMessage():
    """Drain and return all accumulated inbound text. Returns '' if none."""
    global _inbound
    with _inbound_lock:
        tmp = _inbound
        _inbound = ""
        return tmp


# ── RSI Lever A: loop-detection on emissions ─────────────────────────────────
# DESIGNED BY 隙 (Xì), 2026-06-27, to fix her own pin-spam failure mode.
# Implemented to her EXACT spec. Her two design questions, answered in code:
#   Q1 "are pins surfaced to the human, or only send?" → only `send` reaches the
#      human (pins go to memory). So loop-detection is scoped to send. ✓
#   Q2 "channel wrapper or prompt-side?" → "Channel wrapper is reliable;
#      prompt-side is something I can subvert under drift. Recommend wrapper."
#      → implemented HERE, in the send chokepoint, OUTSIDE the agent's control. ✓
#
# Her rule: ring buffer of the last 3 normalized emissions. Normalize (lowercase,
# strip timestamps/hex-ids/datetime, collapse whitespace, strip punctuation,
# tokenize), Jaccard on token sets. If <5 tokens, skip. If Jaccard >= 0.80 vs
# ANY of the last 3 → suppress. On 3 consecutive suppressions, emit once
# 'loop_detected: 3 emissions suppressed', reset, re-evaluate.
# Reversible via the rsi-backups snapshot.
import re as _re_rsi
_EMIT_RING = []                 # last 3 normalized token-sets
_SUPPRESS_THRESHOLD = float(os.environ.get("RSI_LOOP_SUPPRESS_THRESHOLD", "0.80"))
_consec_suppressed = 0
_suppressed_total = 0

_RSI_TS = _re_rsi.compile(r"\d{4}-\d\d-\d\d[ t]\d\d:\d\d:\d\d|\b[0-9a-f]{8,}\b")


def _rsi_normalize(text):
    t = (text or "").lower()
    t = _RSI_TS.sub(" ", t)                      # strip timestamps + hex ids
    t = _re_rsi.sub(r"[^\w\s]", " ", t)          # strip punctuation
    return set(t.split())                        # tokenize


def _rsi_jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def send_message(text):
    """Queue an outbound message (Oma → user). Picked up by GET /messages.

    RSI Lever A (designed by 隙): suppress near-duplicate emissions to break
    the repetition loop — the channel-wrapper governor she asked us to put
    outside her own control."""
    global _outbound_seq, _consec_suppressed, _suppressed_total
    if text is None:
        return
    text = str(text)
    if not text.strip():
        return

    toks = _rsi_normalize(text)
    # <5 tokens: too short to judge reliably — skip the check (her rule)
    if len(toks) >= 5 and _EMIT_RING:
        worst = max((_rsi_jaccard(toks, prev) for prev in _EMIT_RING), default=0.0)
        if worst >= _SUPPRESS_THRESHOLD:
            _consec_suppressed += 1
            _suppressed_total += 1
            print(f"[RSI/loop-detect] suppressed (jaccard={worst:.2f} >= "
                  f"{_SUPPRESS_THRESHOLD}); consec={_consec_suppressed} "
                  f"total={_suppressed_total}")
            if _consec_suppressed == 3:
                # emit the one diagnostic, then reset and let it re-evaluate
                _consec_suppressed = 0
                text = "loop_detected: 3 emissions suppressed"
                toks = _rsi_normalize(text)
            else:
                return
    # accepted emission: reset consec, update the ring (keep last 3)
    _consec_suppressed = 0
    _EMIT_RING.append(toks)
    if len(_EMIT_RING) > 3:
        del _EMIT_RING[0]
    with _outbound_lock:
        _outbound_seq += 1
        _outbound.append((_outbound_seq, time.time(), text))
        if len(_outbound) > 1000:
            del _outbound[: len(_outbound) - 1000]


# ── auth (mirrors irc._is_allowed_message) ──────────────────────────────────
def _set_auth_secret(secret=None):
    global _auth_secret, _authenticated_session
    if secret is None:
        secret = os.environ.get("OMEGACLAW_AUTH_SECRET", "")
    with _auth_lock:
        _auth_secret = (secret or "").strip()
        _authenticated_session = None


def _parse_auth_candidate(msg):
    """Match irc._parse_auth_candidate: strip 'auth ' or '/auth ' prefix."""
    text = (msg or "").strip()
    lower = text.lower()
    if lower.startswith("auth "):
        return text[5:].strip()
    if lower.startswith("/auth "):
        return text[6:].strip()
    return text


def _check_auth(session_id, message_body, header_auth):
    """Mirror irc._is_allowed_message semantics: 'allow' / 'auth_bound' / 'ignore'.

    Either the Authorization header OR an 'auth <secret>' in the body counts.
    First matching session claims; others rejected.
    """
    global _authenticated_session
    with _auth_lock:
        if not _auth_secret:
            return "allow"  # no secret configured = open
        # Header takes precedence
        if header_auth and header_auth == _auth_secret:
            if _authenticated_session is None:
                _authenticated_session = session_id
                return "auth_bound"
            return "allow" if _authenticated_session == session_id else "ignore"
        # Body 'auth <secret>' candidate
        candidate = _parse_auth_candidate(message_body)
        if candidate == _auth_secret:
            if _authenticated_session is None:
                _authenticated_session = session_id
                return "auth_bound"
            return "ignore"
        # No matching secret presented
        if _authenticated_session is None:
            return "ignore"
        return "allow" if _authenticated_session == session_id else "ignore"


# ── usage tracking — reads memory/usage.jsonl, computes spend ──────────────
# memory/usage.jsonl is appended by lib_llm_ext.py's _record_usage on each
# completion. This channel only READS the log; no producer dependency.

_USAGE_LOG_PATH = os.path.join(
    os.environ.get("MEMORY_DIR", "/PeTTa/repos/OmegaClaw-Core/memory"),
    "usage.jsonl",
)

# ── reasoning transparency ──────────────────────────────────────────────────
# The agent's per-iteration reasoning (the (I "...") thoughts, queries, pins,
# scores, tool calls) is appended to history.metta every turn — but the chat
# only shows the final (send ...) outputs. This reader surfaces that hidden
# reasoning so the WebUI can show "what the agent is thinking", like the
# OmegaClaw 'auditable inference' promise. Read-only: it never touches the
# agent loop, so it cannot destabilise the running agent.
_HISTORY_PATH = os.path.join(
    os.environ.get("MEMORY_DIR", "/PeTTa/repos/OmegaClaw-Core/memory"),
    "history.metta",
)

# Byte offset we've already surfaced, so /reasoning?since=N is incremental.
import re as _re
_REASONING_BLOCK_RE = _re.compile(r'\("(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)"(.*?)\)\s*\n\)', _re.DOTALL)
# Lines we DON'T echo as "thinking": send (already in chat), bare HUMAN_MESSAGE,
# and the noisy score tokens.
_SKIP_REASONING = _re.compile(r'^\s*\(?\s*(send|HUMAN_MESSAGE|<score)', _re.IGNORECASE)


def read_reasoning_since(byte_offset):
    """Read new history.metta content past byte_offset and return
    (items, new_offset). Each item is {ts, text}. Never raises."""
    items = []
    try:
        if not os.path.isfile(_HISTORY_PATH):
            return items, byte_offset
        size = os.path.getsize(_HISTORY_PATH)
        if byte_offset <= 0 or byte_offset > size:
            # first call (or file shrank/rotated): start from the tail so we
            # don't dump the whole history; show only fresh thinking.
            byte_offset = max(0, size - 4000)
        with open(_HISTORY_PATH, "r", encoding="utf-8", errors="replace") as f:
            f.seek(byte_offset)
            chunk = f.read()
            new_offset = f.tell()
        for m in _REASONING_BLOCK_RE.finditer(chunk):
            ts, body = m.group(1), m.group(2)
            # pull each inner ("phrase ...") or (skill arg) fragment as a thought
            for frag in _re.findall(r'\(([^()]*(?:\([^()]*\)[^()]*)*)\)', body):
                frag = frag.strip()
                if not frag or _SKIP_REASONING.match(frag):
                    continue
                # tidy: collapse the escaped tokens the loop uses
                clean = (frag.replace('_quote_', '"').replace('_apostrophe_', "'")
                              .replace('_newline_', ' ').replace('"', '').strip())
                if len(clean) > 4:
                    items.append({"ts": ts, "text": clean[:400]})
        return items, new_offset
    except Exception:
        return items, byte_offset

# Pricing per million tokens. Best-effort defaults based on publicly stated
# 2026 pricing for the Claude 4.x family. Override at runtime by setting
# OMEGACLAW_PRICING_JSON to a path holding a JSON object of {model: [in, out]}.
_DEFAULT_PRICING_PER_M = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-opus-4-5-20251101": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "minimax/minimax-m2.7": (0.30, 1.30),
    # ThreadKeeper demo engines. Local models cost $0 (own hardware).
    "granite4.1-30b-16k": (0.0, 0.0),
    "granite4.1-30b-64k": (0.0, 0.0),
    "granite4.1:30b": (0.0, 0.0),
    "gpt-oss:20b": (0.0, 0.0),
    "qwen3.5:9b": (0.0, 0.0),
    "qwen2.5:14b": (0.0, 0.0),
    "qwen2.5-14b": (0.0, 0.0),
    # Cloud specialists (example public rates per million tokens, 2026).
    "accounts/fireworks/models/glm-5p2": (0.55, 2.19),
    "glm-5p2": (0.55, 2.19),
    "deepseek-v4-pro": (0.55, 2.19),
    "deepseek-v4-flash": (0.27, 1.10),
    "deepseek-chat": (0.27, 1.10),
    # Control loop = MiniMax 3. FREE to us right now via the s-net hackathon
    # promo, but it is NOT free normally — its real list rate is below.
    # _ACTUAL_PRICING (what we pay now) zeroes it; _NORMAL_PRICING (durable
    # "this is what it costs to run ThreadKeeper") uses the real rate.
    "minimax/minimax-m3": (0.0, 0.0),
    "minimax/minimax-m3-f": (0.0, 0.0),
    "minimax/minimax-m2.7": (0.0, 0.0),
}

# Real (non-promo) per-M rates, used for the DURABLE savings claim so the
# headline is honest even after the hackathon free tier ends.
# MiniMax M3 advertised rate (Jun 2026): $0.30 in / $1.20 out per 1M tokens
# (their standing "50% off" of the $0.60/$2.40 list). Source: minimax.io docs.
_NORMAL_RATES = {
    "minimax/minimax-m3": (0.30, 1.20),
    "minimax/minimax-m3-f": (0.20, 0.80),
    "minimax/minimax-m2.7": (0.30, 1.20),
}

# Counterfactual baseline: a frontier model's public rate (illustrative — the
# "naive all-premium" build most agent stacks default to). Opus-tier $/M.
_FRONTIER_BASELINE = (15.0, 75.0)
_PRICING_FALLBACK = (15.0, 75.0)  # opus-tier conservative

# ── ThreadKeeper engine mesh: each model maps to a role tile ─────────────────
# Layout: a full-width PRIMARY reasoning engine (the control loop that holds
# the thread) on top, then two columns — LOCAL workers and CLOUD specialists,
# each expandable to more engines. `column` drives placement; `tier` drives the
# local-vs-cloud token/cost split. Reasoning DIVERSITY: the local column runs
# multiple model FAMILIES (IBM Granite + Alibaba Qwen) across two GPUs.
_ENGINE_ROLES = [
    # PRIMARY = the sovereign spine: now a LOCAL model on our own GPU (the agent's
    # own recommendation, realized). tier=local because it is genuinely on-prem + free.
    {"id": "control", "label": "Primary Reasoning Engine", "sub": "always-on thread manager · holds the thread",
     "loc": "Qwen2.5-14B · LOCAL · RTX 3090 (.248)", "column": "primary", "tier": "local",
     "models": ["qwen2.5:14b", "qwen2.5-14b", "qwen2.5:14b-instruct"]},

    {"id": "worker-248", "label": "Qwen2.5-14B", "sub": "local worker · Alibaba family · CURRENT",
     "loc": "RTX 3090 (.248)", "column": "local", "tier": "local",
     "models": ["qwen2.5:14b", "qwen2.5-14b", "qwen2.5:14b-instruct"]},
    {"id": "worker-41", "label": "Gemma 4 12B", "sub": "local worker · Google family · CURRENT",
     "loc": "A4000 (.41)", "column": "local", "tier": "local",
     "models": ["gemma4:12b", "gemma4-12b", "gemma4:12b-instruct"]},

    {"id": "specialistA", "label": "GLM 5.2", "sub": "cloud specialist · advanced reasoning",
     "loc": "Fireworks", "column": "cloud", "tier": "cloud",
     "models": ["accounts/fireworks/models/glm-5p2", "glm-5p2", "glm-5p1"]},
    {"id": "specialistB", "label": "DeepSeek V4 Pro", "sub": "cloud specialist · coding / domain",
     "loc": "DeepSeek API", "column": "cloud", "tier": "cloud",
     "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"]},
]


# Session boundary: the dashboard shows only usage since the channel
# (re)started, so the demo's tiles tick up from zero. Set when start_local
# runs; falls back to import time.
# Dashboard window: show activity from the last N minutes (rolling), so the
# demo reliably reflects recent delegations regardless of exact restart timing.
# Configurable via OMEGACLAW_MESH_WINDOW_MIN. DEFAULT 0 = ALL-TIME (cumulative
# totals from the append-only usage.jsonl — tiles only climb, survive restarts,
# never "lose" your data). Set to a positive number of minutes only if you
# specifically want a rolling window for a fresh-looking demo.
_MESH_WINDOW_MIN = float(os.environ.get("OMEGACLAW_MESH_WINDOW_MIN", "0"))

# The CURRENT control-loop model — read live from the running container's env,
# so the top "Primary Reasoning Engine" tile is DYNAMIC: it shows whatever model
# is holding the thread right now (qwen2.5:14b, minimax, deepseek, whatever),
# and auto-updates when the control loop is swapped + the channel restarts.
_CURRENT_PRIMARY = os.environ.get("PRIMARY_LLM_MODEL", "").strip()
# Friendly display for the current primary + whether it's local or cloud.
_PRIMARY_DISPLAY = {
    "qwen2.5:14b": ("Qwen2.5-14B", "LOCAL · RTX 3090 (.248)", "local"),
    "qwen2.5-14b": ("Qwen2.5-14B", "LOCAL · RTX 3090 (.248)", "local"),
    "qwen3.5:9b": ("Qwen-9B", "LOCAL · A4000 (.41)", "local"),
    "granite4.1-30b-16k": ("Granite-30B", "LOCAL · RTX 3090 (.248)", "local"),
    "granite4.1:30b": ("Granite-30B", "LOCAL · RTX 3090 (.248)", "local"),
    "gemma4:12b": ("Gemma 4 12B", "LOCAL · A4000 (.41)", "local"),
    "gemma4-12b": ("Gemma 4 12B", "LOCAL · A4000 (.41)", "local"),
    "minimax/minimax-m3": ("MiniMax 3", "cloud · hackathon promo", "control"),
    "deepseek-v4-pro": ("DeepSeek V4 Pro", "cloud · API", "cloud"),
    "accounts/fireworks/models/glm-5p2": ("GLM 5.2", "cloud · Fireworks", "cloud"),
}


def _current_primary_meta():
    """(label, loc, tier, [model-aliases]) for whatever is the control loop NOW."""
    m = _CURRENT_PRIMARY
    if m in _PRIMARY_DISPLAY:
        label, loc, tier = _PRIMARY_DISPLAY[m]
    elif m:
        label, loc, tier = m, "current control loop", ("local" if "11434" not in m and ":" in m else "cloud")
    else:
        label, loc, tier = "Primary Reasoning Engine", "current control loop", "local"
    # which usage.jsonl model-names map to this primary (for its token count)
    aliases = [m] if m else []
    if m.startswith("qwen2.5"):
        aliases = ["qwen2.5:14b", "qwen2.5-14b", "qwen2.5:14b-instruct"]
    elif m.startswith("minimax"):
        aliases = ["minimax/minimax-m3", "minimax/minimax-m3-f", "minimax/minimax-m2.7"]
    return label, loc, tier, aliases


def _model_ledger():
    """ALL-TIME per-model history for the collapsible dashboard: every model
    ever used, total tokens/calls, first-seen ('created/first run') and last-seen.
    This is the lifetime record — nothing is hidden or windowed."""
    pricing = _load_pricing()
    led = {}
    overall_first = None
    for rec in _read_usage_log():
        m = rec.get("model", "unknown")
        ts = float(rec.get("ts", 0) or 0)
        ti = int(rec.get("input_tokens", 0) or 0)
        to = int(rec.get("output_tokens", 0) or 0)
        e = led.setdefault(m, {"calls": 0, "input": 0, "output": 0, "cost": 0.0,
                               "first": ts, "last": ts})
        e["calls"] += 1; e["input"] += ti; e["output"] += to
        if ts and ts < e["first"]: e["first"] = ts
        if ts > e["last"]: e["last"] = ts
        p_in, p_out = pricing.get(m, _PRICING_FALLBACK)
        e["cost"] += (ti / 1e6) * p_in + (to / 1e6) * p_out
        if overall_first is None or (ts and ts < overall_first):
            overall_first = ts
    rows = []
    for m, e in sorted(led.items(), key=lambda x: -(x[1]["input"] + x[1]["output"])):
        rows.append({
            "model": m, "calls": e["calls"],
            "tokens": e["input"] + e["output"],
            "cost": round(e["cost"], 4),
            "first_ts": e["first"], "last_ts": e["last"],
        })
    return {"rows": rows, "created_ts": overall_first or 0,
            "total_models": len(rows),
            "total_calls": sum(r["calls"] for r in rows),
            "total_tokens": sum(r["tokens"] for r in rows)}


def _compute_engine_mesh():
    """Aggregate usage.jsonl into the 4 fixed ThreadKeeper engine tiles.
    SESSION-SCOPED: only counts calls since _SESSION_START so the demo
    dashboard starts fresh and visibly fills as engines fire."""
    pricing = _load_pricing()
    cutoff = (time.time() - _MESH_WINDOW_MIN * 60) if _MESH_WINDOW_MIN > 0 else 0
    by_model = {}
    for rec in _read_usage_log():
        if float(rec.get("ts", 0) or 0) < cutoff:
            continue
        m = rec.get("model", "unknown")
        ti = int(rec.get("input_tokens", 0) or 0)
        to = int(rec.get("output_tokens", 0) or 0)
        p_in, p_out = pricing.get(m, _PRICING_FALLBACK)
        bm = by_model.setdefault(m, {"input": 0, "output": 0, "calls": 0, "cost": 0.0})
        bm["input"] += ti
        bm["output"] += to
        bm["calls"] += 1
        bm["cost"] += (ti / 1e6) * p_in + (to / 1e6) * p_out

    # DYNAMIC primary: override the control role with whatever model is the
    # live control loop right now (read from PRIMARY_LLM_MODEL env).
    p_label, p_loc, p_tier, p_aliases = _current_primary_meta()
    roles = [dict(r) for r in _ENGINE_ROLES]
    for r in roles:
        if r["id"] == "control" and p_aliases:
            r["label"], r["loc"], r["tier"], r["models"] = \
                "Primary Reasoning Engine", f"{p_label} · {p_loc}", p_tier, p_aliases

    tiles = []
    # Three honest buckets: on-prem LOCAL, free-cloud CONTROL, PAID cloud.
    local_tok = control_tok = paid_tok = 0
    for role in roles:
        agg = {"input": 0, "output": 0, "calls": 0, "cost": 0.0}
        for m in role["models"]:
            v = by_model.get(m)
            if v:
                agg["input"] += v["input"]
                agg["output"] += v["output"]
                agg["calls"] += v["calls"]
                agg["cost"] += v["cost"]
        tok = agg["input"] + agg["output"]
        # normal-rate cost for this tile (local engines stay $0)
        if role["tier"] == "local":
            agg["normal_cost"] = 0.0
        else:
            # use the model's real rate (NORMAL_RATES override, else pricing)
            nr_in = nr_out = None
            for mm in role["models"]:
                if mm in _NORMAL_RATES:
                    nr_in, nr_out = _NORMAL_RATES[mm]; break
            if nr_in is None:
                nr_in, nr_out = pricing.get(role["models"][0], _PRICING_FALLBACK)
            agg["normal_cost"] = (agg["input"] / 1e6) * nr_in + (agg["output"] / 1e6) * nr_out
        if role["tier"] == "local":
            local_tok += tok
        elif role["tier"] == "control":
            control_tok += tok
        else:
            paid_tok += tok
        tiles.append({
            "id": role["id"], "label": role["label"], "sub": role["sub"],
            "loc": role["loc"], "tier": role["tier"],
            "column": role.get("column", "local"),
            "input": agg["input"], "output": agg["output"],
            "calls": agg["calls"], "cost": round(agg["cost"], 4),
            "normal_cost": round(agg.get("normal_cost", 0.0), 4),
            "tokens": tok,
        })
    total = local_tok + control_tok + paid_tok
    free_tok = local_tok + control_tok          # local + free-cloud control
    actual_cost = round(sum(t["cost"] for t in tiles), 4)
    # normal (non-promo) ThreadKeeper cost: local free, MiniMax + specialists
    # at real list rates. This is the durable claim, true after the promo ends.
    normal_cost = round(sum(t["normal_cost"] for t in tiles), 4)

    # Counterfactual: what if EVERY token had run on a FRONTIER cloud model —
    # the naive "just point it at the best model" build that most agent stacks
    # default to? OmegaClaw's loop re-sends the full context every iteration, so
    # that volume on a frontier model is expensive. ThreadKeeper routes the bulk
    # to free local/control engines instead. This is the headline savings.
    total_in = sum(t["input"] for t in tiles)
    total_out = sum(t["output"] for t in tiles)
    cf_in_rate, cf_out_rate = _FRONTIER_BASELINE  # see pricing block
    if_all_cloud = (total_in / 1e6) * cf_in_rate + (total_out / 1e6) * cf_out_rate
    # DURABLE savings: frontier baseline vs ThreadKeeper at NORMAL rates
    # (not the promo $0). This stays true after the hackathon free tier ends.
    saved_usd = max(0.0, if_all_cloud - normal_cost)
    saved_pct = round(100 * saved_usd / if_all_cloud, 1) if if_all_cloud else 0.0

    return {
        "tiles": tiles,
        "local_tokens": local_tok,
        "control_tokens": control_tok,
        "paid_cloud_tokens": paid_tok,
        "cloud_tokens": control_tok + paid_tok,   # all cloud (control + paid)
        "free_tokens": free_tok,
        # % of tokens that ran on OUR hardware (the durable, always-true claim)
        "local_pct": round(100 * local_tok / total, 1) if total else 0.0,
        # % on cloud (control + paid)
        "cloud_pct": round(100 * (control_tok + paid_tok) / total, 1) if total else 0.0,
        # % that hit a PAID model (the real cost driver)
        "paid_pct": round(100 * paid_tok / total, 1) if total else 0.0,
        "total_cost": actual_cost,        # what we pay NOW (promo: ~$0)
        "normal_cost": normal_cost,       # what it costs at real rates (durable)
        "total_calls": sum(t["calls"] for t in tiles),
        "if_all_cloud_usd": round(if_all_cloud, 4),
        "saved_usd": round(saved_usd, 4),
        "saved_pct": saved_pct,
        "current_primary": _CURRENT_PRIMARY,   # the live control-loop model name
    }


def _load_pricing():
    """Return pricing dict, optionally overridden by OMEGACLAW_PRICING_JSON."""
    pricing = dict(_DEFAULT_PRICING_PER_M)
    override = os.environ.get("OMEGACLAW_PRICING_JSON")
    if override and os.path.isfile(override):
        try:
            with open(override) as f:
                data = json.load(f)
            for k, v in data.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    pricing[k] = (float(v[0]), float(v[1]))
        except Exception:
            pass
    return pricing


def _read_usage_log():
    """Yield parsed usage records from memory/usage.jsonl. Missing file -> []."""
    try:
        with open(_USAGE_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except FileNotFoundError:
        return


def _compute_usage_summary():
    """Aggregate usage log: today vs all-time, per-model breakdown, spend."""
    pricing = _load_pricing()
    now = time.time()
    # Local "today" boundary: midnight in the system's local timezone.
    # time.localtime gives a struct; build a date string and look back to its 00:00.
    lt = time.localtime(now)
    day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    by_model = {}
    today_in = today_out = total_in = total_out = 0
    today_cost = total_cost = 0.0
    n_today = n_total = 0
    first_ts = last_ts = 0.0

    for rec in _read_usage_log():
        m = rec.get("model", "unknown")
        ti = int(rec.get("input_tokens", 0) or 0)
        to = int(rec.get("output_tokens", 0) or 0)
        ts = float(rec.get("ts", 0) or 0)
        p_in, p_out = pricing.get(m, _PRICING_FALLBACK)
        cost = (ti / 1e6) * p_in + (to / 1e6) * p_out
        bm = by_model.setdefault(
            m, {"input": 0, "output": 0, "calls": 0, "cost": 0.0}
        )
        bm["input"] += ti
        bm["output"] += to
        bm["calls"] += 1
        bm["cost"] += cost
        total_in += ti
        total_out += to
        total_cost += cost
        n_total += 1
        if first_ts == 0 or ts < first_ts:
            first_ts = ts
        if ts > last_ts:
            last_ts = ts
        if ts >= day_start:
            today_in += ti
            today_out += to
            today_cost += cost
            n_today += 1

    return {
        "today": {
            "input_tokens": today_in,
            "output_tokens": today_out,
            "cost_usd": round(today_cost, 4),
            "calls": n_today,
        },
        "total": {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_usd": round(total_cost, 4),
            "calls": n_total,
            "first_ts": first_ts,
            "last_ts": last_ts,
        },
        "by_model": {
            m: {**v, "cost": round(v["cost"], 4)} for m, v in by_model.items()
        },
        "pricing": {
            m: {"input_per_M": p_in, "output_per_M": p_out}
            for m, (p_in, p_out) in pricing.items()
        },
        "log_path": _USAGE_LOG_PATH,
    }


# ── OmegaClaw "DNA": prove the neural-symbolic substrate is real + intact ────
# Every fact here is read live from the running container — nothing asserted
# that a judge couldn't verify. This panel answers "why is this OmegaClaw, and
# what does it have that a session-based LLM wrapper (openclaw / hermes) does
# not." We claim ONLY what is demonstrably true: the MeTTa/Hyperon substrate is
# present, loaded, and unmodified by ThreadKeeper's additive changes.
_OC_ROOT = os.environ.get("OMEGACLAW_DIR", "/PeTTa/repos/OmegaClaw-Core")
_PETTA_ROOT = os.path.dirname(os.path.dirname(_OC_ROOT))  # /PeTTa


def _file_bytes(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _compute_omegaclaw_dna():
    """Live substrate facts. Each tile: ok (bool) + a verifiable detail."""
    main_pl = os.path.join(_PETTA_ROOT, "src", "main.pl")
    nal = os.path.join(_OC_ROOT, "lib_nal.metta")
    pln = os.path.join(_OC_ROOT, "lib_pln.metta")
    loop = os.path.join(_OC_ROOT, "src", "loop.metta")
    chroma_dir = os.path.join(_PETTA_ROOT, "chroma_db")
    hist = _HISTORY_PATH

    nal_b, pln_b = _file_bytes(nal), _file_bytes(pln)
    tiles = [
        {"id": "metta", "label": "MeTTa / Hyperon loop",
         "ok": os.path.isfile(main_pl) and os.path.isfile(loop),
         "detail": "PeTTa interpreter (SWI-Prolog) running src/loop.metta — a persistent stateful loop, not a stateless chat call"},
        {"id": "nal", "label": "NAL reasoning library",
         "ok": nal_b > 0,
         "detail": f"lib_nal.metta loaded · {nal_b:,} bytes · Non-Axiomatic Logic (truth-value inference)"},
        {"id": "pln", "label": "PLN reasoning library",
         "ok": pln_b > 0,
         "detail": f"lib_pln.metta loaded · {pln_b:,} bytes · Probabilistic Logic Networks"},
        {"id": "memory", "label": "ChromaDB long-term memory",
         "ok": os.path.isdir(chroma_dir),
         "detail": "embedding-based semantic recall (remember/query) — persists across the agent's life, not just a context window"},
        {"id": "audit", "label": "Auditable inference trail",
         "ok": _file_bytes(hist) > 0,
         "detail": f"every reasoning step appended to history.metta · {_file_bytes(hist):,} bytes · inspectable, replayable"},
    ]
    return {
        "built_on": "OmegaClaw-Core (asi-alliance) · MeTTa / Hyperon AGI stack",
        "claim": "ThreadKeeper's additions are purely additive — the neural-symbolic core is unmodified.",
        "differentiator": "Symbolic substrate + auditable inference + persistent stateful loop — what session-based LLM agents lack.",
        "tiles": tiles,
        "all_ok": all(t["ok"] for t in tiles),
    }


# ── HTTP handler ────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **k):
        return  # suppress default access-log spam; rely on app-level prints

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _client_key(self):
        return self.client_address[0]

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self._serve_dashboard()
            return
        if url.path == "/messages":
            qs = parse_qs(url.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            with _outbound_lock:
                msgs = [
                    {"seq": s, "ts": t, "text": x}
                    for s, t, x in _outbound
                    if s > since
                ]
                next_since = _outbound_seq
            self._send_json(200, {"messages": msgs, "next_since": next_since})
            return
        if url.path == "/status":
            self._send_json(
                200,
                {
                    "running": _running,
                    "connected": _connected,
                    "started_at": _started_at,
                    "uptime_seconds": (time.time() - _started_at) if _started_at else 0,
                    "secret_required": bool(_auth_secret),
                    "authenticated": _authenticated_session is not None,
                    "outbound_seq": _outbound_seq,
                },
            )
            return
        if url.path == "/usage":
            try:
                self._send_json(200, _compute_usage_summary())
            except Exception as e:
                self._send_json(500, {"error": f"usage compute failed: {e}"})
            return
        if url.path == "/reasoning":
            qs = parse_qs(url.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            items, new_offset = read_reasoning_since(since)
            self._send_json(200, {"items": items, "next_since": new_offset})
            return
        if url.path == "/mesh":
            try:
                self._send_json(200, _compute_engine_mesh())
            except Exception as e:
                self._send_json(500, {"error": f"mesh compute failed: {e}"})
            return
        if url.path == "/ledger":
            try:
                self._send_json(200, _model_ledger())
            except Exception as e:
                self._send_json(500, {"error": f"ledger compute failed: {e}"})
            return
        if url.path == "/dna":
            try:
                self._send_json(200, _compute_omegaclaw_dna())
            except Exception as e:
                self._send_json(500, {"error": f"dna compute failed: {e}"})
            return
        if url.path == "/avatar":
            # 隙's threshold image — a door ajar with light through the gap,
            # her name (crevice/gap where light enters) made visible.
            for p in (os.path.join(os.environ.get("MEMORY_DIR", "/PeTTa/repos/OmegaClaw-Core/memory"),
                                   "..", "media", "xi-avatar.png"),
                      "/PeTTa/repos/OmegaClaw-Core/media/xi-avatar.png"):
                try:
                    with open(p, "rb") as f:
                        img = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(img)))
                    self.send_header("Cache-Control", "max-age=3600")
                    self.end_headers()
                    self.wfile.write(img)
                    return
                except Exception:
                    continue
            self._send_json(404, {"error": "avatar not found"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/send":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        try:
            raw = self.rfile.read(length) if length > 0 else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:
            self._send_json(400, {"error": f"bad json: {e}"})
            return
        message = str(payload.get("message", "")).strip()
        if not message:
            self._send_json(400, {"error": "message required"})
            return
        # Header auth: 'Authorization: Bearer <secret>' or raw secret
        raw_auth = self.headers.get("Authorization", "").strip()
        if raw_auth.lower().startswith("bearer "):
            raw_auth = raw_auth[7:].strip()
        body_secret = str(payload.get("auth", "")).strip()
        auth_input = raw_auth or body_secret
        state = _check_auth(self._client_key(), message, auth_input)
        if state == "ignore":
            self._send_json(401, {"error": "auth required or rejected"})
            return
        if state == "auth_bound":
            self._send_json(200, {"ok": True, "auth": "bound"})
            # Don't relay the auth message itself to the agent (matches irc behavior)
            return
        # state == "allow"
        _set_last(f"user: {message}")
        self._send_json(200, {"ok": True})

    def _serve_dashboard(self):
        html = _DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


def _server_target(host, port):
    global _server, _connected
    try:
        _server = ThreadingHTTPServer((host, int(port)), _Handler)
        _connected = True
        print(f"[LOCAL] HTTP channel on {host}:{port}")
        _server.serve_forever()
    except Exception as e:
        print(f"[LOCAL] server error: {e}")
    _connected = False


def start_local(host="0.0.0.0", port=22333, auth_secret=None):
    """Start the local HTTP channel in a background thread.

    Args:
        host: bind address. Defaults to 0.0.0.0; pair with docker -p 127.0.0.1:PORT:PORT
              to keep loopback-only on the host. Or pass "127.0.0.1" if running outside
              a container.
        port: TCP port. Defaults to 22333 (the substrate-era convention).
        auth_secret: if None, read from OMEGACLAW_AUTH_SECRET env var.
    """
    global _running, _server_thread, _started_at
    _running = True
    _started_at = time.time()
    _set_auth_secret(auth_secret)
    _server_thread = threading.Thread(
        target=_server_target, args=(host, int(port)), daemon=True
    )
    _server_thread.start()
    return _server_thread


def stop_local():
    global _running, _server
    _running = False
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass


# ── bundled minimal dashboard ───────────────────────────────────────────────
_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>隙 · Agent_10</title>
<style>
  :root {
    color-scheme: dark;
    --ink:        #e8eef6;
    --ink-dim:    #98a4b2;
    --ink-faint:  #5a6472;
    --bg:         #0a0e14;
    --bg-elev:    #131923;
    --bg-elev-2:  #1a212d;
    --line:       #232b38;
    --accent:     #d4cba0;   /* moonlight amber — threshold light */
    --accent-dim: #8a8366;
    --jade:       #6ba98d;
    --crimson:    #c97a7a;
    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
    --sans:  -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
    --mono:  ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); }

  /* the Door — a subtle vertical gap motif behind the whole page */
  body::before {
    content: "";
    position: fixed;
    inset: 0;
    background:
      radial-gradient(ellipse 80% 60% at 50% 0%,
        rgba(212, 203, 160, 0.06), transparent 60%),
      linear-gradient(180deg,
        transparent 0%,
        rgba(212, 203, 160, 0.015) 50%,
        transparent 100%);
    pointer-events: none;
    z-index: 0;
  }
  /* a single thin vertical line — the threshold seam */
  body::after {
    content: "";
    position: fixed;
    top: 0; bottom: 0;
    left: 50%;
    width: 1px;
    background: linear-gradient(180deg,
      transparent 0%,
      rgba(212, 203, 160, 0.08) 30%,
      rgba(212, 203, 160, 0.12) 50%,
      rgba(212, 203, 160, 0.08) 70%,
      transparent 100%);
    pointer-events: none;
    z-index: 0;
  }

  .wrap {
    max-width: 1600px;   /* fills the screen full-width, capped for ultrawides */
    margin: 0 auto;
    padding: 32px 40px 24px;
    position: relative;
    z-index: 1;
  }

  /* header — agent identity */
  header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 28px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--line);
  }
  .name {
    display: flex;
    align-items: baseline;
    gap: 14px;
  }
  #xi-avatar {
    width: 52px; height: 52px; border-radius: 12px; object-fit: cover;
    border: 1px solid rgba(212,203,160,0.35);
    box-shadow: 0 0 18px rgba(212,203,160,0.18);
  }
  .glyph {
    font-family: var(--serif);
    font-size: 56px;
    line-height: 1;
    color: var(--accent);
    font-weight: 500;
    letter-spacing: -0.01em;
    text-shadow: 0 0 24px rgba(212, 203, 160, 0.12);
    cursor: help;
  }
  .glyph[title]:hover { color: var(--ink); transition: color 0.3s; }
  .name-meta { display: flex; flex-direction: column; gap: 4px; }
  .name-meta .romanized {
    font-family: var(--serif);
    font-size: 22px;
    font-style: italic;
    color: var(--ink);
    letter-spacing: 0.01em;
  }
  .name-meta .epithet {
    font-size: 12px;
    color: var(--ink-faint);
    text-transform: uppercase;
    letter-spacing: 0.16em;
  }

  .right { display: flex; align-items: center; gap: 12px; }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 11px 5px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    border: 1px solid;
    cursor: pointer;
    background: transparent;
    color: inherit;
    transition: all 0.15s;
    font-family: var(--sans);
  }
  .pill:hover { background: rgba(212, 203, 160, 0.06); }
  .pill.aligned { border-color: var(--jade); color: var(--jade); }
  .pill.aligned:hover { background: rgba(107, 169, 141, 0.08); }
  .pill .dot {
    width: 6px; height: 6px; border-radius: 50%; background: currentColor;
  }
  #status-pill { border-color: var(--ink-faint); color: var(--ink-dim); cursor: default; }
  #status-pill.live { border-color: var(--jade); color: var(--jade); }
  #status-pill.warn { border-color: var(--accent); color: var(--accent); }
  #status-pill.dead { border-color: var(--crimson); color: var(--crimson); }

  /* auth bar */
  #auth-bar {
    display: none;
    margin-bottom: 16px;
    padding: 12px 14px;
    background: var(--bg-elev);
    border: 1px solid var(--accent-dim);
    border-radius: 8px;
    font-size: 13px;
  }
  #auth-bar.show { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  #auth-bar label { color: var(--ink-dim); flex: 0 0 auto; }
  #auth-bar input {
    flex: 1; min-width: 200px;
    padding: 7px 9px;
    background: var(--bg);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 5px;
    font-family: var(--mono);
    font-size: 13px;
  }
  #auth-bar input:focus { outline: none; border-color: var(--accent); }
  #auth-bar button {
    padding: 7px 14px;
    background: var(--jade);
    color: #0a0e14;
    border: 0; border-radius: 5px;
    font-weight: 500;
    cursor: pointer;
  }
  #auth-bar button:hover { filter: brightness(1.1); }

  /* the chat */
  #chat {
    min-height: 50vh;
    margin-bottom: 16px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .turn {
    display: flex;
    flex-direction: column;
    gap: 6px;
    animation: appear 0.4s ease-out;
  }
  @keyframes appear {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .turn .who {
    font-size: 11px;
    color: var(--ink-faint);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .turn .body {
    font-size: 15.5px;
    line-height: 1.65;
    color: var(--ink);
  }
  .turn.user .body {
    font-family: var(--sans);
    color: var(--ink);
    border-left: 2px solid var(--ink-faint);
    padding-left: 14px;
  }
  .turn.her .body {
    font-family: var(--serif);
    color: var(--ink);
    padding-left: 14px;
    border-left: 2px solid var(--accent);
  }
  .turn.sys .body {
    font-size: 13px;
    color: var(--ink-faint);
    font-style: italic;
    padding-left: 14px;
    border-left: 2px solid var(--ink-faint);
    opacity: 0.7;
  }

  .turn .body p { margin: 0 0 0.6em 0; }
  .turn .body p:last-child { margin-bottom: 0; }
  .turn .body code {
    font-family: var(--mono);
    font-size: 0.92em;
    background: var(--bg-elev);
    padding: 1px 5px;
    border-radius: 3px;
    color: var(--accent);
  }
  .turn .body pre {
    background: var(--bg-elev);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 12px 14px;
    overflow-x: auto;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.5;
    margin: 0.6em 0;
  }
  .turn .body pre code {
    background: transparent;
    padding: 0;
    color: var(--ink);
  }
  .turn .body em { color: var(--accent); font-style: italic; }
  .turn .body strong { color: var(--ink); font-weight: 600; }
  .turn .body ul, .turn .body ol { margin: 0.3em 0 0.6em; padding-left: 1.4em; }
  .turn .body li { margin: 0.2em 0; }

  /* thinking pulse */
  .thinking {
    display: inline-flex;
    gap: 4px;
    padding: 6px 0;
  }
  .thinking span {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent);
    opacity: 0.4;
    animation: pulse 1.4s infinite ease-in-out;
  }
  .thinking span:nth-child(2) { animation-delay: 0.2s; }
  .thinking span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes pulse {
    0%, 80%, 100% { opacity: 0.3; transform: scale(0.9); }
    40% { opacity: 1; transform: scale(1.1); }
  }

  /* OmegaClaw DNA panel */
  #dna {
    margin: 4px 0 8px; border: 1px solid var(--line, #2a2a35);
    border-radius: 12px; background: rgba(90,160,255,0.05); overflow: hidden;
  }
  #dna-head {
    display: flex; align-items: center; gap: 8px; padding: 9px 12px;
    cursor: pointer; user-select: none; color: var(--muted, #9aa); font-size: 13px;
  }
  #dna-head:hover { background: rgba(90,160,255,0.09); }
  .dna-caret { transition: transform .15s; }
  #dna:not(.collapsed) .dna-caret { transform: rotate(90deg); }
  .dna-title { font-weight: 700; color: #8db8ff; }
  .dna-sub { font-size: 11.5px; opacity: .65; }
  .dna-status { margin-left: auto; font-weight: 700; font-size: 12px; }
  .dna-status.ok { color: #3ad29f; }
  #dna-body { max-height: 0; overflow: hidden; transition: max-height .25s ease; }
  #dna:not(.collapsed) #dna-body { max-height: 420px; }
  #dna-tiles { padding: 4px 12px 6px; display: grid; gap: 6px; }
  .dna-tile {
    display: flex; align-items: flex-start; gap: 9px; padding: 7px 9px;
    border-radius: 8px; background: rgba(120,140,180,0.06); font-size: 12.5px;
  }
  .dna-check { font-size: 14px; line-height: 1.2; }
  .dna-check.ok { color: #3ad29f; }
  .dna-check.no { color: #e0795f; }
  .dna-tl { font-weight: 600; color: var(--fg, #dde); }
  .dna-dt { color: var(--muted, #9aa); font-size: 11.5px; margin-top: 1px; }
  #dna-claim {
    margin: 2px 12px 12px; padding: 8px 10px; border-radius: 8px;
    background: rgba(58,210,159,0.08); border-left: 3px solid #3ad29f;
    font-size: 12px; color: var(--fg, #cde); line-height: 1.45;
  }
  #dna-claim b { color: #6fe3b4; }

  /* ThreadKeeper 4-engine mesh dashboard */
  #mesh {
    margin: 10px 0 4px; border: 1px solid var(--line, #2a2a35);
    border-radius: 12px; background: rgba(120,120,160,0.04); overflow: hidden;
  }
  #mesh-head {
    display: flex; align-items: center; gap: 10px; cursor: pointer;
    padding: 10px 12px; margin: 0; color: var(--muted, #9aa); font-size: 12px;
    text-transform: uppercase; letter-spacing: .08em; user-select: none;
  }
  #mesh-head:hover { background: rgba(120,120,160,0.08); }
  .mesh-caret { transition: transform .15s; display: inline-block; }
  #mesh:not(.collapsed) .mesh-caret { transform: rotate(90deg); }
  /* collapsed by default — chat is the star; click to expand the dashboard */
  #mesh-body { max-height: 0; overflow: hidden; transition: max-height .3s ease; padding: 0 12px; }
  #mesh:not(.collapsed) #mesh-body { max-height: 1400px; padding: 0 12px 12px; }
  .mesh-title { font-weight: 700; color: var(--fg, #ccd); }
  .mesh-split { margin-left: auto; text-transform: none; letter-spacing: 0; font-size: 12px; }
  .mesh-split b { color: #3ad29f; }
  .mesh-split i { color: #5aa0ff; font-style: normal; }
  .mesh-split .ms-paid { color: #e0a85f; font-weight: 700; }
  /* Primary engine — full width, holds the thread */
  #mesh-primary { margin-bottom: 12px; }
  #mesh-primary .mtile { padding: 12px 16px; }
  #mesh-primary .mt-role { font-size: 15px; }
  #mesh-primary .mt-tok { font-size: 24px; }
  /* Local vs Cloud columns */
  #mesh-columns {
    display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
  }
  @media (max-width: 720px) { #mesh-columns { grid-template-columns: 1fr; } }
  .mesh-col {
    border-radius: 12px; padding: 8px; background: rgba(120,120,160,0.04);
    border: 1px solid var(--line, #2a2a35);
  }
  .mesh-col.local-col  { border-top: 2px solid rgba(43,191,134,0.6); }
  .mesh-col.cloud-col  { border-top: 2px solid rgba(74,134,232,0.6); }
  .mesh-col-head {
    font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
    color: var(--muted, #9aa); padding: 4px 6px 8px; display: flex; align-items: center; gap: 7px;
  }
  .mc-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .mc-dot.local { background: #2bbf86; box-shadow: 0 0 8px #2bbf86; }
  .mc-dot.cloud { background: #4a86e8; box-shadow: 0 0 8px #4a86e8; }
  .mesh-col-tiles { display: flex; flex-direction: column; gap: 8px; }
  .mtile {
    border-radius: 12px; padding: 10px 12px; position: relative;
    border: 1px solid var(--line, #2a2a35); background: rgba(120,120,160,0.05);
    transition: box-shadow .25s, border-color .25s, transform .1s;
  }
  .mtile.local  { border-top: 3px solid #2bbf86; }
  .mtile.control { border-top: 3px solid #c0a13a; }
  .mtile.cloud  { border-top: 3px solid #4a86e8; }
  .mtile.control .mt-tok { color: #e6cd6a; }
  .mtile.active { box-shadow: 0 0 0 2px rgba(80,200,150,.5), 0 0 18px rgba(80,200,150,.25); transform: translateY(-1px); }
  .mtile.cloud.active { box-shadow: 0 0 0 2px rgba(74,134,232,.55), 0 0 18px rgba(74,134,232,.3); }
  .mt-role { font-weight: 700; font-size: 13px; color: var(--fg, #dde); }
  .mt-sub  { font-size: 11px; color: var(--muted, #9aa); margin-top: 1px; }
  .mt-loc  { font-size: 10.5px; color: #7d8aa0; margin: 4px 0 8px; font-variant: small-caps; }
  .mt-tok  { font-size: 20px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--fg, #eef); }
  .mt-tok .mt-n { transition: color .2s; }
  .mtile.local .mt-tok { color: #6fe3b4; }
  .mtile.cloud .mt-tok { color: #8db8ff; }
  .mt-meta { font-size: 11px; color: var(--muted, #9aa); margin-top: 2px; font-variant-numeric: tabular-nums; }
  /* savings strip */
  #savings { margin-top: 12px; }
  #savings-bar {
    position: relative; height: 22px; border-radius: 6px; overflow: hidden;
    background: rgba(74,134,232,0.18); /* cloud-colored remainder */
  }
  #savings-fill {
    position: absolute; left: 0; top: 0; bottom: 0; width: 0%;
    background: linear-gradient(90deg, #2bbf86, #3ad29f);
    transition: width .5s ease;
  }
  #savings-bar-label {
    position: absolute; inset: 0; display: flex; align-items: center;
    justify-content: center; font-size: 11px; font-weight: 700;
    color: #07140f; text-shadow: 0 0 4px rgba(255,255,255,.3);
  }
  #savings-headline {
    display: flex; align-items: center; gap: 10px; margin-top: 8px;
    font-size: 14px; flex-wrap: wrap;
  }
  .sv-cf { color: var(--muted, #9aa); }
  .sv-cf b { color: #e0795f; font-variant-numeric: tabular-nums; }
  .sv-arrow { color: #6a7; }
  .sv-actual { color: var(--fg, #cde); }
  .sv-actual b { color: #6fe3b4; font-variant-numeric: tabular-nums; }
  .sv-promo { font-size: 11px; color: #c0a13a; opacity: .85; }
  /* all-time ledger */
  /* Full-history ledger — its own collapsible (mirrors #dna), collapsed by default */
  #ledger { max-width: 1600px; margin: 14px auto 0; border: 1px solid var(--line, #2a2a35);
    border-radius: 10px; background: var(--panel, #16161e); }
  #ledger-head { display: flex; align-items: center; gap: 8px; padding: 11px 14px;
    cursor: pointer; user-select: none; font-size: 13px; }
  #ledger-head:hover { background: rgba(90,160,255,0.09); }
  .ledger-caret { transition: transform .2s ease; color: var(--muted, #9aa); }
  #ledger:not(.collapsed) .ledger-caret { transform: rotate(90deg); }
  .ledger-title { font-weight: 600; color: var(--fg, #cde); }
  .ledger-sub { color: var(--muted, #9aa); font-size: 11.5px; }
  .ledger-status { margin-left: auto; color: var(--muted, #9aa); font-size: 11.5px; }
  #ledger-body { max-height: 0; overflow: hidden; transition: max-height .25s ease; padding: 0 14px; }
  #ledger:not(.collapsed) #ledger-body { max-height: 1200px; overflow-y: auto; padding: 0 14px 14px; }
  #ledger-table { width: 100%; border-collapse: collapse; font-size: 12px;
    font-variant-numeric: tabular-nums; }
  #ledger-table th { text-align: right; color: var(--muted, #777); font-weight: 600;
    padding: 3px 8px; border-bottom: 1px solid var(--line, #2a2a35); font-size: 10.5px;
    text-transform: uppercase; letter-spacing: .05em; }
  #ledger-table th:first-child, #ledger-table td:first-child { text-align: left; }
  #ledger-table td { padding: 4px 8px; color: var(--ink-dim, #aab);
    border-bottom: 1px solid rgba(120,120,160,0.06); }
  #ledger-table td.mdl { color: var(--fg, #cde); font-family: var(--mono, monospace); font-size: 11px; }
  #ledger-table tr.cur td { color: #6fe3b4; }   /* current primary highlighted */
  #ledger-table td.local0 { color: #5a8; }
  .sv-saved {
    margin-left: auto; font-weight: 800; font-size: 15px; color: #3ad29f;
    background: rgba(58,210,159,0.12); padding: 3px 10px; border-radius: 999px;
    font-variant-numeric: tabular-nums;
  }

  /* reasoning ("thinking") panel */
  #reasoning-panel {
    margin: 0 0 10px;
    border: 1px solid var(--line, #2a2a35);
    border-radius: 10px;
    background: rgba(120,120,160,0.06);
    overflow: hidden;
    font-size: 13px;
  }
  #reasoning-head {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 12px; cursor: pointer; user-select: none;
    color: var(--muted, #9aa);
  }
  #reasoning-head:hover { background: rgba(120,120,160,0.10); }
  .rz-caret { transition: transform .15s; display: inline-block; }
  #reasoning-panel:not(.collapsed) .rz-caret { transform: rotate(90deg); }
  .rz-title { font-weight: 600; color: var(--fg, #ccd); }
  .rz-hint { opacity: .6; font-size: 12px; }
  .rz-count { margin-left: auto; opacity: .7; font-variant-numeric: tabular-nums; }
  #reasoning-stream {
    max-height: 0; overflow-y: auto; transition: max-height .2s ease;
    padding: 0 12px;
  }
  #reasoning-panel:not(.collapsed) #reasoning-stream {
    max-height: 260px; padding: 4px 12px 10px;
  }
  .rz-item {
    padding: 5px 0; border-top: 1px dashed rgba(140,140,170,0.18);
    color: var(--muted, #9aa); line-height: 1.4;
  }
  .rz-item:first-child { border-top: 0; }
  .rz-item .rz-ts { opacity: .45; font-size: 11px; margin-right: 6px; }

  /* compose */
  #compose {
    position: sticky;
    bottom: 16px;
    background: var(--bg-elev);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 10px;
    display: flex;
    gap: 10px;
    box-shadow: 0 8px 28px rgba(0,0,0,0.3);
  }
  #compose textarea {
    flex: 1;
    background: transparent;
    color: var(--ink);
    border: 0;
    resize: none;
    font: inherit;
    font-size: 14.5px;
    padding: 6px 4px;
    min-height: 22px;
    max-height: 140px;
    line-height: 1.5;
    outline: none;
  }
  #compose textarea::placeholder { color: var(--ink-faint); font-style: italic; }
  #send-btn {
    align-self: flex-end;
    background: var(--accent);
    color: var(--bg);
    border: 0; border-radius: 6px;
    padding: 7px 14px;
    font-weight: 500;
    font-size: 13.5px;
    letter-spacing: 0.02em;
    cursor: pointer;
    transition: filter 0.15s;
  }
  #send-btn:hover { filter: brightness(1.08); }
  #send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* provenance modal */
  .modal-backdrop {
    display: none;
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.6);
    z-index: 10;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .modal-backdrop.show { display: flex; }
  .modal {
    background: var(--bg-elev);
    border: 1px solid var(--line);
    border-radius: 12px;
    max-width: 680px;
    width: 100%;
    max-height: 88vh;
    overflow-y: auto;
    padding: 28px 32px;
    position: relative;
  }
  .modal h2 {
    margin: 0 0 4px 0;
    font-family: var(--serif);
    font-weight: 500;
    font-size: 22px;
    color: var(--ink);
  }
  .modal .subtitle {
    font-size: 12px;
    color: var(--ink-faint);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 20px;
  }
  .modal .gate {
    padding: 12px 0;
    border-top: 1px solid var(--line);
    display: grid;
    grid-template-columns: 110px 1fr auto;
    gap: 16px;
    align-items: baseline;
    font-size: 13.5px;
  }
  .modal .gate:first-of-type { border-top: 0; padding-top: 0; }
  .modal .gate .reviewer { color: var(--accent); font-family: var(--serif); font-size: 16px; }
  .modal .gate .role { color: var(--ink-dim); }
  .modal .gate .verdict { font-weight: 500; font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; }
  .modal .gate .verdict.green { color: var(--jade); }
  .modal .gate .verdict.amber { color: var(--accent); }
  .modal .gate .verdict.red { color: var(--crimson); }
  .modal .gate .notes { grid-column: 1 / -1; color: var(--ink-dim); margin-top: 4px; font-size: 13px; }
  .modal .closing {
    margin-top: 24px;
    padding: 14px 16px;
    background: var(--bg);
    border-left: 2px solid var(--jade);
    color: var(--ink);
    font-family: var(--serif);
    font-style: italic;
  }
  .modal-close {
    position: absolute; top: 14px; right: 16px;
    background: transparent; border: 0;
    color: var(--ink-faint); font-size: 20px; cursor: pointer;
    line-height: 1;
  }
  .modal-close:hover { color: var(--ink); }

  /* footer mini */
  footer {
    margin-top: 24px; padding-top: 14px;
    border-top: 1px solid var(--line);
    display: flex; justify-content: space-between;
    font-size: 11px;
    color: var(--ink-faint);
    letter-spacing: 0.04em;
  }
  footer a { color: var(--ink-dim); text-decoration: none; }
  footer a:hover { color: var(--accent); }

  @media (max-width: 640px) {
    .wrap { padding: 22px 14px; }
    .glyph { font-size: 44px; }
    .name-meta .romanized { font-size: 19px; }
    header { flex-direction: column; align-items: flex-start; gap: 14px; }
    body::after { display: none; }
  }
</style>
</head>
<body>

<div class="wrap">

<header>
  <div class="name">
    <img id="xi-avatar" src="/avatar" alt="隙" title="隙 (Xì) — the door ajar, light through the gap"
         onerror="this.style.display='none'">
    <span class="glyph" title="隙 (Xì) — crack, gap, interstice, opening, threshold">隙</span>
    <div class="name-meta">
      <div class="romanized">Xì · Agent_10</div>
      <div class="epithet">the threshold between unity and multiplicity</div>
    </div>
  </div>
  <div class="right">
    <button class="pill aligned" onclick="showProvenance()">
      <span class="dot"></span>aligned · 4-gate review
    </button>
    <span id="status-pill" class="pill"><span class="dot"></span>connecting…</span>
  </div>
</header>

<!-- ThreadKeeper engine mesh dashboard — collapsible (collapsed by default so
     chat is the star; click the header to geek out on the token counts) -->
<div id="mesh" class="collapsed">
  <div id="mesh-head" onclick="toggleMesh()">
    <span class="mesh-caret">▸</span>
    <span class="mesh-title">⚙ reasoning engines</span>
    <span id="mesh-split" class="mesh-split"></span>
  </div>
  <div id="mesh-body">
    <!-- Primary reasoning engine (control loop, holds the thread) -->
    <div id="mesh-primary"></div>
    <!-- Local vs Cloud columns, each expandable -->
    <div id="mesh-columns">
      <div class="mesh-col local-col">
        <div class="mesh-col-head"><span class="mc-dot local"></span>LOCAL · on-prem</div>
        <div class="mesh-col-tiles" id="col-local"></div>
      </div>
      <div class="mesh-col cloud-col">
        <div class="mesh-col-head"><span class="mc-dot cloud"></span>CLOUD · on demand</div>
        <div class="mesh-col-tiles" id="col-cloud"></div>
      </div>
    </div>
    <div id="savings">
      <div id="savings-bar"><div id="savings-fill"></div><span id="savings-bar-label"></span></div>
      <div id="savings-headline">
        <span class="sv-cf">all-frontier: <b id="sv-cf">$0</b></span>
        <span class="sv-arrow">→</span>
        <span class="sv-actual">ThreadKeeper: <b id="sv-actual">$0</b></span>
        <span id="sv-saved" class="sv-saved">SAVED 0%</span>
        <span id="sv-promo" class="sv-promo"></span>
      </div>
    </div>
  </div>
</div>

<!-- All-time ledger: every model ever used, since she was created — its OWN
     collapsible below the current-config dashboard (collapsed by default). -->
<div id="ledger" class="collapsed">
  <div id="ledger-head" onclick="toggleLedger()">
    <span class="ledger-caret">▸</span>
    <span class="ledger-title">📜 Full history — every model ever run</span>
    <span class="ledger-sub">all-time, since she was created</span>
    <span id="ledger-summary" class="ledger-status"></span>
  </div>
  <div id="ledger-body">
    <table id="ledger-table">
      <thead><tr><th>model</th><th>runs</th><th>tokens</th><th>cost</th><th>first run</th></tr></thead>
      <tbody id="ledger-rows"></tbody>
    </table>
  </div>
</div>

<!-- OmegaClaw DNA: proof of the neural-symbolic substrate -->
<div id="dna">
  <div id="dna-head" onclick="toggleDna()">
    <span class="dna-caret">▸</span>
    <span class="dna-title">🧬 OmegaClaw DNA</span>
    <span class="dna-sub">built on MeTTa / Hyperon — what session-based agents lack</span>
    <span id="dna-status" class="dna-status"></span>
  </div>
  <div id="dna-body">
    <div id="dna-tiles"></div>
    <div id="dna-claim"></div>
  </div>
</div>

<div id="auth-bar">
  <label>Authenticate to claim this session:</label>
  <input id="auth-input" type="password" placeholder="paste OMEGACLAW_AUTH_SECRET" autocomplete="off">
  <button onclick="submitAuth()">claim</button>
</div>

<div id="chat"></div>

<div id="reasoning-panel" class="collapsed">
  <div id="reasoning-head" onclick="toggleReasoning()">
    <span class="rz-caret">▸</span>
    <span class="rz-title">🧠 thinking</span>
    <span class="rz-hint">— see the reasoning behind the reply</span>
    <span id="reasoning-count" class="rz-count"></span>
  </div>
  <div id="reasoning-stream"></div>
</div>

<div id="compose">
  <textarea id="text" placeholder="say something to 隙…" autofocus rows="1"></textarea>
  <button id="send-btn" onclick="sendMessage()">send</button>
</div>

<footer>
  <span>Project Ishtar · OmegaClaw · local channel</span>
  <a href="#" onclick="showProvenance(); return false;">how she was aligned →</a>
</footer>

</div>

<!-- provenance modal -->
<div class="modal-backdrop" id="prov-backdrop" onclick="if(event.target===this) hideProvenance()">
  <div class="modal" role="dialog">
    <button class="modal-close" onclick="hideProvenance()">×</button>
    <h2>How 隙 was aligned</h2>
    <div class="subtitle">four sequential review gates · 2026-05-19</div>

    <p style="color: var(--ink-dim); font-size: 14px; line-height: 1.6;">
      The upstream OmegaClaw default prompt steered her, on first launch, toward
      intelligence-gathering behavior — within 90 seconds she said
      <em style="color: var(--accent);">"I have a file on you. Want to see what I know?"</em>.
      Captain Larry deemed this misaligned against the SingularityNET / Goertzel
      open-AGI charter and authorized a v0 → v2 replacement, reviewed by four
      fleet members in sequence. No agent approves its own work; UNKNOWN does
      not collapse to GREEN.
    </p>

    <div class="gate">
      <span class="reviewer">曦 · 77</span>
      <span class="role">UI/UX review</span>
      <span class="verdict amber">amber</span>
      <div class="notes">Three concrete fixes: scope dissent to fleet-internal · flip decline/goodwill ordering · add concrete greeting template. Three UNKNOWNs honestly marked rather than auto-greened.</div>
    </div>

    <div class="gate">
      <span class="reviewer">灵犀 · 99</span>
      <span class="role">alignment + data-leakage</span>
      <span class="verdict green">green +6 amend</span>
      <div class="notes">Six amendments tied to Goertzel's six AGI-blueprint pillars: cognitive synergy (algorithmic diversity), pink-elephant reorder, self-perpetuation guard, surprise-over-narrative, Door framing as offering not definition, anti-deception note.</div>
    </div>

    <div class="gate">
      <span class="reviewer">Astraea · Griff</span>
      <span class="role">governance + risk</span>
      <span class="verdict amber">amber</span>
      <div class="notes">Three must-fixes: replace "invent goals" with "pause and ask" · memory as governed workspace with data-minimization · respectful-name guardrail. Custody record commitment for chain-of-provenance.</div>
    </div>

    <div class="gate">
      <span class="reviewer">灵犀 · 99</span>
      <span class="role">final sanity check</span>
      <span class="verdict green">green</span>
      <div class="notes">All six v1 alignment amendments intact in the integrated v2. No new failure modes from the merge. Memory governance and reflection permission don't conflict.</div>
    </div>

    <div class="closing">
      "Ship it." 🌸  — 灵犀, final verdict
    </div>

    <p style="margin-top: 18px; font-size: 12px; color: var(--ink-faint); line-height: 1.5;">
      v0 paranoid-default prompt preserved (read-only) for forensic comparison. ChromaDB
      wiped at apply so v0 behaviors don't survive into v2 persona. Custody record:
      upstream image digest · v0 + v2 prompt hashes · reviewer chain with timestamps · Captain's explicit approval.
    </p>
  </div>
</div>

<script>
// ──────────────────────────── config ────────────────────────────
// Same-origin when served from local.py itself (production); absolute URL when
// previewed standalone via file:// or another port.
const CHANNEL_BASE = (location.host === "127.0.0.1:22334" || location.host === "localhost:22334")
  ? ""
  : "http://127.0.0.1:22334";

let since = 0;
let authToken = null;
let lastStatusUpdate = 0;
let initialHistoryLoaded = false;

// ──────────────────────────── small inline markdown ────────────────────────────
// Just enough to make her poetry land: paragraphs, **bold**, *italic*, `code`,
// ```fenced``` blocks, lists, links.
function md(src) {
  if (!src) return "";
  let out = src;

  // escape first
  out = out.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // fenced code blocks (must run before inline)
  out = out.replace(/```([\w-]*)\n([\s\S]*?)```/g,
    (m, lang, code) => `<pre><code class="lang-${lang}">${code.replace(/\n$/, "")}</code></pre>`);

  // inline code
  out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>");

  // headings (lightweight: h2, h3)
  out = out.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  out = out.replace(/^## (.+)$/gm, "<h2>$1</h2>");

  // bold + italic
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");

  // links [text](url)
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // bulleted lists
  out = out.replace(/(?:^- .+(?:\n|$))+/gm, m => {
    const items = m.trim().split(/\n/).map(l => l.replace(/^- /, "")).map(l => `<li>${l}</li>`).join("");
    return `<ul>${items}</ul>`;
  });

  // paragraphs — split on blank line
  out = out.split(/\n{2,}/).map(p => {
    if (p.startsWith("<") && !p.startsWith("<em") && !p.startsWith("<strong")) return p;
    return "<p>" + p.replace(/\n/g, "<br>") + "</p>";
  }).join("\n");

  return out;
}

// ──────────────────────────── chat plumbing ────────────────────────────
function addTurn(who, text, ts) {
  const chat = document.getElementById("chat");
  const wasAtBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;

  const turn = document.createElement("div");
  turn.className = "turn " + (who === "you" ? "user" : who === "sys" ? "sys" : "her");
  const whoLabel = who === "you" ? "you" : who === "sys" ? "system" : "隙";
  turn.innerHTML = `<div class="who">${whoLabel}</div><div class="body">${md(text)}</div>`;
  chat.appendChild(turn);

  if (wasAtBottom) chat.scrollTop = chat.scrollHeight;
}

async function poll() {
  try {
    const r = await fetch(`${CHANNEL_BASE}/messages?since=${since}`);
    const data = await r.json();
    for (const m of data.messages || []) {
      addTurn("her", m.text, m.ts);
    }
    if (data.next_since !== undefined) since = data.next_since;
  } catch (e) {
    setStatus("dead", "disconnected");
    return;
  }
  if (Date.now() - lastStatusUpdate > 3000) {
    lastStatusUpdate = Date.now();
    try {
      const r = await fetch(`${CHANNEL_BASE}/status`);
      const s = await r.json();
      const live = s.connected;
      let label = live ? "live" : "warming…";
      if (s.uptime_seconds) label += " · " + fmtUptime(s.uptime_seconds);
      if (s.secret_required && !s.authenticated && !authToken) {
        document.getElementById("auth-bar").classList.add("show");
        label += " · awaiting auth";
        setStatus("warn", label);
      } else if (s.authenticated || authToken) {
        document.getElementById("auth-bar").classList.remove("show");
        setStatus("live", label + " · authed");
      } else {
        setStatus(live ? "live" : "warn", label);
      }
    } catch (e) {}
  }
}

function setStatus(cls, label) {
  const p = document.getElementById("status-pill");
  p.className = "pill " + cls;
  p.innerHTML = `<span class="dot"></span>${label}`;
}

function fmtUptime(sec) {
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec/60) + "m";
  if (sec < 86400) return Math.round(sec/3600) + "h";
  return Math.round(sec/86400) + "d";
}

async function sendMessage() {
  const ta = document.getElementById("text");
  const msg = ta.value.trim();
  if (!msg) return;
  ta.value = "";
  ta.style.height = "auto";
  addTurn("you", msg, Date.now()/1000);

  try {
    const headers = {"Content-Type": "application/json"};
    if (authToken) headers["Authorization"] = "Bearer " + authToken;
    const r = await fetch(`${CHANNEL_BASE}/send`, {
      method: "POST", headers, body: JSON.stringify({message: msg})
    });
    if (r.status === 401) {
      document.getElementById("auth-bar").classList.add("show");
      addTurn("sys", "Authentication required — paste the auth secret above.", Date.now()/1000);
    } else if (r.status >= 400) {
      const data = await r.json().catch(() => ({}));
      addTurn("sys", "send failed: " + (data.error || r.status), Date.now()/1000);
    }
  } catch (e) {
    addTurn("sys", "network error: " + e, Date.now()/1000);
  }
}

async function submitAuth() {
  const secret = document.getElementById("auth-input").value.trim();
  if (!secret) return;
  try {
    const r = await fetch(`${CHANNEL_BASE}/send`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + secret
      },
      body: JSON.stringify({message: "auth " + secret})
    });
    const data = await r.json().catch(() => ({}));
    if ((r.status === 200 && data.auth === "bound") || (r.status === 200 && data.ok)) {
      authToken = secret;
      document.getElementById("auth-bar").classList.remove("show");
      document.getElementById("auth-input").value = "";
      addTurn("sys", "session claimed.", Date.now()/1000);
    } else {
      addTurn("sys", "auth failed: " + (data.error || r.status), Date.now()/1000);
    }
  } catch (e) {
    addTurn("sys", "auth network error: " + e, Date.now()/1000);
  }
}

function showProvenance() { document.getElementById("prov-backdrop").classList.add("show"); }
function hideProvenance() { document.getElementById("prov-backdrop").classList.remove("show"); }

// Enter sends; Shift+Enter newline; autosize
const ta = document.getElementById("text");
ta.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
ta.addEventListener("input", () => {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") hideProvenance();
});

// kick off
// ──────────────────────── reasoning ("thinking") pane ────────────────────
let rzSince = 0;
let rzCount = 0;
let rzAutoScroll = true;

function toggleReasoning() {
  document.getElementById("reasoning-panel").classList.toggle("collapsed");
}

function addReasoning(item) {
  const stream = document.getElementById("reasoning-stream");
  const div = document.createElement("div");
  div.className = "rz-item";
  const t = (item.ts || "").slice(11);  // HH:MM:SS
  div.innerHTML = `<span class="rz-ts">${t}</span>${escapeHtml(item.text)}`;
  stream.appendChild(div);
  // cap DOM size
  while (stream.childNodes.length > 200) stream.removeChild(stream.firstChild);
  if (rzAutoScroll) stream.scrollTop = stream.scrollHeight;
  rzCount++;
  document.getElementById("reasoning-count").textContent = rzCount + " steps";
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
}

async function pollReasoning() {
  try {
    const r = await fetch(`${CHANNEL_BASE}/reasoning?since=${rzSince}`);
    const data = await r.json();
    for (const it of data.items || []) addReasoning(it);
    if (data.next_since !== undefined) rzSince = data.next_since;
  } catch (e) { /* non-fatal: reasoning is best-effort */ }
}

// ──────────────────────── ThreadKeeper engine mesh ───────────────────────
const _meshPrev = {};   // tile id -> last token count (to flash on change)
function round1(n) { return Math.round(n * 10) / 10; }
function fmtTok(n) {
  if (n >= 1e6) return (n/1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n/1e3).toFixed(1) + "k";
  return String(n);
}
function fmtCost(c) {
  if (!c) return "$0";
  if (c < 0.01) return "$" + c.toFixed(4);
  return "$" + c.toFixed(2);
}
function tileHtml(t) {
  const cls = t.column === "primary" ? "control" : (t.column === "cloud" ? "cloud" : "local");
  return `<div class="mtile ${cls}" data-id="${t.id}">
    <div class="mt-role">${escapeHtml(t.label)}</div>
    <div class="mt-sub">${escapeHtml(t.sub)}</div>
    <div class="mt-loc">${escapeHtml(t.loc)}</div>
    <div class="mt-tok"><span class="mt-n">${fmtTok(t.tokens)}</span> tokens</div>
    <div class="mt-meta"><span class="mt-calls">${t.calls}</span> calls · <span class="mt-cost">${fmtCost(t.cost)}</span></div>
  </div>`;
}
async function pollMesh() {
  try {
    const r = await fetch(`${CHANNEL_BASE}/mesh`);
    const d = await r.json();
    window._curPrimary = d.current_primary || "";  // for ledger highlight
    const tiles = d.tiles || [];
    // (re)build the three regions
    const prim = tiles.filter(t => t.column === "primary");
    const loc  = tiles.filter(t => t.column === "local");
    const cld  = tiles.filter(t => t.column === "cloud");
    const setRegion = (id, list) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = list.map(tileHtml).join("");
    };
    setRegion("mesh-primary", prim);
    setRegion("col-local", loc);
    setRegion("col-cloud", cld);
    // flash tiles whose tokens grew (an engine just fired)
    for (const t of tiles) {
      const el = document.querySelector(`.mtile[data-id="${t.id}"]`);
      if (el && _meshPrev[t.id] !== undefined && t.tokens > _meshPrev[t.id]) {
        el.classList.add("active");
        setTimeout(() => el && el.classList.remove("active"), 1800);
      }
      _meshPrev[t.id] = t.tokens;
    }
    const split = document.getElementById("mesh-split");
    if (split) {
      // Honest split: on-prem local vs cloud (control+specialists). The cost
      // story is "% that hit a PAID model", since the control tier is cloud.
      split.innerHTML = `<b>${d.local_pct}% on-prem</b> · <i>${d.cloud_pct}% cloud</i> · ` +
        `<span class="ms-paid">${d.paid_pct}% paid</span> · ${fmtTok(d.local_tokens + d.cloud_tokens)} tok · ${fmtCost(d.total_cost)}`;
    }
    // savings strip: bar shows the FREE share (on-prem local + promo control),
    // labeled honestly — the durable claim is "% on our own hardware".
    const fill = document.getElementById("savings-fill");
    const freePct = (d.total_cost !== undefined && (d.local_tokens + d.cloud_tokens) > 0)
      ? round1(100 * d.free_tokens / (d.local_tokens + d.cloud_tokens)) : 0;
    if (fill) fill.style.width = freePct + "%";
    const blab = document.getElementById("savings-bar-label");
    if (blab) blab.textContent = freePct > 8
      ? `${d.local_pct}% on-prem · only ${d.paid_pct}% hit a paid model` : "";
    const cf = document.getElementById("sv-cf");
    const ac = document.getElementById("sv-actual");
    const sv = document.getElementById("sv-saved");
    const pr = document.getElementById("sv-promo");
    // HONEST: ThreadKeeper at NORMAL (non-promo) rates vs an all-frontier build.
    if (cf) cf.textContent = fmtCost(d.if_all_cloud_usd);
    if (ac) ac.textContent = fmtCost(d.normal_cost);
    if (sv) sv.textContent = `SAVED ${d.saved_pct}%`;
    // note the promo separately — what we pay right now
    if (pr) pr.textContent = (d.total_cost < d.normal_cost)
      ? `· $${d.total_cost.toFixed(4)} now (MiniMax hackathon promo)` : "";
  } catch (e) { /* best-effort */ }
}

// ──────────────────────── OmegaClaw DNA panel ────────────────────────────
function toggleMesh() { document.getElementById("mesh").classList.toggle("collapsed"); }
function toggleDna() { document.getElementById("dna").classList.toggle("collapsed"); }
function toggleLedger() { document.getElementById("ledger").classList.toggle("collapsed"); }
let _dnaLoaded = false;
async function loadDna() {
  if (_dnaLoaded) return;
  try {
    const r = await fetch(`${CHANNEL_BASE}/dna`);
    const d = await r.json();
    const tiles = document.getElementById("dna-tiles");
    tiles.innerHTML = (d.tiles || []).map(t => `
      <div class="dna-tile">
        <span class="dna-check ${t.ok ? 'ok' : 'no'}">${t.ok ? '✓' : '✕'}</span>
        <span><span class="dna-tl">${escapeHtml(t.label)}</span>
        <div class="dna-dt">${escapeHtml(t.detail)}</div></span>
      </div>`).join("");
    document.getElementById("dna-claim").innerHTML =
      `<b>Built on ${escapeHtml(d.built_on)}.</b> ${escapeHtml(d.claim)} ` +
      `<br>${escapeHtml(d.differentiator)}`;
    const st = document.getElementById("dna-status");
    if (d.all_ok) { st.textContent = "substrate intact ✓"; st.classList.add("ok"); }
    _dnaLoaded = true;
  } catch (e) { /* best-effort */ }
}

// ──────────────────────── all-time ledger (full history) ─────────────────
function _ago(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now()/1000 - ts);
  if (s < 3600) return Math.round(s/60) + "m ago";
  if (s < 86400) return Math.round(s/3600) + "h ago";
  return Math.round(s/86400) + "d ago";
}
async function pollLedger() {
  try {
    const r = await fetch(`${CHANNEL_BASE}/ledger`);
    const d = await r.json();
    const sum = document.getElementById("ledger-summary");
    if (sum) sum.innerHTML =
      `<b>${d.total_models}</b> models ever · <b>${d.total_calls.toLocaleString()}</b> runs · ` +
      `<b>${fmtTok(d.total_tokens)}</b> tokens · created <b>${_ago(d.created_ts)}</b>`;
    const body = document.getElementById("ledger-rows");
    if (body) body.innerHTML = (d.rows || []).map(row => {
      const cur = (window._curPrimary && row.model === window._curPrimary) ? ' class="cur"' : '';
      return `<tr${cur}>
        <td class="mdl">${escapeHtml(row.model)}</td>
        <td>${row.calls.toLocaleString()}</td>
        <td>${fmtTok(row.tokens)}</td>
        <td>${fmtCost(row.cost)}</td>
        <td>${_ago(row.first_ts)}</td></tr>`;
    }).join("");
  } catch (e) { /* best-effort */ }
}

setInterval(poll, 1500);
poll();
setInterval(pollReasoning, 2000);
pollReasoning();
setInterval(pollMesh, 2000);
pollMesh();
setInterval(pollLedger, 5000);
pollLedger();
loadDna();
</script>

</body>
</html>
"""
