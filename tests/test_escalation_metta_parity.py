"""Parity test: the MeTTa escalation policy must match the Python spec.

ThreadKeeper's routing/escalation decision now lives in `src/escalation.metta`
(Atomspace rules), evaluated through OmegaClaw's MeTTa runtime (PeTTa) by
`BudgetTracker.should_escalate`. This test proves the lift to MeTTa changed the
*substrate*, not the *behavior*: across a grid covering every branch and
boundary, the MeTTa verdict equals the original Python decision — and every
verdict is served by the MeTTa path (reason tagged ``[metta]``).

Run inside the agent runtime (where `petta` is importable):

    python3 tests/test_escalation_metta_parity.py

If PeTTa is not importable (e.g. a plain host / CI without the runtime), the
test SKIPS rather than fails — the production code path is designed to fall
back to the equivalent Python rules in exactly that situation, and that
fallback is covered by simply running `python3 src/threadkeeper_budget.py`.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "src"))

# Budget constants must mirror threadkeeper.config.yaml > budget.
CEILING = 2_000_000
SOFT = 1_000_000
MIN_LOCAL = 2


def python_reference(spent, iters, hard):
    """Independent restatement of the legacy Python rules — the spec the
    MeTTa policy must reproduce exactly."""
    if iters < MIN_LOCAL:
        return (False, "iterate")
    if spent >= CEILING:
        return (False, "budget")
    if spent < SOFT:
        return (True, "soft")
    if hard:
        return (True, "hard")
    return (False, "nothard")


def main():
    import threadkeeper_budget as tb

    # Construct against the repo config + a scratch escalation log.
    bt = tb.BudgetTracker(
        usage_log=os.path.join(_REPO, "memory", "usage.jsonl"),
        escalation_log="/tmp/tk_parity_escalations.jsonl",
    )

    engine = bt._policy._ensure()
    if engine is None:
        print("SKIP: PeTTa MeTTa runtime not available — "
              "production falls back to Python rules here (see "
              "`python3 src/threadkeeper_budget.py`).")
        return 0

    grid = [
        (spent, iters, hard)
        for spent in (0, 10, SOFT - 1, SOFT, SOFT + 1, CEILING - 1, CEILING, CEILING + 10)
        for iters in (0, 1, 2, 5)
        for hard in (True, False)
    ]

    mismatches = 0
    for spent, iters, hard in grid:
        bt.spent_tokens = lambda tid="default", s=spent: s
        bt.local_iterations = lambda tid="default", i=iters: i
        d = bt.should_escalate(thread_id="default", subproblem_is_hard=hard)
        ref_allowed, ref_branch = python_reference(spent, iters, hard)
        used_metta = d.reason.startswith("[metta]")
        if d.allowed != ref_allowed or not used_metta:
            mismatches += 1
            print(f"MISMATCH spent={spent} iters={iters} hard={hard} | "
                  f"metta={used_metta} got={d.allowed} ref={ref_allowed} "
                  f"({ref_branch}) | {d.reason[:60]}")

    print(f"cases={len(grid)} mismatches={mismatches}")
    if mismatches:
        print("PARITY: FAIL")
        return 1
    print("PARITY: PASS — MeTTa policy == Python spec, all served via [metta]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
