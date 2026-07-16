# Escalation demo — smart routing, not "local is dumb"

The honest version of ThreadKeeper's routing story. Modern local models
(Granite-30B) are GOOD — they solve the classic reasoning puzzles. So we do NOT
fake local failing. We show the **escalation decision**: cheap work stays local
($0); a genuinely hard design problem escalates to the cloud specialists.

## What we ran

| Task | Routed to | Cost | Result |
|---|---|---|---|
| "What is 15% of 240?" (trivial) | **Local Granite-30B** | $0 | "36" — stayed local, correctly |
| "Design a 4-rule token-budget escalation policy with thresholds" (hard design) | **GLM 5.2** (Cloud Specialist A) | ~$0.003 | 4 concrete rules with thresholds |
| same hard problem | **DeepSeek V4 Pro** (Cloud Specialist B) | ~$0.006 | independent answer — triangulation |

(Meta touch: the hard question was literally "design a token-budget escalation
policy" — so GLM and DeepSeek end up describing ThreadKeeper's own logic.)

## What the dashboard shows
- Easy task: Local tile ticks, $0.
- Hard task: GLM + DeepSeek tiles light up; paid cost is fractions of a cent.
- Headline: ~98% saved vs an all-frontier build (at normal, non-promo rates).

## The point
"Escalate only when needed" is real and visible: the architecture spends paid
tokens only on the hard problem, and the bulk runs free/local. No single-model
dependency — the two cloud specialists agree, which raises confidence.

## How to run it live
See the dispatch calls in this folder / the WebUI: delegate an easy task to the
`researcher` (local) persona and a hard one to the `specialist` (GLM) persona;
the mesh dashboard reflects the routing in real time.
