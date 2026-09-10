;;; =======================================================================
;;; Mechanism 1: agency telemetry writers — append / prune / clear-all.
;;;
;;; ADR-005 discipline: the verdict writer clears ALL agency-balance atoms
;;; for the conversation before writing one fresh atom. The earlier
;;; ClarityOmega clear-one pattern left stale bootstrap atoms for 2200+
;;; cycles; clear-all is the regression fix and is tested here.
;;; =======================================================================

(= (record-decision-event! $id $cid $cycle $origin $weight $action $sources $confidence)
   (add-atom &self
     (decision-event $id $cid $cycle $origin $weight $action $sources $confidence)))

(= (remove-all! $xs)
   (if (== $xs ())
     ()
     (let ($h $t) (decons $xs)
       (progn (remove-atom &self $h) (remove-all! $t)))))

;;; Span-bounded pruning: remove every event of $cid older than $cutoff.
;;; Deterministic and bounded; called once per cycle with a moving cutoff
;;; derived from (agency-window-span-cycles).
(= (event-cycle-of (decision-event $id $cid $k $o $w $a $s $c)) $k)

(= (prune-xs-before! $xs $cutoff)
   (if (== $xs ())
     ()
     (let ($h $t) (decons $xs)
       (progn
         (if (< (event-cycle-of $h) $cutoff)
           (remove-atom &self $h)
           ())
         (prune-xs-before! $t $cutoff)))))

(= (prune-decision-events-before! $cid $cutoff)
   (prune-xs-before!
     (collapse (match &self
                 (decision-event $id $cid $k $o $w $a $s $c)
                 (decision-event $id $cid $k $o $w $a $s $c)))
     $cutoff))

(= (clear-agency-balance! $cid)
   (remove-all!
     (collapse (match &self
                 (agency-balance $v $cid $pw $sw $cw $uw)
                 (agency-balance $v $cid $pw $sw $cw $uw)))))

;;; One fresh verdict atom per write; stale atoms cannot accumulate.
(= (write-agency-balance! $cid)
   (let* (($pw (origin-weight $cid person))
          ($sw (origin-weight $cid system))
          ($cw (origin-weight $cid collaborative))
          ($uw (origin-weight $cid unknown))
          ($v  (agency-verdict $cid)))
     (progn
       (clear-agency-balance! $cid)
       (add-atom &self (agency-balance $v $cid $pw $sw $cw $uw)))))
