# Hackathon submission — ThreadKeeper

**Event:** BGI Sprint I
**Track:** Improvements to OmegaClaw
**Team:** ThreadKeeper
**Repository:** this repo — a fork of
[`asi-alliance/OmegaClaw-Core`](https://github.com/asi-alliance/OmegaClaw-Core),
branched from a recent upstream `main` (June 2026) and extended.

---

## Submission summary

**ThreadKeeper** is a configurable hybrid OmegaClaw architecture for
open, decentralized, budget-aware agent orchestration. It decouples
*reasoning quality* from *reasoning frequency*:

- a cheap, persistent **local control loop** holds the thread (goal
  tracking, memory, continuity, and the escalation decision);
- a **local worker loop** iterates cheaply;
- **cloud specialists** are invoked only for hard subproblems, via a
  governed `delegate` skill;
- an optional **adjudicator** breaks ties / gates high-stakes actions.

A configurable **token budget** drives the escalation decision, and
every LLM call and escalation decision is logged — an ISO/IEC
42001-friendly audit trail. The result is lower cost, better
persistence, and no single-model dependency.

It is framed as an **extension** to OmegaClaw-Core, not a correction:
OmegaClaw's persistent MeTTa loop is the ideal foundation for the
"thread keeper," and a deployment that ignores ThreadKeeper's additions
runs exactly as before.

## What this submission adds on top of OmegaClaw-Core

| Addition | Files | Purpose |
|---|---|---|
| Subagent dispatch (`delegate` skill) | `src/subagent.py`, `src/skills.metta` | The "cloud specialist" node — bounded, governed delegation to a right-sized model. |
| Cost-awareness seam | `src/threadkeeper_budget.py` | Per-loop token accounting; supplies live facts to the policy and executes its verdict. |
| **Escalation policy in MeTTa** | `src/escalation.metta`, `tests/test_escalation_metta_parity.py` | The routing decision as Atomspace rules (`tk-escalate`), evaluated through PeTTa — symbolic, auditable, agent-rewritable. Python fallback proven equivalent by a parity test. |
| Configuration surface | `threadkeeper.config.yaml` | The four-node mesh + budget threshold declared in one place. |
| Architecture docs + diagram | `docs/architecture.md`, `docs/architecture.png` | The concept, the node responsibilities, the escalation logic. |
| Subagent reference + tutorial | `docs/reference-skills-subagent.md`, `docs/tutorial-09-subagents.md` | How to use the `delegate` skill end to end. |
| Persona scaffolding | `memory/personas-subagent/` | Example specialist persona config (env-var keys only — no secrets). |

## Where to start reading

1. [`README.md`](./README.md) — elevator pitch, the problem, the
   solution, the quickstart.
2. [`docs/architecture.md`](./docs/architecture.md) — the four-node mesh
   and escalation-trigger logic.
3. [`threadkeeper.config.yaml`](./threadkeeper.config.yaml) — the
   configurable surface (models are examples; all roles swappable).
4. [`src/threadkeeper_budget.py`](./src/threadkeeper_budget.py) — the
   cost-awareness / escalation seam.
5. [`src/subagent.py`](./src/subagent.py) — the delegation primitive.

## Constraints honored

- **No secrets in the repo.** All credentials are referenced by the
  *name* of an environment variable; no key material is committed. The
  persona-config directory gitignores real configs and keeps only the
  `.example`.
- **Extension, not critique.** ThreadKeeper changes none of OmegaClaw's
  existing behavior and is offered in the spirit of the
  OmegaClaw / SingularityNET vision.
- **Model-agnostic.** No claim that any model is "better." The
  architecture works regardless of which models fill each role.

## Status & notes

- The base is a recent `asi-alliance/OmegaClaw-Core` `main` (branched June 2026); ThreadKeeper's additions are layered on top.
- The subagent dispatch primitive and the budget module are working
  code (import-checked and functionally exercised), not vaporware; the
  escalation *policy* is a deliberate v1 designed as a swappable seam.
- The `~4.7×` input-token figure cited in the README came from earlier
  local testing of subagent dispatch against a 30B local model on
  tool-heavy workloads. It is illustrative — real numbers depend
  entirely on the models and workload — not a benchmark claim.

*Submission editable until 28 Jun 2026.*
