;;; =======================================================================
;;; Mechanism 2: aliveness and terminal-state gating — single canonical
;;; state machine (guide §4.3, §5.3).
;;;
;;; Pure gate: typed decision SILENT | ENGAGE | EMIT-TERMINAL, made after
;;; receive/scheduler state is known and before prompt construction.
;;; "Do nothing" is a first-class terminal action: SILENT must prevent the
;;; provider call, not merely prompt the LLM not to speak.
;;;
;;; Lifecycle: IDLE -> ENGAGED -> COMPLETING -> IDLE.
;;;   new message        IDLE      -> ENGAGED  (gate ENGAGE)
;;;   async work pending ENGAGED   -> ENGAGED  (gate ENGAGE, no new message)
;;;   final result ready ENGAGED   -> COMPLETING (terminal owed)
;;;   terminal sent ok   COMPLETING-> IDLE     (exactly once)
;;; Unknown/missing state fails SILENT and requires a diagnostic record.
;;; =======================================================================

;;; -----------------------------------------------------------------------
;;; Pure gate truth table.
;;;   $msgnew ∈ True|False   $idle-directive ∈ True|False
;;; An idle directive forces ONE ENGAGE cycle to process the directive
;;; (guide §4.3.1) regardless of latch state.
;;; -----------------------------------------------------------------------
(= (aliveness-gate $cid $msgnew $idle-directive)
   (if $idle-directive
     ENGAGE
     (if $msgnew
       ENGAGE
       (aliveness-gate-state $cid (conversation-state-or-missing $cid)))))

(= (aliveness-gate-state $cid $state-atom)
   (case $state-atom
     ((missing SILENT)                       ; no message, no state: fail silent
      ((conversation-state $c IDLE $v $e) SILENT)
      ((conversation-state $c ENGAGED $v $e) ENGAGE)
      ((conversation-state $c COMPLETING $v $e) EMIT-TERMINAL)
      ($other SILENT))))                    ; unknown state: fail silent

;;; Gate with audit: unknown/missing silence emits a diagnostic atom
;;; (guide §4.3.3). Writers, not the pure gate, perform the record.
(= (aliveness-gate-audited! $cid $msgnew $idle-directive $cycle)
   (let $decision (aliveness-gate $cid $msgnew $idle-directive)
     (progn
       (let $state-atom (conversation-state-or-missing $cid)
         (case $state-atom
           ((missing (record-aliveness-diagnostic! $cid missing-state $cycle))
            ((conversation-state $c IDLE $v $e) ())
            ((conversation-state $c ENGAGED $v $e) ())
            ((conversation-state $c COMPLETING $v $e) ())
            ($other (record-aliveness-diagnostic! $cid unknown-state $cycle)))))
       $decision)))

;;; -----------------------------------------------------------------------
;;; Guarded transitions. Each returns True on the legal transition it
;;; performed, False otherwise; no transition is ever silently coerced.
;;; Version increments on every successful transition (audit/optimistic
;;; concurrency).
;;; -----------------------------------------------------------------------

;;; New message: IDLE -> ENGAGED; ENGAGED -> ENGAGED (refresh event);
;;; COMPLETING stays COMPLETING (terminal is still owed) but the event is
;;; recorded so the next gate cycle re-engages after emission.
(= (transition-new-message! $cid $event-id)
   (let $cur (conversation-state-or-missing $cid)
     (case $cur
       ((missing
          (progn (set-conversation-state! $cid ENGAGED 1 $event-id) True))
        ((conversation-state $c IDLE $v $e)
          (progn (set-conversation-state! $cid ENGAGED (+ $v 1) $event-id) True))
        ((conversation-state $c ENGAGED $v $e)
          (progn (set-conversation-state! $cid ENGAGED (+ $v 1) $event-id) True))
        ((conversation-state $c COMPLETING $v $e)
          (progn (set-conversation-state! $cid COMPLETING (+ $v 1) $event-id) True))
        ($other False)))))

;;; Skill begins asynchronous work: ENGAGED stays ENGAGED (legal);
;;; from IDLE without a message this is forbidden (idle creative work must
;;; be an explicit scheduled task, not an accident of state — guide §5.3).
(= (transition-begin-async! $cid $event-id)
   (let $cur (conversation-state-or-missing $cid)
     (case $cur
       (((conversation-state $c ENGAGED $v $e)
          (progn (set-conversation-state! $cid ENGAGED (+ $v 1) $event-id) True))
        ($other False)))))

;;; Final result ready: ENGAGED -> COMPLETING with exactly one terminal
;;; owed. Forbidden from IDLE/COMPLETING (a second completion would owe a
;;; second terminal for the same task).
(= (transition-mark-completing! $cid $task-id $event-id)
   (let $cur (conversation-state-or-missing $cid)
     (case $cur
       (((conversation-state $c ENGAGED $v $e)
          (progn (set-conversation-state! $cid COMPLETING (+ $v 1) $event-id)
                 (clear-terminal-owed! $cid)
                 (add-atom &self (terminal-owed $cid $task-id $event-id))
                 True))
        ($other False)))))

;;; Consume the owed terminal exactly once. Succeeds only in COMPLETING
;;; with a matching owed event that was not already sent; records the
;;; durable terminal-sent marker and returns to IDLE. A retry with the same
;;; event-id returns False and MUST NOT send again (idempotence).
(= (transition-consume-terminal! $cid $event-id)
   (let $cur (conversation-state-or-missing $cid)
     (case $cur
       (((conversation-state $c COMPLETING $v $e)
          (let $owed (terminal-owed-of $cid)
            (if (== $owed ())
              False
              (let ($h $t) (decons $owed)
                (case $h
                  (((terminal-owed $c2 $task $event-id)
                     (if (terminal-already-sent? $cid $event-id)
                       False
                       (progn (record-terminal-sent! $cid $event-id)
                              (clear-terminal-owed! $cid)
                              (set-conversation-state! $cid IDLE (+ $v 1) $event-id)
                              True)))
                   ($other False)))))))
        ($other False)))))

;;; Idle directive: ENGAGED -> IDLE (the gate already forced one ENGAGE
;;; cycle to process the directive itself). Forbidden from COMPLETING:
;;; a terminal emission is still owed and must not be dropped.
(= (transition-idle-directive! $cid $event-id)
   (let $cur (conversation-state-or-missing $cid)
     (case $cur
       (((conversation-state $c ENGAGED $v $e)
          (progn (set-conversation-state! $cid IDLE (+ $v 1) $event-id) True))
        ($other False)))))
