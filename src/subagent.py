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
import subprocess
import sys
import time

# Worker-call usage log — SAME file the parent loop + dashboard read, so
# delegated work shows up on the ThreadKeeper mesh's Local Worker tile.
_USAGE_LOG_PATH = os.path.join(
    os.environ.get("MEMORY_DIR", "/PeTTa/repos/OmegaClaw-Core/memory"),
    "usage.jsonl",
)


def _log_worker_usage(model, in_tok, out_tok):
    """Append a worker LLM call to usage.jsonl. Never raises."""
    try:
        rec = {"ts": time.time(), "model": model,
               "input_tokens": int(in_tok or 0), "output_tokens": int(out_tok or 0)}
        with open(_USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
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
def _persona_is_cloud(cfg):
    """Classify a persona as cloud vs local. A persona may declare
    `node_role` explicitly; otherwise we infer from the base_url (the
    same local-Ollama heuristic _call_subagent_llm uses)."""
    role = (cfg.get("node_role") or "").strip().lower()
    if role in ("cloud_specialist", "cloud", "specialist", "adjudicator"):
        return True
    if role in ("worker_loop", "control_loop", "local", "worker"):
        return False
    base_url = (cfg.get("base_url") or "").lower()
    is_local = ("11434" in base_url) or ("localhost" in base_url) or ("ollama" in base_url)
    return not is_local


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

# Hard caps. Per-call max_turns is clamped by the lower of dispatch
# arg, persona-config default, and this hard cap. Same for digest.
SUBAGENT_MAX_TURNS_HARD_CAP = int(
    os.environ.get("OMEGACLAW_SUBAGENT_MAX_TURNS", "8")
)
SUBAGENT_MAX_DIGEST_CHARS = int(
    os.environ.get("OMEGACLAW_SUBAGENT_MAX_DIGEST_CHARS", "2000")
)
SUBAGENT_DEFAULT_OUTPUT_TOKENS = 1500

# Per-subagent-iteration history cap. The subagent's internal history
# is much smaller than the parent's (~4000 chars vs 30000) because
# the subagent operates on a focused goal, not an ongoing
# conversation.
_SUBAGENT_HISTORY_CAP = 4000
_SUBAGENT_RESULTS_CAP = 4000

# Shell tool restrictions. Subagent's shell is more restricted than
# parent's — disabled by default, optional executable allowlist, no shell=True,
# output truncated, default 30s timeout.
_SHELL_OUTPUT_CAP = 4000
_SHELL_TIMEOUT_S = 30


def _subagent_workspace_root():
    """Return the filesystem root visible to subagent file tools.

    Defaults to the current working directory so deployments that run the agent
    from the repo keep the historical relative-path ergonomics while closing
    absolute/parent traversal escapes. Override with
    OMEGACLAW_SUBAGENT_WORKSPACE for a narrower or dedicated scratch root.
    """
    root = os.environ.get("OMEGACLAW_SUBAGENT_WORKSPACE") or os.getcwd()
    return os.path.realpath(os.path.abspath(root))


def _resolve_workspace_path(path):
    if not path or "\x00" in str(path):
        raise ValueError("invalid path")
    root = _subagent_workspace_root()
    raw = str(path)
    candidate = raw if os.path.isabs(raw) else os.path.join(root, raw)
    resolved = os.path.realpath(os.path.abspath(candidate))
    if os.path.commonpath([root, resolved]) != root:
        raise ValueError(
            f"path escapes subagent workspace ({root}): {path}"
        )
    return resolved


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

def load_persona_config(persona_key):
    """Read memory/personas-subagent/<key>.json. Returns dict with the
    fields documented in docs/subagent-design.md §4.4.1."""
    path = os.path.join(PERSONA_DIR, f"{persona_key}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"persona config '{persona_key}.json' not found at {path}"
        )
    with open(path, "r", encoding="utf-8") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"persona config '{persona_key}.json' is malformed JSON: {e}"
            )
    required = ["persona_file", "provider", "model", "api_key_env"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(
            f"persona config '{persona_key}.json' missing required field(s): {missing}"
        )
    cfg["_persona_key"] = persona_key
    return cfg


def load_persona_prompt(persona_file, persona_key):
    """Read the persona text. `persona_file` is the value of the
    persona_file field; it is resolved relative to PERSONA_DIR
    unless absolute."""
    if os.path.isabs(persona_file):
        path = persona_file
    else:
        path = os.path.join(PERSONA_DIR, persona_file)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"persona prompt '{persona_file}' for key '{persona_key}' "
            f"not found at {path}"
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


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
            lambda q: websearch.search(q),
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
            lambda q: agentverse.tavily_search(q),
            "endpoint_independent",
        )
        registry["technical-analysis"] = (
            lambda t: agentverse.technical_analysis(t),
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

def resolve_or_instantiate_provider(provider_name, model_name, base_url, var_name):
    """Build an AIProvider scoped to this dispatch. Stays inside
    lib_llm_ext's existing class abstraction; does not mutate
    _provider_registry.

    A fresh AIProvider instance per dispatch ensures each persona's
    (provider, model, base_url, api_key_env) binding is honored
    exactly — no shared mutable state across dispatches with
    different bindings. AIProvider's _ensure_client lazy-inits the
    underlying openai client on first .chat() call, so this is
    cheap at construction (no network).

    Returns a dict with keys: provider (AIProvider), model, provider_name."""
    api_key = os.environ.get(var_name)
    if not api_key:
        raise RuntimeError(
            f"env var '{var_name}' is unset; cannot reach endpoint for "
            f"provider '{provider_name}'"
        )
    # Build a plain OpenAI-compatible client for the CLOUD worker path
    # (e.g. GLM 5.2 specialist). The LOCAL Ollama path in
    # _call_subagent_llm uses native urllib and ignores this client, so a
    # client failure here doesn't break local delegation. Import deferred
    # so the module stays lint-importable without openai installed.
    client = None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=(base_url or None))
    except Exception:
        client = None  # local path doesn't need it
    return {
        "provider": client,
        "model": model_name,
        "provider_name": provider_name,
        "base_url": base_url or "",
        "var_name": var_name,
    }


