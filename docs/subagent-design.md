# Subagent Dispatch — Design

This document is the design reference for OmegaClaw's subagent-dispatch
primitive (`src/subagent.py`) and the `delegate` skill
(`src/skills.metta`). In ThreadKeeper's four-node mesh
([`architecture.md`](./architecture.md)) this primitive is what powers
the **worker loop** (a local persona) and the **cloud specialist** node
(a premium persona invoked only for hard subproblems).

The skill reference is [`reference-skills-subagent.md`](./reference-skills-subagent.md);
the end-to-end walkthrough is [`tutorial-09-subagents.md`](./tutorial-09-subagents.md).

---

## 1. Intent

Pair the foundation-model **parent** (which keeps the thread and makes
routing judgments) with a narrow **specialist subagent** (which executes
a bounded task). Each subagent is bound — by a JSON persona config — to
its own provider / model / endpoint, typically smaller, cheaper, or more
specialized than the parent. The parent delegates a goal; the subagent
runs a short, tool-using loop and returns a single-string **digest**.

This keeps premium reasoning rare and cheap reasoning frequent — the
core ThreadKeeper thesis.

---

## 2. Dispatch contract

`(delegate goal [tools] [persona] [max_turns])` →
`subagent.dispatch(goal, tool_subset_csv, persona_key, max_turns)` →
a single-line digest string (≤ `max_chars`).

The dispatch primitive **never raises into the MeTTa interpreter**.
Every failure path returns a `(subagent error: …)` digest string instead.

### v1 scope
- One dispatch at a time, synchronously.
- **No subagent → subagent recursion** (`delegate` is a v1-excluded
  tool, see §4.5.2).
- Bounded turns (hard cap `OMEGACLAW_SUBAGENT_MAX_TURNS`, default 8).
- Digest capped (`OMEGACLAW_SUBAGENT_MAX_DIGEST_CHARS`, default 2000).

---

## 3. The subagent mini-loop

Each turn the subagent receives a prompt with: its PERSONA, the narrowed
TOOLS catalogue, an OUTPUT_FORMAT directive, the GOAL, the iteration
counter, and (after turn 1) LAST_RESULTS + a small HISTORY tail. The
subagent emits one s-expression per line. The loop:

1. calls the subagent's LLM (via `lib_llm_ext.AIProvider.chat`);
2. parses line-leading s-expressions (stripping `<think>` blocks and
   markdown fences);
3. accepts `(emit "…")` only when it is the sole parsed call in the
   response, then returns that digest and stops;
4. rejects mixed or conflicting `emit` + tool responses as protocol
   violations, so a final digest cannot hide later tool actions;
5. otherwise executes the parsed tool calls and feeds the results into
   the next turn.

If the turn budget is exhausted without `(emit …)`, a fallback digest
summarizing the last results is returned.

---

## 4. Persona configuration

### 4.4.1 Persona config schema

A subagent persona is two files in `memory/personas-subagent/`
(directory overridable via `OMEGACLAW_SUBAGENT_PERSONA_DIR`):

- `<key>.json` — the binding (provider/model/endpoint + tool defaults)
- `<persona_file>` — the persona prompt text

| Field | Required | Meaning |
|---|---|---|
| `persona_file` | yes | Path to the persona prompt text (relative to the persona dir unless absolute). |
| `provider` | yes | Provider name (informational + used for registry lookup). |
| `model` | yes | Model identifier passed to the chat call. |
| `base_url` | optional | Endpoint override; takes precedence when present. |
| `api_key_env` | yes | **Name** of the env var carrying the key. Never embed key material. |
| `max_output_tokens` | optional (1500) | Per-call output cap. |
| `default_tool_subset` | optional | Tools used when the dispatch omits the tools arg. |
| `notes` | optional | Free-form; not consumed by the dispatcher. |

See [`../memory/personas-subagent/README.md`](../memory/personas-subagent/README.md)
and the shipped `researcher.json.example`.

### 4.5 Tool registry for subagents (v1)

Tools a subagent may call, narrowed per dispatch to the requested
subset:

| Tool | Backed by |
|---|---|
| `search` | `channels/websearch.py` (DuckDuckGo) |
| `read-file` / `write-file` / `append-file` | stdlib file I/O |
| `shell` | restricted subprocess (no apostrophes, 30 s timeout, 4 KB output cap) |
| `tavily-search` | `src/agentverse.py` (if `uagents` installed) |
| `technical-analysis` | `src/agentverse.py` (if `uagents` installed) |

Unknown tools are rejected at parse time with a clear error string.

### 4.5.2 Deliberately excluded tools

These parent skills are **not** callable by subagents in v1, and the
dispatcher rejects them at parse time:

`remember`, `pin`, `metta`, `send`, `delegate`, `query`, `episodes`.

Rationale:
- `delegate` — no subagent→subagent recursion (keeps cost and blast
  radius bounded, and the escalation accounting one level deep).
- `send` — only the parent talks to the human/channel; the subagent
  returns a digest, it does not message out.
- `remember` / `pin` / `query` / `episodes` — long-term and working
  memory belong to the parent's thread; a short-lived helper should not
  mutate or read the parent's memory store.
- `metta` — arbitrary interpreter access stays with the parent.

---

## 5. Provider integration

`resolve_or_instantiate_provider(...)` builds a fresh
`lib_llm_ext.AIProvider` per dispatch from the persona's binding. It
**does not mutate** `lib_llm_ext._provider_registry`, so concurrent or
successive dispatches with different bindings never interfere. The
underlying client is lazily initialized on first `.chat()` (no network
at construction). The `lib_llm_ext` import is deferred to dispatch time
so the module stays importable for linting without the full runtime.

---

## 6. Relationship to the budget seam

The dispatcher logs token usage (today via `lib_llm_ext`, and on the
ThreadKeeper roadmap via a direct `BudgetTracker.record(...)` call). The
parent's control loop consults
[`threadkeeper_budget.py`](../src/threadkeeper_budget.py)
`should_escalate(...)` **before** issuing a `(delegate …)` to a premium
specialist — so the escalation decision and the dispatch are two halves
of the same governed seam. See [`architecture.md`](./architecture.md)
§"The escalation trigger".
