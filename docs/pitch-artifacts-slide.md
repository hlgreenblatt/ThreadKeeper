# Pitch slide — "The Artifacts" (real code, runnable in <10 min)

All files below exist in this repo on the `threadkeeper` branch (verified).
Use as the artifacts slide in the 3-minute pitch video.

## ⭐ What makes ThreadKeeper different (lead here)
- **`docs/recursive-self-improvement.md`** — the agent diagnosed its own failure,
  *designed the fix*, we built it to spec, and it improved itself — with a
  human-in-the-loop governor **it insisted on** ("the governor must live where I
  can't subvert it"). OmegaClaw's stated RSI goal, demonstrated.
- **`channels/local.py`** — a live mesh dashboard: 5 reasoning engines, real-time
  tokens/cost, a counterfactual savings headline (~98% vs an all-frontier build,
  at normal rates), and an "OmegaClaw DNA" panel proving the MeTTa/Hyperon
  substrate is present and intact.

## 🔧 The four-node mesh, as real files
- **`src/subagent.py`** — the governed `delegate` skill (bounded sub-agent dispatch
  to local workers / cloud specialists).
- **`src/threadkeeper_budget.py`** — the budget gate: token accounting + the
  escalation decision (`should_escalate`).
- **`threadkeeper.config.yaml`** — the configurable surface; every engine swappable,
  credentials by env-var name only (no secrets in the repo).
- **`src/skills.metta`** — `delegate` wired into OmegaClaw's MeTTa loop (additive).

## ▶ Proof it works (live demo on screen)
- **`demo/demo-bulk-summarize.sh`** — runnable; routes real work to the local
  worker at $0. (Run this live — deterministic, fast, low-risk on Zoom.)
- **`docs/escalation-demo.md`** — easy task stays local ($0), hard task escalates
  to GLM 5.2 + DeepSeek V4 Pro. Honest "smart routing, not local-is-dumb."

## 🛡 Governance / audit (beneficence differentiator)
- Every LLM call → **`memory/usage.jsonl`**; every escalation decision →
  **`memory/escalations.jsonl`**. ISO/IEC 42001-friendly audit trail.

## 📖 Reviewability (<10 min)
- Reading path: **`README.md` → `HACKATHON.md` → `PITCH.md`**
- **`docs/architecture.md`** + **`docs/architecture.png`** — the four-node diagram.
- Repo: **github.com/hlgreenblatt/ThreadKeeper** (fork of asi-alliance/OmegaClaw-Core,
  MIT, default branch `threadkeeper`).

## Honesty notes to state aloud
- Extension, not correction: a deployment that ignores ThreadKeeper runs exactly
  as before.
- The ~4.7× input-token figure is illustrative (earlier local test vs a 30B
  model), not a benchmark.
- Model-agnostic: models shown are examples; every role is swappable.
- The escalation policy is a deliberate v1, designed as a swappable seam.