# ----------------------------------------------------------------------
# LLM call — uses AIProvider.chat from lib_llm_ext.
# ----------------------------------------------------------------------

def _call_subagent_llm(provider_handle, content, max_tokens):
    """Call the subagent's worker LLM and return response text.

    For LOCAL Ollama endpoints we use the NATIVE /api/chat path with
    {"think": false} — the OpenAI /v1 path on this Ollama build returns
    EMPTY content for reasoning models (qwen/gemma/gpt-oss/granite) because
    hidden <think> tokens consume the whole budget. The native path with
    thinking disabled returns real content. For non-Ollama (cloud) endpoints
    we fall back to AIProvider.chat (/v1), which is correct there.

    Never raises into the MeTTa interpreter — returns a (subagent ...) string
    on failure.
    """
    base_url = (provider_handle.get("base_url") or "").rstrip("/")
    model = provider_handle["model"]
    is_local = ("11434" in base_url) or ("localhost" in base_url) or ("ollama" in base_url.lower())

    if base_url and is_local:
        import json as _json
        import urllib.request as _u
        root = base_url[:-3] if base_url.endswith("/v1") else base_url
        try:
            body = _json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": False,
                "think": False,
                "options": {"num_predict": max_tokens},
            }).encode()
            req = _u.Request(root + "/api/chat", data=body,
                             headers={"Content-Type": "application/json"})
            with _u.urlopen(req, timeout=180) as r:
                data = _json.loads(r.read().decode("utf-8", errors="replace"))
            _log_worker_usage(model, data.get("prompt_eval_count", 0),
                              data.get("eval_count", 0))
            return (data.get("message") or {}).get("content", "") or ""
        except Exception as e:
            return f"(subagent LLM call failed: {type(e).__name__}: {e})"

    # Cloud endpoint — standard OpenAI /v1 chat (GLM/DeepSeek separate
    # reasoning from content correctly here).
    client = provider_handle["provider"]
    if client is None:
        return "(subagent error: no cloud client available)"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
        try:
            u = resp.usage
            _log_worker_usage(model, getattr(u, "prompt_tokens", 0),
                              getattr(u, "completion_tokens", 0))
        except Exception:
            pass
        return resp.choices[0].message.content or ""
    except Exception as e:
        return f"(subagent LLM call failed: {type(e).__name__}: {e})"


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
                          iteration, max_iterations):
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
    if last_results:
        parts.append(f"LAST_RESULTS:\n{last_results[-_SUBAGENT_RESULTS_CAP:]}")
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
    for raw_line in text.splitlines():
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
            if content.startswith('"') and content.endswith('"'):
                content = content[1:-1]
            return [filename, content]
        parts = rest.split(None, 1)
        if len(parts) == 1:
            return [parts[0], ""]
        filename, content = parts[0], parts[1].strip()
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        return [filename, content]
    # Single-arg skills
    if rest.startswith('"') and rest.endswith('"'):
        return [rest[1:-1]]
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

