;;; =======================================================================
;;; Mechanism 3 (ClarityOmega ingestion guide §4.1, §5.4):
;;; mutation policy — protected namespaces/effects and authorization.
;;;
;;; Single queryable policy values (guide §6.2). Approver identity is an
;;; authenticated channel ID, never a display name parsed from message text
;;; (guide §4.1.3: "Authorization must be channel-bound").
;;; =======================================================================

(= (mutation-policy-version) pv-2026-07-20-1)
(= (mutation-stale-cycles) 20)

;;; Protected heads: definitions of these may only reach eval through the
;;; mutation gate. Includes the governance seams themselves.
(= (protected-heads)
   (core-value
    authorized-approvers
    agency-dependency-threshold
    agency-unknown-threshold
    aliveness-gate
    mutation-policy-version
    mutation-stale-cycles
    load-extensions))

(= (protected-head? $h) (is-member $h (protected-heads)))

;;; Authenticated approver identities (channel-bound stable IDs).
(= (authorized-approver-ids) (admin-402314199))
(= (authorized-approver? $id) (is-member $id (authorized-approver-ids)))

;;; Command-effect classification at the eval seam.
;;;   protected-mutation | benign
;;; The (metta ...) skill is an indirect mutation path and is classified by
;;; its payload (guide §5.4: skills must not bypass the gate indirectly).
;;; Extension installation is a protected effect surface (drift item 5 of
;;; the commit reconciliation: load-extensions entered the copied init).
(= (head-of-lhs $lhs)
   (if (is-expr $lhs) (car-atom $lhs) $lhs))

;;; NB: `=` cannot be matched as a case pattern literal in PeTTa (it
;;; compiles to unification), so classification is structural: deconstruct
;;; and compare the head symbol against the parsed equals symbol.
(= (eq-symbol) (sread "="))

(= (classify-effect $cmd)
   (if (is-expr $cmd)
     (let ($h $rest) (decons $cmd)
       (if (== $h (eq-symbol))
         (let ($lhs $body-tail) (decons $rest)
           (if (protected-head? (head-of-lhs $lhs)) protected-mutation benign))
         (if (== $h metta)
           (let ($inner $tail) (decons $rest) (classify-effect $inner))
           (if (== $h load-extensions)
             protected-mutation
             (if (== $h load-extension)
               protected-mutation
               benign)))))
     benign))
