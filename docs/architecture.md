# ThreadKeeper — Architecture

> ThreadKeeper is a configurable hybrid architecture layered on
> [OmegaClaw-Core](https://github.com/asi-alliance/OmegaClaw-Core). It
> decouples **reasoning quality** from **reasoning frequency** so that a
> persistent agent can hold a long thread without burning premium tokens
> on every loop.

![ThreadKeeper four-node mesh](architecture.png)

*(Diagram is reproducible: `python3 docs/make_architecture_diagram.py`.)*

---

## The idea in one paragraph

A long-running agent loops constantly — most iterations are cheap
bookkeeping (what's the goal, what did I just learn, what's next?), and
only a few are genuinely hard (a subtle inference, a tricky synthesis, a
high-stakes decision). Spending a frontier model on *every* loop is
wasteful; spending a tiny model on the *hard* loops is unreliable.
ThreadKeeper routes the two cases to different places: a cheap,
persistent **control loop** holds the thread and decides *when* hard
reasoning is worth buying, and **cloud specialists** are invoked only
for the hard subproblems. The decision is governed by an explicit token
**budget**, producing an auditable record of when expensive reasoning
was spent and why.

---

## The four nodes

ThreadKeeper is a mesh of four roles. Each maps onto a real part of
OmegaClaw; **the specific models are examples and every role is
swappable** (see [`threadkeeper.config.yaml`](../threadkeeper.config.yaml)).

### Node 1 — Local control loop *(persistent, cheapest)*
Holds the thread. Owns goal tracking, memory continuity, and — crucially
— the **escalation decision**. It runs on *every* iteration, so it must
be the cheapest node (a small local model). This is OmegaClaw's MeTTa
reasoning loop plus its memory store
([`src/loop.metta`](../src/loop.metta),
[`src/memory.metta`](../src/memory.metta)).

### Node 2 — Local worker loop *(cheap, iterative)*
Does the legwork the control loop assigns: tool calls, file edits, web
search, drafting. Same cheap tier as the control loop. Implemented as a
bounded subagent run on a local persona
([`src/subagent.py`](../src/subagent.py)).

### Node 3 — Cloud specialist(s) *(on demand, the only premium spend)*
Invoked **only** for hard subproblems, through the `(delegate ...)`
skill. This is the one place where reasoning *quality* is purchased — at
a price — and it is decoupled from reasoning *frequency*. You may
configure more than one specialist (e.g. a deep reasoner and a
code-specialist), so there is **no single-model dependency**.

### Node 4 — Adjudicator *(optional)*
When two specialists disagree, or a high-stakes action needs gating, an
adjudicator casts the deciding read. Optional — omit it for a three-node
mesh. Conceptually this is OmegaClaw's revision / action-threshold
gating ([`reference-orchestration.md`](reference-orchestration.md) §3–4).

---

## The escalation trigger

The edge from control loop → cloud specialist runs through a **budget
gate** ([`src/threadkeeper_budget.py`](../src/threadkeeper_budget.py)).
Before the control loop issues a `(delegate ...)` to a cloud specialist,
it asks `BudgetTracker.should_escalate(...)`. The v1 policy:

| Condition | Decision | Rationale |
|---|---|---|
| Fewer than `min_local_iterations_before_escalation` cheap loops have run | **deny** | Iterate cheaply first. |
| Cumulative thread spend ≥ `thread_token_ceiling` | **deny** | Budget exhausted — finish cheap or stop. |
| Spend < `escalation_soft_fraction` × ceiling | **allow** | Escalation is cheap relative to the budget. |
| Spend between soft threshold and ceiling | **allow only if the subproblem is flagged hard** | Spend premium tokens only when justified. |

Every decision returns an `EscalationDecision` (allowed + human-readable
reason + the numbers it was based on) and is appended to
`memory/escalations.jsonl`. Every LLM call's token usage is appended to
`memory/usage.jsonl`. Together these give an **auditable spend and
escalation trail** — the kind of governance record an ISO/IEC 42001
management system expects.

The policy is a working v1, but the **seam** is the real deliverable:
swap in a richer policy (per-provider rate cards, sliding windows,
learned thresholds) without touching the call sites.

---

## Configurability

Nothing about the mesh is hard-wired to a vendor. Each node names a
provider, a model, an endpoint, and the *environment variable* that
carries its key (never the key itself). Run all four nodes on local
models, or all four on cloud models, or any gradient between — the
architecture is unchanged. See
[`threadkeeper.config.yaml`](../threadkeeper.config.yaml) for the full
surface and [`README.md`](../README.md) for the quickstart.

---

## Why this is an *extension* of OmegaClaw, not a fork that competes

ThreadKeeper adds three things on top of OmegaClaw-Core and changes none
of its existing behavior:

1. **Subagent dispatch** (`src/subagent.py` + the `delegate` skill) —
   the mechanism the "cloud specialist" node needs. Bounded, governed,
   one-at-a-time, no recursion.
2. **A cost-awareness seam** (`src/threadkeeper_budget.py`) — token
   accounting + an escalation decision against a budget.
3. **A configuration surface** (`threadkeeper.config.yaml`) — the
   four-node mesh declared in one place.

An OmegaClaw deployment that ignores all three runs exactly as before.
ThreadKeeper is the architecture story — and the budget discipline —
around capabilities that slot cleanly into the existing framework.