def _tool_read_file(path):
    try:
        resolved = _resolve_workspace_path(path)
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"(read-file error: {e})"


def _tool_write_file(path, content):
    try:
        resolved = _resolve_workspace_path(path)
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return "WRITE-FILE-SUCCESS"
    except Exception as e:
        return f"(write-file error: {e})"


def _tool_append_file(path, content):
    try:
        resolved = _resolve_workspace_path(path)
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved, "a", encoding="utf-8") as f:
            f.write(content + "\n")
        return "APPEND-FILE-SUCCESS"
    except Exception as e:
        return f"(append-file error: {e})"


def _tool_shell(cmd):
    """Restricted command runner: disabled unless explicitly enabled and
    executable-allowlisted. Uses shell=False so metacharacters are arguments,
    not command separators."""
    if not _shell_enabled():
        return "(shell error: disabled by default; set OMEGACLAW_SUBAGENT_ENABLE_SHELL=1 and OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST)"
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return f"(shell error: invalid command: {e})"
    if not argv:
        return "(shell error: empty command)"
    allow = _shell_allowlist()
    exe = os.path.basename(argv[0])
    if not allow or exe not in allow:
        return f"(shell error: executable '{exe}' is not allowlisted)"
    try:
        out = subprocess.run(
            argv, shell=False, capture_output=True, timeout=_SHELL_TIMEOUT_S,
        )
        text = (out.stdout or b"").decode("utf-8", errors="replace")
        text += (out.stderr or b"").decode("utf-8", errors="replace")
        return text[:_SHELL_OUTPUT_CAP]
    except subprocess.TimeoutExpired:
        return f"(shell error: timed out after {_SHELL_TIMEOUT_S}s)"
    except Exception as e:
        return f"(shell error: {e})"


def run_tools(calls, allowed_names):
    """Execute each call against the registry, return aggregated result
    string for the next turn's prompt."""
    if not calls:
        return "(no parseable tool calls in last response)"
    reg = _tool_registry()
    out_parts = []
    for (name, args) in calls:
        if name == "emit":
            # emit is the loop terminator; handled by the caller
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
        fn, _category = tool
        try:
            result = fn(*args)
        except TypeError as e:
            out_parts.append(f"(SKILL_ARG_ERROR: {name}: {e})")
            continue
        except Exception as e:
            out_parts.append(f"(SKILL_RUNTIME_ERROR: {name}: {e})")
            continue
        out_parts.append(f"(COMMAND_RETURN: ({name} {args[0] if args else ''}) "
                         f"{_clip(str(result), 2000)})")
    return " ".join(out_parts)


def _extract_emit(calls):
    """Find the first (emit "...") call in `calls`; return its arg."""
    for (name, args) in calls:
        if name == "emit" and args:
            return args[0]
    return None


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
    """Wrap an error into the structured digest string the parent
    sees. Always returns; never raises into the parent's MeTTa
    interpreter."""
    return f"(subagent error: {msg})"


# ----------------------------------------------------------------------
# The dispatch entry point — called from MeTTa via py-call
# ----------------------------------------------------------------------

