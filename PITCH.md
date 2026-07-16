# ThreadKeeper — 3-minute pitch (BGI Sprint I · Track 1: Improvements to OmegaClaw)

> Theme: **"Agents that hold the thread."**
> Format: 3-min pitch/video + 2-min Q&A. Must cover: problem · track · artifact ·
> 10-min reviewability · ecosystem relevance · next steps. Live demo + share-screen.

---

## 0:00 — Hook (15s)
"The theme of this sprint is *agents that hold the thread*. So we built one, and
we named it that: **ThreadKeeper.** It's an OmegaClaw architecture that lets an
agent hold a long thread — cheaply, persistently, and without depending on any
single cloud model."

## 0:15 — The problem (35s)
"A persistent agent loops constantly. But the two things it does each loop have
wildly different value. *Most* loops are cheap bookkeeping — what's the goal,
what did I just learn, what's next. A *few* loops are genuinely hard.

Running a frontier cloud model on *every* loop is expensive and slow. Running a
tiny model on the *hard* loops is unreliable. And today's OmegaClaw cloud setups
bind to a single API provider — one point of cost, failure, and lock-in.

Reasoning *frequency* and reasoning *quality* are different axes. Most stacks
couple them. That's the problem."

## 0:50 — The solution: the four-node mesh (45s)
"ThreadKeeper decouples them. Four nodes, every one swappable:
- a cheap, **local control loop** that *holds the thread* — goal, memory, and the
  decision of *when* hard reasoning is worth buying;
- a **local worker loop** that iterates cheaply;
- **cloud specialists**, invoked *only* for hard subproblems;
- and a **budget gate** between them that decides escalation against a token
  budget — and logs every call and every decision.

The control loop is OmegaClaw's own persistent MeTTa loop. We didn't replace it —
we built *around* it. That's the whole 'holds the thread' idea, made literal."

## 1:35 — LIVE DEMO (60s)  ← share-screen
"Here's our agent, 隙, running **entirely on local hardware** — no cloud API.
[type a message] Watch the new **🧠 thinking pane**: that's its real
per-iteration reasoning, surfaced live — the auditable inference OmegaClaw
promises, that the stock WebUI throws away. [reply lands] That answer came from
a model on a GPU in this room. The budget log shows zero cloud spend for this
turn."

*(Backup if live is flaky: 30-sec screen recording of the same.)*

## 2:35 — Why it's a real Track-1 improvement (15s)
"It's an *extension*, not a rewrite. Three named Track-1 contributions in one:
**memory** — we mapped and fixed OmegaClaw's disaster-recovery and
cross-provider migration gaps; **reliability/performance** — local inference with
graceful cloud fallback; **plugin-style extension** — a bounded, governed
subagent-dispatch primitive. An OmegaClaw deployment that ignores all three runs
exactly as before."

## 2:50 — Reviewability + next steps (10s)
"Ten-minute reviewable: README, architecture diagram, and a working repo at
github.com/hlgreenblatt/ThreadKeeper. Next: a re-embedding migration tool, and
NexiClaw — an ethics agent — as the first real application riding the mesh.
ThreadKeeper holds the thread. Thank you."

---

## Q&A prep (the 2-min round)
- **"Isn't this just routing/model-switching?"** → No — the novel part is the
  *budget-governed escalation decision* + the *auditable trail*. Routing is the
  easy half; deciding *when it's worth the spend* and *proving why* is the half
  that makes it governance-grade (ISO/IEC 42001).
- **"What's actually new vs OmegaClaw?"** → Subagent dispatch (`delegate`),
  the cost-awareness seam, the config surface, and the DR/migration analysis +
  reasoning-transparency pane. All additive.
- **"Does it depend on specific models?"** → No. Every node names a
  provider/model/endpoint by config; keys by env-var name only. Demo runs local
  Gemma/Granite; could be all-cloud or any gradient.
- **"How does memory survive a migration?"** → Two on-disk stores
  (history.metta + chroma_db). Portable IF the embedder is held constant —
  which local-first embedding guarantees. We documented the trap and the fix.
- **"Single point of failure?"** → Control loop is local and cheap; if a cloud
  specialist is unreachable, the agent degrades to local reasoning instead of
  dying. We demoed the fallback path.
