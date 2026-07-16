;; ════════════════════════════════════════════════════════════════════════
;; escalation.metta — ThreadKeeper routing/escalation POLICY as MeTTa rules.
;;
;; ThreadKeeper rests on one thesis: reasoning *quality* should be decoupled from
;; reasoning *frequency*: the cheap local control loop runs every iteration, and
;; an expensive cloud specialist is invoked only when justified. THAT DECISION
;; is the policy below — and it lives here, in the Atomspace, not buried in
;; Python `if` statements.
;;
;; Why MeTTa and not Python: this is the part of ThreadKeeper the agent is meant
;; to be able to read and, eventually, rewrite (the OmegaClaw self-modification
;; story — see docs/recursive-self-improvement.md). Expressing the policy as
;; Atomspace rules makes it inspectable and agent-editable via the existing
;; (read-file ...) / (write-file ...) skills. `src/threadkeeper_budget.py` is
;; now just the SEAM that supplies live facts and executes the verdict.
;;
;; CONTRACT
;;   Facts in (supplied by the budget gate at decision time — NOT hardcoded here;
;;   the numbers live in threadkeeper.config.yaml, the logic lives here):
;;     $spent     cumulative tokens spent on this thread
;;     $ceiling   hard token ceiling for the thread
;;     $soft      soft threshold (ceiling * escalation_soft_fraction)
;;     $minlocal  min local iterations required before any escalation
;;     $iters     local (control/worker) iterations logged so far
;;     $hard      True/False — the caller flag that the subproblem is genuinely hard
;;   Verdict out:
;;     (allow "<reason>")  escalation to a cloud specialist is permitted
;;     (deny  "<reason>")  escalation denied; finish on cheap nodes or stop
;;
;; The reasons are human-readable and flow straight into the auditable
;; memory/escalations.jsonl trail (ISO/IEC 42001-friendly).
;; ════════════════════════════════════════════════════════════════════════

;; The policy, as ordered pattern-matching rules. Read top-to-bottom:
;;   1. iterate cheaply first   — too few local loops yet            -> deny
;;   2. budget exhausted        — at/over the hard ceiling           -> deny
;;   3. under soft threshold    — escalation is cheap vs the budget  -> allow
;;   4. hard subproblem         — between soft and hard, flagged hard-> allow
;;   5. otherwise               — between soft and hard, not hard     -> deny
(= (tk-escalate $spent $ceiling $soft $minlocal $iters $hard)
   (if (< $iters $minlocal)  (deny  "iterate cheaply first")
   (if (>= $spent $ceiling)  (deny  "budget exhausted")
   (if (< $spent $soft)      (allow "under soft threshold")
   (if (== $hard True)       (allow "subproblem flagged hard")
                             (deny  "not hard enough to justify spend"))))))