def dispatch(goal, tool_subset_csv, persona_key, max_turns=None,
             max_chars=None):
    """Entry point invoked by (delegate ...) in src/skills.metta.

    Returns a single-line string (length ≤ max_chars or
    SUBAGENT_MAX_DIGEST_CHARS) suitable for inclusion in the
    parent's LAST_SKILL_USE_RESULTS.

    Failure path always returns a (subagent error: ...) string;
    never raises into the MeTTa interpreter."""
    # 1. Bound the per-call caps
    if max_turns is None:
        max_turns = SUBAGENT_MAX_TURNS_HARD_CAP
    try:
        max_turns = int(max_turns)
    except (TypeError, ValueError):
        max_turns = SUBAGENT_MAX_TURNS_HARD_CAP
    bounded_turns = max(1, min(max_turns, SUBAGENT_MAX_TURNS_HARD_CAP))

    if max_chars is None:
        max_chars = SUBAGENT_MAX_DIGEST_CHARS
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = SUBAGENT_MAX_DIGEST_CHARS
    bounded_chars = max(100, min(max_chars, SUBAGENT_MAX_DIGEST_CHARS))

    # 2. Load persona config
    try:
        cfg = load_persona_config(persona_key)
    except (FileNotFoundError, ValueError) as e:
        return error(str(e))

    # 2b. ThreadKeeper escalation gate. A delegation to a CLOUD specialist is
    # the expensive node — consult the budget policy (src/escalation.metta via
    # PeTTa) before spending. If denied, refuse the dispatch and return the
    # [metta]-tagged reason so the parent loop sees WHY (and can finish on cheap
    # nodes). Local delegations are free and pass through. Fail-open on errors.
    gate_allowed, gate_reason = _escalation_gate(cfg)
    if not gate_allowed:
        return cap(
            f"(escalation denied) {gate_reason} — "
            f"cloud delegation to persona '{persona_key}' refused by the "
            f"ThreadKeeper budget policy; finish on local/cheap nodes or stop.",
            bounded_chars,
        )

    # 3. Resolve tool subset
    subset_csv = (tool_subset_csv or "").strip()
    if not subset_csv:
        default_subset = cfg.get("default_tool_subset", [])
        if not default_subset:
            return error(
                f"no tool subset given and persona '{persona_key}' has no "
                "default_tool_subset"
            )
        subset_csv = ",".join(default_subset)
    try:
        tool_names = parse_subset(subset_csv)
    except ValueError as e:
        return error(str(e))

    validate_endpoint_compat(tool_names, cfg)  # always passes in v1

    # 4. Load persona prompt
    try:
        persona_text = load_persona_prompt(cfg["persona_file"], persona_key)
    except FileNotFoundError as e:
        return error(str(e))

    # 5. Resolve provider
    try:
        provider_handle = resolve_or_instantiate_provider(
            provider_name=cfg["provider"],
            model_name=cfg["model"],
            base_url=cfg.get("base_url"),
            var_name=cfg["api_key_env"],
        )
    except RuntimeError as e:
        return error(str(e))

    # 6. Run the mini-loop
    catalog = tools_catalog(tool_names)
    history = []
    last_results = ""
    max_out_tok = int(cfg.get("max_output_tokens", SUBAGENT_DEFAULT_OUTPUT_TOKENS))

    for turn in range(bounded_turns):
        prompt = build_subagent_prompt(
            persona_text, catalog, last_results, history, goal,
            turn + 1, bounded_turns,
        )
        raw = _call_subagent_llm(provider_handle, prompt, max_out_tok)
        # If the call failed catastrophically, _call_subagent_llm
        # already returned a (subagent ...) string; surface as digest.
        if raw.startswith("(subagent LLM call failed"):
            return cap(raw, bounded_chars)

        calls = parse_calls(raw)
        emit_value = _extract_emit(calls)
        if emit_value is not None:
            return cap(emit_value, bounded_chars)

        last_results = run_tools(calls, tool_names)
        history.append((turn + 1, raw, last_results))

    # Loop exhausted without (emit ...)
    fallback = (
        f"(subagent: max_turns ({bounded_turns}) reached without emit; "
        f"last_results: {_clip(last_results, 500)})"
    )
    return cap(fallback, bounded_chars)
