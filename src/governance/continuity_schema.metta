;;; =======================================================================
;;; UNIFIED continuity ledger schema and validation.
;;;
;;; Reconciles Mechanism 3 (mutation gate + continuity ledger, guide §4.3)
;;; and Mechanism 5 (provenance-aware continuity records, guide §4.5).
;;;
;;; Append-only event chain. Every committed self-change records sequence,
;;; event id, parent, cycle, conversation id, actor, proposal, fingerprint,
;;; before/after structural signatures, capability delta, evidence, result,
;;; and source commit — linked by parent event id.
;;;
;;; Genesis convention: exactly one event, the first, has parent `genesis`.
;;;
;;; Unified continuity-event (14 fields):
;;;   (continuity-event $seq $eid $parent $cycle $cid $actor $proposal
;;;                     $proposal-fp $before-sig $after-sig
;;;                     $capability-delta $evidence $result $source-commit)
;;; =======================================================================

(= (init-continuity!)
   (progn (bind! &continuity-counter (new-state 0))
          (bind! &continuity-last (new-state genesis))
          continuity-ready))

(= (append-continuity-event! $cid $k $actor $proposal $proposal-fp $before-sig $after-sig $cap-delta $evidence $result $source-commit)
   (let* (($n (+ 1 (get-state &continuity-counter)))
          ($eid (cev $n))
          ($parent (get-state &continuity-last)))
     (progn (change-state! &continuity-counter $n)
            (add-atom &self
              (continuity-event $n $eid $parent $k $cid $actor $proposal
                                $proposal-fp $before-sig $after-sig
                                $cap-delta $evidence $result $source-commit))
            (change-state! &continuity-last $eid)
            $eid)))

;;; ---- accessors (positional, single source of truth) -------------------

(= (event-seq      (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $seq)
(= (event-eid      (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $eid)
(= (event-parent   (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $p)
(= (event-cycle    (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $k)
(= (event-cid      (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $cid)
(= (event-actor    (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $a)
(= (event-proposal (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $prop)
(= (event-fp       (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $fp)
(= (event-before   (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $b)
(= (event-after    (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $af)
(= (event-delta    (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $d)
(= (event-evidence (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $ev)
(= (event-result   (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $r)
(= (event-commit   (continuity-event $seq $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)) $sc)

(= (continuity-event-of-seq $n)
   (collapse (match &self
               (continuity-event $n $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc)
               (continuity-event $n $eid $p $k $cid $a $prop $fp $b $af $d $ev $r $sc))))

;;; ---- chain validation ------------------------------------------------
;;; Event 1 must have parent genesis; event n must have parent (cev n-1).
;;; Any break fails closed with (chain-broken $n).

(= (validate-chain-step $n)
   (let $found (continuity-event-of-seq $n)
     (if (== $found ())
       (chain-missing $n)
       (let ($h $t) (decons $found)
         (if (== $n 1)
           (if (== (event-parent $h) genesis) chain-ok (chain-broken $n))
           (if (== (event-parent $h) (cev (- $n 1))) chain-ok (chain-broken $n)))))))

(= (validate-continuity-chain $n)
   (if (== $n 0)
     chain-ok
     (let $rest (validate-continuity-chain (- $n 1))
       (if (== $rest chain-ok)
         (validate-chain-step $n)
         $rest))))

(= (validate-full-chain)
   (validate-continuity-chain (get-state &continuity-counter)))

;;; ---- startup lineage check -------------------------------------------
;;; The current structural signature must equal the latest ledger
;;; after-signature. With no events, only the exact genesis signature is
;;; acceptable; anything else is an untracked-state break.

(= (latest-after-signature)
   (let $n (get-state &continuity-counter)
     (if (== $n 0)
       no-events
       (let $found (continuity-event-of-seq $n)
         (if (== $found ())
           no-events
           (let ($h $t) (decons $found) (event-after $h)))))))

(= (lineage-check $current-sig)
   (let $latest (latest-after-signature)
     (if (== $latest no-events)
       lineage-break-untracked-state
       (if (== $latest $current-sig)
         lineage-ok
         (lineage-break $latest $current-sig)))))

;;; ---- graded self-continuity (from Mech 5) ----------------------------
;;; Continuity is a DEGREE of structural persistence between successive
;;; snapshots. Snapshots are canonical element sets; the score is
;;; |intersection| / |union|.

(= (self-continuity-score $elements1 $elements2)
   (let* (($inter (size-atom (intersection-atom $elements1 $elements2)))
          ($union (- (+ (size-atom $elements1) (size-atom $elements2)) $inter)))
         (if (== $union 0)
             1.0
             (/ (* 1.0 $inter) $union))))

(= (chain-continuity-bound $scores)
   (foldl-atom $scores 1.0 *))

;;; ---- capability-delta reconstruction (from Mech 5) -------------------
;;; Fold deltas in append order from the empty registry.
;;; Set semantics: add is idempotent, remove drops if present.

(= (apply-delta $registry $delta)
   (if (== (size-atom $delta) 0)
       $registry
       (let ($op $cap $ver) (car-atom $delta)
            (if (== $op add)
                (if (is-member ($cap $ver) $registry)
                    (apply-delta $registry (cdr-atom $delta))
                    (apply-delta (cons-atom ($cap $ver) $registry)
                                 (cdr-atom $delta)))
                (apply-delta (exclude-item ($cap $ver) $registry)
                             (cdr-atom $delta))))))

(= (apply-event-delta $registry $event)
   (apply-delta $registry (event-delta $event)))

(= (reconstruct-registry $events)
   (foldl-atom $events () apply-event-delta))

;;; ---- event validation (from Mech 5, unified) --------------------------

(= (valid-result $r)
   (is-member $r (committed error rolled-back)))

(= (valid-delta $delta)
   (if (== (size-atom $delta) 0)
       True
       (let $entry (car-atom $delta)
            (if (is-expr $entry)
                (if (== (size-atom $entry) 3)
                    (if (is-member (car-atom $entry) (add remove))
                        (valid-delta (cdr-atom $delta))
                        False)
                    False)
                False))))

(= (validate-event $event)
   (if (not (valid-result (event-result $event)))
       (invalid result)
       (if (is-member () ((event-actor $event)
                          (event-cid $event)
                          (event-proposal $event)
                          (event-fp $event)
                          (event-before $event)
                          (event-after $event)
                          (event-commit $event)))
           (invalid empty-required-field)
           (if (not (valid-delta (event-delta $event)))
               (invalid capability-delta)
               (valid (event-eid $event))))))
