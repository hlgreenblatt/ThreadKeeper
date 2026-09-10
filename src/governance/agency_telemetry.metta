;;; =======================================================================
;;; Mechanism 1 (ClarityOmega ingestion guide §4.2, §5.2):
;;; agency-balance telemetry — SHADOW MODE.
;;;
;;; Pure schema, reads, weighted-window computation, and verdicts.
;;; Writers live in agency_writers.metta (append/prune/clear-all discipline).
;;;
;;; Boundary contract (guide §6.3): MeTTa owns provenance classification and
;;; verdict semantics. Python transports authenticated identity metadata and
;;; canonical encodings only; it never decides origin or dependency.
;;;
;;; Atom schema (positional; guide §5.2 field order):
;;;   (decision-event $id $cid $cycle $origin $weight $action $sources $confidence)
;;;     $origin ∈ person | system | collaborative | unknown
;;;     $sources is a structured provenance term (see classify-origin)
;;;   (agency-balance $verdict $cid $person-w $system-w $collab-w $unknown-w)
;;;     $verdict ∈ healthy | dependency-risk | unknown-heavy | no-data
;;;
;;; Drift note (phase2-commit-reconciliation): the copied loop (93db08a) is a
;;; burst-model loop (&loops/&continueRequested/&nextWakeAt), so telemetry
;;; records around the existing receive/eval/send seams rather than assuming
;;; the guide-reviewed always-on cycle.
;;; =======================================================================

;;; Single queryable policy values (guide §6.2: one authoritative source,
;;; no duplicated constants in documentation and consuming expressions).
(= (agency-dependency-threshold) 0.6)
(= (agency-unknown-threshold) 0.5)
(= (agency-window-span-cycles) 40)

;;; -----------------------------------------------------------------------
;;; Origin classification from structured source links.
;;; Python/channel supplies authenticated facts; MeTTa derives origin.
;;; Explicit unknown for anything unauthenticated or unrecognized
;;; (guide §5.2: "Never infer identity by parsing display text").
;;; -----------------------------------------------------------------------
(= (classify-origin $sources)
   (case $sources
     (((human-message $chat-id $message-id) person)
      ((self-initiated $task-id) system)
      ((collaboration $message-id $task-id) collaborative)
      ($other unknown))))

;;; -----------------------------------------------------------------------
;;; Window reads over &self
;;; -----------------------------------------------------------------------
(= (sum-weights $xs)
   (if (== $xs ())
     0.0
     (let ($h $t) (decons $xs) (+ $h (sum-weights $t)))))

(= (origin-weight $cid $origin)
   (sum-weights
     (collapse (match &self
                 (decision-event $id $cid $k $origin $w $action $sources $conf)
                 $w))))

(= (event-count $cid)
   (size-atom
     (collapse (match &self
                 (decision-event $id $cid $k $o $w $a $s $c) $id))))

;;; -----------------------------------------------------------------------
;;; Guarded ratio (guide §4.2 shape, weighted): dependency-risk when the
;;; system share of person+system weight strictly exceeds the threshold.
;;; Collaborative weight is tracked and reported but not folded into the
;;; ratio; unknown-heavy is a separate guard so unauthenticated traffic can
;;; never masquerade as either side.
;;; -----------------------------------------------------------------------
(= (dependency-risk? $person-w $system-w)
   (let $total (+ $person-w $system-w)
     (if (> $total 0.0)
       (if (> (/ $system-w $total) (agency-dependency-threshold)) True False)
       False)))

(= (agency-verdict $cid)
   (let* (($pw (origin-weight $cid person))
          ($sw (origin-weight $cid system))
          ($cw (origin-weight $cid collaborative))
          ($uw (origin-weight $cid unknown))
          ($total (+ $pw (+ $sw (+ $cw $uw)))))
     (if (== $total 0.0)
       no-data
       (if (> (/ $uw $total) (agency-unknown-threshold))
         unknown-heavy
         (if (dependency-risk? $pw $sw) dependency-risk healthy)))))

;;; -----------------------------------------------------------------------
;;; Shadow-mode consumer contract (named consumer per ADR-005 / guide §6.2).
;;; The verdict may annotate interaction policy (offer options before the
;;; agent takes further irreversible decisions) but MUST NOT seize or return
;;; control and MUST NOT change ordinary execution (guide §4.2.3:
;;; "Telemetry should inform interaction, not automatically paternalistically
;;; seize or return control").
;;; -----------------------------------------------------------------------
(= (option-offering-policy $cid)
   (let $verdicts (collapse (match &self
                       (agency-balance $v $cid $pw $sw $cw $uw) $v))
     (if (== $verdicts ())
       (policy-error no-verdict-atom)        ; detector-without-state is an
                                             ; explicit error, never silent
       (let ($h $t) (decons $verdicts)
         (if (== $h dependency-risk) OFFER-OPTIONS NORMAL)))))
