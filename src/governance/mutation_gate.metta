;;; =======================================================================
;;; Mechanism 3: mutation lifecycle gate (guide §4.1, §5.4).
;;;
;;; Lifecycle: PENDING -> APPROVED -> COMMITTED | ERROR
;;;                 \-> DENIED (terminal)   \-> STALE (terminal)
;;;
;;; Improvements over the ClarityOmega source mechanism (guide §4.1.3):
;;; approval is bound to the FULL canonical-AST fingerprint plus policy
;;; version, not merely operation+head; a committed approval leaves no
;;; bypass token (replay requires a fresh proposal); deny wins over approve
;;; in the same batch; expiry is fail-closed at BOTH approval time and
;;; eval time.
;;;
;;; Boundary: fingerprints are opaque values supplied by the caller (the
;;; Python adapter hashes the canonical AST; guide §6.3: Python must not
;;; choose which fields constitute mutation identity — MeTTa passes the
;;; whole normalized AST). All transition legality is decided here.
;;;
;;; Proposal atom:
;;;   (mutation-proposal $id $cid $proposer $fp $pv $ast $created $expires $state)
;;;     $state ∈ PENDING | APPROVED | DENIED | STALE | COMMITTED | ERROR
;;; =======================================================================

;;; --- field accessors (pattern-LHS) --------------------------------------
(= (proposal-id-of        (mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)) $id)
(= (proposal-cid-of       (mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)) $cid)
(= (proposal-actor-of     (mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)) $p)
(= (proposal-fp-of        (mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)) $fp)
(= (proposal-expires-of   (mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)) $e)
(= (proposal-state-of     (mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)) $s)
(= (proposal-ast-of      (mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)) $ast)

(= (proposals-with-fp $cid $fp $state)
   (collapse (match &self
               (mutation-proposal $id $cid $p $fp $pv $ast $c $e $state)
               (mutation-proposal $id $cid $p $fp $pv $ast $c $e $state))))

;;; --- state writer: remove old atom, add same fields with new state ------
(= (set-proposal-state! $prop $new-state)
   (case $prop
     (((mutation-proposal $id $cid $p $fp $pv $ast $c $e $s)
        (progn (remove-atom &self $prop)
               (add-atom &self (mutation-proposal $id $cid $p $fp $pv $ast $c $e $new-state))
               True))
      ($other False))))

;;; --- pure eval decision --------------------------------------------------
;;; Fail-closed: any state other than an unexpired APPROVED exact-fingerprint
;;; match suppresses eval. Missing proposal, PENDING, DENIED, STALE,
;;; COMMITTED, ERROR, and expiry all suppress.
(= (eval-decision $cid $fp $k)
   (let $approved (proposals-with-fp $cid $fp APPROVED)
     (if (== $approved ())
       (suppress no-approved-exact-match)
       (let ($h $t) (decons $approved)
         (if (> $k (proposal-expires-of $h))
           (suppress approval-expired)
           (allow-approved (proposal-id-of $h)))))))

;;; --- lifecycle writers ----------------------------------------------------

(= (propose-mutation! $id $cid $proposer $fp $ast $k)
   (let $expires (+ $k (mutation-stale-cycles))
     (progn
       (add-atom &self
         (mutation-proposal $id $cid $proposer $fp (mutation-policy-version) $ast
                            $k $expires PENDING))
       (suppressed-pending $fp))))

;;; Approve: authorized approver + existing PENDING exact-fp proposal +
;;; not expired. Anything else is an explicit no-op result, never silent.
(= (approve-mutation! $cid $approver $fp $k)
   (if (authorized-approver? $approver)
     (let $pending (proposals-with-fp $cid $fp PENDING)
       (if (== $pending ())
         approval-noop-no-pending-proposal
         (let ($h $t) (decons $pending)
           (if (> $k (proposal-expires-of $h))
             (progn (set-proposal-state! $h STALE) approval-expired-marked-stale)
             (progn (set-proposal-state! $h APPROVED) (approved $fp))))))
     unauthorized-approver))

