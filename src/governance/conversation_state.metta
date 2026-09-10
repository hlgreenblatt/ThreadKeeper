;;; =======================================================================
;;; Mechanism 2 (ClarityOmega ingestion guide §4.3, §5.3):
;;; conversation-scoped state atoms for the aliveness gate.
;;;
;;; Single canonical state shape (guide §6.2: "Do not retain multiple state
;;; schema versions live"). Scoped by conversation-id; never global.
;;;
;;;   (conversation-state $cid $state $version $last-event)
;;;     $state ∈ IDLE | ENGAGED | COMPLETING
;;;   (terminal-owed $cid $task-id $event-id)
;;;     exactly one terminal emission is owed (guide §4.3.3: COMPLETING must
;;;     mean "one terminal emission is owed", not another permissive ENGAGE)
;;;   (terminal-sent $cid $event-id)
;;;     durable exactly-once consumption record (retry safety)
;;;   (aliveness-diagnostic $cid $kind $detail)
;;;     unknown-state fail-silent diagnostics (guide §4.3.3)
;;;
;;; Drift note (phase2-commit-reconciliation): the copied loop (93db08a)
;;; already starts quiet (&loops=0) with a burst-continuation latch
;;; (&continueRequested) and wall-clock wake (&nextWakeAt). This state
;;; machine is the semantic authority; the loop's scheduler atoms are the
;;; actuator it drives (integration note in the experiment RUN.md).
;;; =======================================================================

(= (conversation-state-of $cid)
   (collapse (match &self
               (conversation-state $cid $s $v $e)
               (conversation-state $cid $s $v $e))))

;;; Missing state is NOT false: distinguish missing from each real state
;;; (guide §6.2 anti-pattern list).
(= (conversation-state-or-missing $cid)
   (let $found (conversation-state-of $cid)
     (if (== $found ()) missing (let ($h $t) (decons $found) $h))))

(= (state-name-of (conversation-state $cid $s $v $e)) $s)
(= (state-version-of (conversation-state $cid $s $v $e)) $v)
(= (state-event-of (conversation-state $cid $s $v $e)) $e)

(= (terminal-owed-of $cid)
   (collapse (match &self (terminal-owed $cid $task $event) (terminal-owed $cid $task $event))))

(= (terminal-already-sent? $cid $event-id)
   (let $found (collapse (match &self (terminal-sent $cid $event-id) True))
     (if (== $found ()) False True)))

;;; --- scoped writers (clear-all for the cid, then one fresh atom) -------
(= (remove-all-cs! $xs)
   (if (== $xs ()) ()
     (let ($h $t) (decons $xs)
       (progn (remove-atom &self $h) (remove-all-cs! $t)))))

(= (set-conversation-state! $cid $state $version $last-event)
   (progn
     (remove-all-cs! (conversation-state-of $cid))
     (add-atom &self (conversation-state $cid $state $version $last-event))))

(= (clear-terminal-owed! $cid)
   (remove-all-cs! (terminal-owed-of $cid)))

(= (record-terminal-sent! $cid $event-id)
   (add-atom &self (terminal-sent $cid $event-id)))

(= (record-aliveness-diagnostic! $cid $kind $detail)
   (add-atom &self (aliveness-diagnostic $cid $kind $detail)))
