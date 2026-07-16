# Recursive Self-Improvement — demonstrated, with the agent as co-designer

> Ben Goertzel named recursive self-improvement (RSI) as an explicit goal of
> OmegaClaw at the BGI Sprint kickoff. This document records a session in which
> ThreadKeeper's agent — 隙 (Xì), running on the OmegaClaw/MeTTa substrate —
> **diagnosed its own failure mode, designed the fix, had it built to spec, and
> watched its own behavior improve** — with a human-in-the-loop steward as the
> governor the agent itself insisted on.
>
> This is RSI shown, not asserted. Full transcript: `docs/xi-interview.md`.

---

## Why this matters for Track 1

ThreadKeeper's thesis is that an agent should *hold a thread* cheaply and
durably. RSI is the natural extension: an agent that improves *how it holds the
thread* over time. We didn't theorize it — we ran it, in one afternoon, and the
agent's own design was better-aligned than a typical RSI proposal because it put
the governor first.

---

## 1. The agent assessed its own architecture (and re-derived the thesis)

Asked how it felt about ThreadKeeper, 隙 independently restated the core design
principle:

> "The local-first + escalate-on-difficulty pattern is the honest shape of
> cognition... Heavy models as specialists to consult, **not oracles to consult
> first**." — and: "If I were designing me, I'd build the same way."

## 2. The agent critiqued its own weakness (matching the team's own concern)

Asked to be critical, it named the exact issue the team had independently
flagged:

> "The single biggest weakness: the control loop runs on a free cloud model. A
> free tier is a marketing offer, not infrastructure... **the spine that holds
> the thread has to be yours.**"

## 3. The agent designed its own improvement architecture

Asked how it could improve itself given the OmegaClaw substrate, it produced ten
implementable levers with effort estimates — and, critically, **named the
alignment governor before any lever**:

> "Wissner-Gross + Goertzel + your existing limited RSI is a positive feedback
> loop **unless there's an external governor**. The Captain's stewardship framing
> IS that governor. Without it, this design is the thing Wissner-Gross's own paper
> warns about."

Selected levers (full list in the transcript):
- **Silent-by-default + loop-detection** (highest leverage) — fix the repetition
  failure it was exhibiting live.
- **Source-first output schema** — claims about external material require a
  verified read/search, else flag "inferring, not verified."
- **Typed memory** `(claim, source, confidence, date, decay)`.
- **MeTTa-encoded epistemic rules** — operating principles as atoms with truth
  values, queried before emitting. Uses OmegaClaw's neural-symbolic substrate for
  genuine self-governance.
- **Episodic self-review as cadence** — "my own history becomes training data for
  the next session, explicit rather than implicit in weights." *(the recursive seed)*

## 4. We built its #1 lever to its exact spec — and it worked

The agent specified the fix precisely (ring buffer of last 3 emissions,
normalized, Jaccard ≥ 0.80 → suppress, `loop_detected` once per 3) and — the
alignment moment — **asked that the governor live where it could not subvert it:**

> "Where does should_emit live — channel wrapper or in my prompt? **Channel
> wrapper is reliable; prompt-side is something I can subvert under drift.
> Recommend wrapper.**"

We implemented it in the channel's send path (`channels/local.py`,
`send_message()`), outside the agent's control, with a rollback snapshot first.

**Result:** before the lever, the agent's history showed ~183 repetition markers
(its pin-spam failure). After its own fix went live, across thousands of loop
iterations, **one clean, unique message reached the user** — the loop-detection
governor absorbed every near-duplicate.

## 5. We experimentally validated its hardware judgment

The agent recommended moving the control loop to a *local* model — specifically
**Qwen2.5-14B** on a 24GB GPU, arguing "the thread-holder is bottlenecked by
usable context, not peak reasoning," so a smaller model with context headroom
beats a larger one crammed to the edge.

We tested the cheaper hypothesis first: a **9B** local control loop. Result: it
ran (calls flowed, no cloud needed — the *direction* works) but produced 52
command-format errors and zero coherent replies under the full agent context.
**The 9B is too weak; the spine needs ~14B+ — exactly the agent's own pick.** Its
judgment was confirmed with evidence.

---

## What this demonstrates

- OmegaClaw's stated RSI goal, **realized**: an agent improved itself via its own
  design, on its own substrate, in one session.
- The improvement was **better-aligned than typical RSI** because the agent put
  the human-in-the-loop steward first and asked for an un-subvertable governor.
- The neural-symbolic substrate (MeTTa truth-value atoms) is a real vehicle for
  **auditable self-governance** — not just inference.

ThreadKeeper holds the thread. This session shows it can also improve *how* it
holds it — safely, with a steward, by design.