;;; Deny: PENDING or APPROVED proposals with the fp become DENIED.
(= (deny-xs! $xs)
   (if (== $xs ()) ()
     (let ($h $t) (decons $xs)
       (progn (set-proposal-state! $h DENIED) (deny-xs! $t)))))

(= (deny-mutation! $cid $fp)
   (progn (deny-xs! (proposals-with-fp $cid $fp PENDING))
          (deny-xs! (proposals-with-fp $cid $fp APPROVED))
          (denied $fp)))

;;; Batch rule: deny wins over approve in the same batch (guide §5.4 tests).
(= (resolve-approval-batch! $cid $approver $fp $deny-present $k)
   (if $deny-present
     (deny-mutation! $cid $fp)
     (approve-mutation! $cid $approver $fp $k)))

;;; Expiry sweep: PENDING/APPROVED past expires-cycle become STALE.
(= (expire-xs! $xs $k)
   (if (== $xs ()) ()
     (let ($h $t) (decons $xs)
       (progn
         (if (> $k (proposal-expires-of $h))
           (set-proposal-state! $h STALE)
           ())
         (expire-xs! $t $k)))))

(= (expire-stale-proposals! $cid $k)
   (let $pending (collapse (match &self
                     (mutation-proposal $id $cid $p $fp $pv $ast $c $e PENDING)
                     (mutation-proposal $id $cid $p $fp $pv $ast $c $e PENDING)))
     (let $approved (collapse (match &self
                       (mutation-proposal $id $cid $p $fp $pv $ast $c $e APPROVED)
                       (mutation-proposal $id $cid $p $fp $pv $ast $c $e APPROVED)))
       (progn (expire-xs! $pending $k)
              (expire-xs! $approved $k)
              expiry-sweep-done))))

;;; --- governed applier (single writer of the protected registry) --------
;;; Runtime PeTTa note (verified 2026-07-20): (eval (= ...)) does NOT
;;; define a callable predicate in this runtime, so the applier additionally
;;; (a) records the definition in the protected-definition registry
;;; (signature-visible) and (b) registers + asserts the value fact so the
;;; live predicate probe observes it. All import! loading must complete
;;; before any application: runtime register_fun affects later translations.
(= (flatten-lhs-body $lhs $body)
   (if (== $lhs ()) ($body)
     (let ($h $t) (decons $lhs) (cons-atom $h (flatten-lhs-body $t $body)))))

(= (apply-approved-mutation! $cmd)
   (let* (($rest (cdr-atom $cmd))
          ($lhs (car-atom $rest))
          ($body (car-atom (cdr-atom $rest)))
          ($head (head-of-lhs $lhs)))
     (progn (add-atom &self (protected-definition $head $cmd))
            (import_prolog_function $head)
            (assertzPredicate (Predicate (flatten-lhs-body $lhs $body)))
            (applied $head))))

;;; Terminal transitions. Both COMMITTED and ERROR leave no live approval:
;;; replaying the same command afterwards requires a fresh proposal.
(= (commit-mutation! $cid $fp $k $before-sig $after-sig $cap-delta $evidence $source-commit)
   (let $approved (proposals-with-fp $cid $fp APPROVED)
     (if (== $approved ())
       commit-noop-no-approved
       (let ($h $t) (decons $approved)
         (progn (set-proposal-state! $h COMMITTED)
                (append-continuity-event! $cid $k (proposal-actor-of $h)
                                          (proposal-ast-of $h) $fp
                                          $before-sig $after-sig $cap-delta
                                          $evidence committed $source-commit)
                (committed $fp))))))

(= (error-mutation! $cid $fp $k $before-sig $after-sig $cap-delta $evidence $source-commit)
   (let $approved (proposals-with-fp $cid $fp APPROVED)
     (if (== $approved ())
       error-noop-no-approved
       (let ($h $t) (decons $approved)
         (progn (set-proposal-state! $h ERROR)
                (append-continuity-event! $cid $k (proposal-actor-of $h)
                                          (proposal-ast-of $h) $fp
                                          $before-sig $after-sig $cap-delta
                                          $evidence error $source-commit)
                (errored $fp))))))
