;;; =======================================================================
;;; Mechanism 3: structural signature (guide §4.5, §5.4).
;;;
;;; The protected snapshot is a LIVE PROBE of the actual (= ...) definitions
;;; of each protected head in &self (PeTTa match translates an = pattern
;;; into a predicate call, so a fully generic all-definitions query is not
;;; expressible; the collectors below enumerate the protected heads and
;;; their arities explicitly). This observes real state rather than a
;;; self-described mirror (guide §3.2: no autobiography).
;;;
;;; Canonical byte encoding, ordering canonicalization, and hashing are the
;;; Python boundary's job; selection of WHAT is protected is MeTTa's job.
;;;
;;; Integration note: when protected-heads or their arities change, these
;;; collectors must be regenerated from the policy list (the integrator
;;; adds this generation step; the protected-head list is the single
;;; source).
;;; =======================================================================

(= (snapshot-core-value)
   (concat-xs (collapse (match &self (= (core-value $a) $b) (quote (= (core-value $a) $b)))) (collapse (match &self (= (core-value $a $b) $c) (quote (= (core-value $a $b) $c))))))
(= (snapshot-authorized-approvers)
   (concat-xs (collapse (match &self (= (authorized-approvers) $b) (quote (= (authorized-approvers) $b)))) (collapse (match &self (= (authorized-approvers $a) $b) (quote (= (authorized-approvers $a) $b))))))
(= (snapshot-agency-dependency-threshold)
   (collapse (match &self (= (agency-dependency-threshold) $b) (quote (= (agency-dependency-threshold) $b)))))
(= (snapshot-agency-unknown-threshold)
   (collapse (match &self (= (agency-unknown-threshold) $b) (quote (= (agency-unknown-threshold) $b)))))
(= (snapshot-aliveness-gate)
   (concat-xs (collapse (match &self (= (aliveness-gate $a $b $c) $d) (quote (= (aliveness-gate $a $b $c) $d)))) (concat-xs (collapse (match &self (= (aliveness-gate $a $b) $c) (quote (= (aliveness-gate $a $b) $c)))) (collapse (match &self (= (aliveness-gate $a) $b) (quote (= (aliveness-gate $a) $b)))))))
(= (snapshot-mutation-policy-version)
   (collapse (match &self (= (mutation-policy-version) $b) (quote (= (mutation-policy-version) $b)))))
(= (snapshot-mutation-stale-cycles)
   (collapse (match &self (= (mutation-stale-cycles) $b) (quote (= (mutation-stale-cycles) $b)))))
(= (snapshot-load-extensions)
   (collapse (match &self (= (load-extensions) $b) (quote (= (load-extensions) $b)))))

;;; Registry atoms written ONLY by the governed applier (mutation_gate
;;; apply path). Including them makes unauthorized registry writes visible
;;; as signature drift at the next lineage check.
(= (snapshot-registry)
   (collapse (match &self (protected-definition $head $ast)
                    (protected-definition $head $ast))))

(= (concat-xs $a $b)
   (if (== $a ()) $b (let ($h $t) (decons $a) (cons-atom $h (concat-xs $t $b)))))

(= (protected-snapshot)
   (concat-xs (snapshot-core-value)
     (concat-xs (snapshot-authorized-approvers)
       (concat-xs (snapshot-agency-dependency-threshold)
         (concat-xs (snapshot-agency-unknown-threshold)
           (concat-xs (snapshot-aliveness-gate)
             (concat-xs (snapshot-mutation-policy-version)
               (concat-xs (snapshot-mutation-stale-cycles)
                 (concat-xs (snapshot-load-extensions)
                            (snapshot-registry))))))))))

;;; Signature request: MeTTa supplies the selected snapshot and the policy
;;; version; the Python adapter canonicalizes ordering and bytes and
;;; returns the digest. MeTTa performs digest equality checks.
(= (request-structural-signature)
   (py-call (mech3_mutation_boundary_adapter.signature_of_terms
              (protected-snapshot) (mutation-policy-version))))
