"""Synthetic checks of declared edges, reachability, and refusal paths."""
from __future__ import annotations

import unittest

from pulse_trader.graph.states import (
    INITIAL_STATE,
    TRANSITIONS,
    OPEN_ORDERS_UNREADABLE_NOTE,
    UNAPPROVED_POSITIONS_NOTE,
    ConsumeRunState,
    DecisionCode,
    GuardianTickState,
    ProposalState,
    ProtectionState,
    ProtectiveOrderState,
    PulseStep,
    ReservationState,
    SlotState,
    StateError,
    assert_transition,
    is_terminal,
    machines,
    reachable,
    slot_terminal_states,
    successors,
    terminal_states,
    unapproved_position_ids,
)

R = ReservationState
P = ProtectiveOrderState


def table(machine: type) -> dict[str, set[str]]:
    return {state.value: {nxt.value for nxt in successors(state)} for state in machine}


class Phase2EdgeTests(unittest.TestCase):
    def test_replacing_successor_set_is_exact(self) -> None:
        self.assertEqual(successors(P.REPLACING), frozenset({
            P.OPEN, P.REFUSED, P.TRIGGERED_FILLED, P.CANCELLED_ORPHAN, P.CANCELLED_BY_LIQUIDATION,
        }))
        for target in (P.TRIGGERED_FILLED, P.CANCELLED_ORPHAN, P.CANCELLED_BY_LIQUIDATION, P.OPEN, P.REFUSED):
            self.assertIs(assert_transition(P.REPLACING, target), target)
        with self.assertRaises(StateError):
            assert_transition(P.REPLACING, P.SUBMITTED)
        with self.assertRaises(StateError):
            assert_transition(P.REPLACING, P.REJECTED_FAIR_VALUE)

    def test_uncertain_successor_set_is_exact(self) -> None:
        self.assertEqual(successors(R.UNCERTAIN), frozenset({
            R.FILLED, R.PARTIAL, R.REFUSED, R.ABANDONED_UNVERIFIED, R.HOLD_FOR_HUMAN, R.SUBMITTED,
        }))
        self.assertIs(assert_transition(R.UNCERTAIN, R.SUBMITTED), R.SUBMITTED)
        with self.assertRaises(StateError):
            assert_transition(R.UNCERTAIN, R.SUBMITTING)
        with self.assertRaises(StateError):
            assert_transition(R.UNCERTAIN, R.SETTLED)

    def test_submitted_can_return_to_uncertain_and_back_only_through_declared_edges(self) -> None:
        self.assertIn(R.UNCERTAIN, successors(R.SUBMITTED))
        self.assertNotIn(R.SUBMITTING, successors(R.SUBMITTED))
        self.assertNotIn(R.RESERVED, successors(R.UNCERTAIN))

    def test_protective_order_path_to_triggered_filled(self) -> None:
        for path in ((P.PLANNED, P.SUBMITTED, P.OPEN, P.TRIGGERED_FILLED),
                     (P.PLANNED, P.SUBMITTED, P.OPEN, P.REPLACING, P.TRIGGERED_FILLED),
                     (P.PLANNED, P.SUBMITTED, P.OPEN, P.REPLACING, P.CANCELLED_ORPHAN),
                     (P.PLANNED, P.SUBMITTED, P.OPEN, P.REPLACING, P.CANCELLED_BY_LIQUIDATION),
                     (P.PLANNED, P.SUBMITTED, P.OPEN, P.REPLACING, P.OPEN, P.CANCELLED_ORPHAN)):
            for current, nxt in zip(path, path[1:]):
                assert_transition(current, nxt)
        with self.assertRaises(StateError):
            assert_transition(P.PLANNED, P.TRIGGERED_FILLED)
        with self.assertRaises(StateError):
            assert_transition(P.SUBMITTED, P.TRIGGERED_FILLED)


class PinnedTableTests(unittest.TestCase):
    """No other edge is added in Phase 2: every table is pinned by value."""

    def test_slot_table(self) -> None:
        self.assertEqual(table(SlotState), {
            "SCHEDULED": {"ATTEMPTED", "DID_NOT_RUN"},
            "ATTEMPTED": {"RUNNING", "LATE_SKIPPED", "LOCK_BUSY", "DB_BUSY", "REFUSED", "ABANDONED"},
            "RUNNING": {"COMPLETED", "COMPLETED_PROTECTION_INCOMPLETE", "REFUSED", "DB_BUSY", "ABANDONED"},
            "COMPLETED": set(), "COMPLETED_PROTECTION_INCOMPLETE": set(), "REFUSED": set(), "LATE_SKIPPED": set(),
            "LOCK_BUSY": set(), "DB_BUSY": set(), "ABANDONED": set(), "DID_NOT_RUN": set(),
        })
        self.assertEqual(slot_terminal_states(), terminal_states(SlotState))

    def test_pulse_step_table(self) -> None:
        self.assertEqual(table(PulseStep), {
            "ATTEMPTED": {"POLICY_LOADED", "REFUSED"}, "POLICY_LOADED": {"CLOCK_CHECKED", "REFUSED"},
            "CLOCK_CHECKED": {"LOCK_ACQUIRED", "REFUSED"}, "LOCK_ACQUIRED": {"SNAPSHOT_CAPTURED", "REFUSED"},
            "SNAPSHOT_CAPTURED": {"RECONCILED", "REFUSED"}, "RECONCILED": {"PROTECTION_EVALUATED"},
            "PROTECTION_EVALUATED": {"PROTECTION_EXECUTED"}, "PROTECTION_EXECUTED": {"EVIDENCE_LOADED"},
            "EVIDENCE_LOADED": {"ENTRIES_EVALUATED"}, "ENTRIES_EVALUATED": {"ENTRIES_ROUTED"},
            "ENTRIES_ROUTED": {"ENTRIES_EXECUTED", "FINALIZED"}, "ENTRIES_EXECUTED": {"FINALIZED"},
            "FINALIZED": set(), "REFUSED": set(),
        })

    def test_reservation_table(self) -> None:
        self.assertEqual(table(ReservationState), {
            "RESERVED": {"REQUEST_MATERIALIZED", "PREVIEW_REFUSED", "PREVIEW_ONLY", "ABANDONED_NOT_SUBMITTED"},
            "REQUEST_MATERIALIZED": {"SUBMITTING", "PREVIEW_ONLY", "ABANDONED_NOT_SUBMITTED"},
            "SUBMITTING": {"SUBMITTED", "REFUSED", "UNCERTAIN", "FILLED", "PARTIAL", "ABANDONED_NOT_SUBMITTED",
                           "ABANDONED_UNVERIFIED", "HOLD_FOR_HUMAN"},
            "SUBMITTED": {"FILLED", "PARTIAL", "REFUSED", "UNCERTAIN"},
            "UNCERTAIN": {"FILLED", "PARTIAL", "REFUSED", "ABANDONED_UNVERIFIED", "HOLD_FOR_HUMAN", "SUBMITTED"},
            "HOLD_FOR_HUMAN": {"FILLED", "PARTIAL", "REFUSED", "ABANDONED_UNVERIFIED"},
            "FILLED": {"SETTLED"}, "PARTIAL": {"SETTLED"},
            "PREVIEW_REFUSED": set(), "PREVIEW_ONLY": set(), "REFUSED": set(), "SETTLED": set(),
            "ABANDONED_NOT_SUBMITTED": set(), "ABANDONED_UNVERIFIED": set(),
        })

    def test_protective_order_table(self) -> None:
        self.assertEqual(table(ProtectiveOrderState), {
            "PLANNED": {"SUBMITTED", "REFUSED"},
            "SUBMITTED": {"OPEN", "REFUSED", "REJECTED_FAIR_VALUE"},
            "OPEN": {"TRIGGERED_FILLED", "CANCELLED_ORPHAN", "REPLACING", "CANCELLED_BY_LIQUIDATION"},
            "REPLACING": {"OPEN", "REFUSED", "TRIGGERED_FILLED", "CANCELLED_ORPHAN", "CANCELLED_BY_LIQUIDATION"},
            "TRIGGERED_FILLED": set(), "CANCELLED_ORPHAN": set(), "CANCELLED_BY_LIQUIDATION": set(),
            "REJECTED_FAIR_VALUE": set(), "REFUSED": set(),
        })

    def test_proposal_guardian_and_protection_tables(self) -> None:
        self.assertEqual(table(ProposalState), {
            "CREATED": {"NOTIFIED", "APPROVED", "REJECTED", "EXPIRED", "WITHDRAWN"},
            "NOTIFIED": {"APPROVED", "REJECTED", "EXPIRED", "WITHDRAWN"},
            "APPROVED": {"CONSUMING", "EXPIRED", "WITHDRAWN", "REJECTED"},
            "CONSUMING": {"CONSUMED_FILLED", "CONSUMED_REFUSED", "REQUOTE_REQUIRED", "CONSUMED_UNCERTAIN",
                          "CONSUMED_PREVIEW_ONLY"},   # Phase 3 3.1 adds CONSUMED_PREVIEW_ONLY; no other proposal edge changes
            "REJECTED": set(), "EXPIRED": set(), "WITHDRAWN": set(), "CONSUMED_FILLED": set(),
            "CONSUMED_REFUSED": set(), "REQUOTE_REQUIRED": set(), "CONSUMED_UNCERTAIN": set(),
            "CONSUMED_PREVIEW_ONLY": set(),
        })
        self.assertEqual(table(GuardianTickState), {
            "STARTED": {"LOCK_ACQUIRED", "SKIPPED_LOCK_BUSY", "FAILED"},
            "LOCK_ACQUIRED": {"SLOTS_AUDITED", "FAILED"}, "SLOTS_AUDITED": {"SNAPSHOT", "FAILED"},
            "SNAPSHOT": {"RECONCILED", "FAILED"}, "RECONCILED": {"EVALUATED"}, "EVALUATED": {"EXECUTED"},
            "EXECUTED": {"OK", "PROTECTION_INCOMPLETE"},
            "SKIPPED_LOCK_BUSY": set(), "OK": set(), "PROTECTION_INCOMPLETE": set(), "FAILED": set(),
        })
        self.assertEqual(table(ProtectionState), {
            "NONE": {"INITIAL", "ADOPTED_UNPROTECTED"},
            "ADOPTED_UNPROTECTED": {"INITIAL", "UNPROTECTED", "CLOSED"},
            "UNPROTECTED": {"INITIAL", "CLOSED"},
            "INITIAL": {"ARMED", "UNPROTECTED", "CLOSED"},
            "ARMED": {"UNPROTECTED", "CLOSED"},
            "CLOSED": set(),
        })


C = ConsumeRunState


class Phase3StateTests(unittest.TestCase):
    """Phase 3 section 3.1 (SM-1, OPS-05)."""

    def test_consuming_successor_set_is_exact(self) -> None:
        self.assertEqual(successors(ProposalState.CONSUMING), frozenset({
            ProposalState.CONSUMED_FILLED, ProposalState.CONSUMED_REFUSED, ProposalState.REQUOTE_REQUIRED,
            ProposalState.CONSUMED_UNCERTAIN, ProposalState.CONSUMED_PREVIEW_ONLY,
        }))
        self.assertIs(assert_transition(ProposalState.CONSUMING, ProposalState.CONSUMED_PREVIEW_ONLY),
                      ProposalState.CONSUMED_PREVIEW_ONLY)
        self.assertTrue(is_terminal(ProposalState.CONSUMED_PREVIEW_ONLY))
        for source in (ProposalState.CREATED, ProposalState.NOTIFIED, ProposalState.APPROVED):
            with self.assertRaises(StateError):
                assert_transition(source, ProposalState.CONSUMED_PREVIEW_ONLY)
        self.assertEqual(ProposalState("CONSUMED_PREVIEW_ONLY"), ProposalState.CONSUMED_PREVIEW_ONLY)

    def test_consume_run_table_is_exact(self) -> None:
        self.assertEqual(table(ConsumeRunState), {
            "STARTED": {"LOCK_ACQUIRED", "SKIPPED_LOCK_BUSY", "FAILED"},
            "LOCK_ACQUIRED": {"SNAPSHOT", "DONE", "FAILED"},
            "SNAPSHOT": {"EVALUATED", "FAILED"},
            "EVALUATED": {"EXECUTED", "DONE", "FAILED"},
            "EXECUTED": {"DONE", "FAILED"},
            "SKIPPED_LOCK_BUSY": set(), "DONE": set(), "FAILED": set(),
        })
        self.assertIs(INITIAL_STATE[ConsumeRunState], C.STARTED)
        self.assertEqual(terminal_states(ConsumeRunState), frozenset({C.SKIPPED_LOCK_BUSY, C.DONE, C.FAILED}))

    def test_every_non_terminal_consume_run_state_reaches_failed(self) -> None:
        for state in ConsumeRunState:
            if is_terminal(state):
                continue
            self.assertIn(C.FAILED, successors(state), state)   # one declared edge, as 9.6 and section 14 need
            self.assertIs(assert_transition(state, C.FAILED), C.FAILED)

    def test_undeclared_consume_run_edges_are_refused(self) -> None:
        for current, nxt in ((C.STARTED, C.SNAPSHOT), (C.STARTED, C.DONE), (C.SNAPSHOT, C.DONE),
                             (C.SNAPSHOT, C.EXECUTED), (C.EXECUTED, C.EVALUATED), (C.DONE, C.FAILED),
                             (C.FAILED, C.STARTED), (C.SKIPPED_LOCK_BUSY, C.LOCK_ACQUIRED)):
            with self.assertRaises(StateError, msg=f"{current}->{nxt}"):
                assert_transition(current, nxt)
        with self.assertRaises(StateError):
            assert_transition(C.STARTED, GuardianTickState.LOCK_ACQUIRED)   # same member names, different machine

    def test_run_paths_used_by_consume(self) -> None:
        for path in ((C.STARTED, C.SKIPPED_LOCK_BUSY),
                     (C.STARTED, C.LOCK_ACQUIRED, C.DONE),                                   # recovery-only
                     (C.STARTED, C.LOCK_ACQUIRED, C.SNAPSHOT, C.EVALUATED, C.DONE),          # deferred / requoted
                     (C.STARTED, C.LOCK_ACQUIRED, C.SNAPSHOT, C.EVALUATED, C.EXECUTED, C.DONE),
                     (C.STARTED, C.LOCK_ACQUIRED, C.SNAPSHOT, C.EVALUATED, C.EXECUTED, C.FAILED)):
            for current, nxt in zip(path, path[1:]):
                assert_transition(current, nxt)


class ReachabilityTests(unittest.TestCase):
    def test_every_state_of_every_machine_is_reachable_from_its_initial_state(self) -> None:
        for machine in machines():
            self.assertEqual(reachable(machine), frozenset(machine), machine.__name__)
            self.assertIn(INITIAL_STATE[machine], machine)
            self.assertFalse(is_terminal(INITIAL_STATE[machine]), machine.__name__)

    def test_terminal_states_have_no_successors_and_every_machine_has_some(self) -> None:
        for machine in machines():
            terminals = terminal_states(machine)
            self.assertTrue(terminals, machine.__name__)
            for state in terminals:
                self.assertEqual(successors(state), frozenset())
                self.assertTrue(is_terminal(state))
            for state in machine:
                self.assertEqual(is_terminal(state), state in terminals)

    def test_machines_are_exactly_the_eight_declared(self) -> None:
        # Phase 3 3.1 adds ConsumeRunState to the seven Phase 2 machines.
        self.assertEqual(set(machines()), {SlotState, PulseStep, ReservationState, ProtectiveOrderState, ProposalState,
                                           ConsumeRunState, GuardianTickState, ProtectionState})
        self.assertEqual(set(TRANSITIONS), set(INITIAL_STATE))

    def test_cross_machine_transition_is_refused(self) -> None:
        with self.assertRaises(StateError):
            assert_transition(R.UNCERTAIN, P.SUBMITTED)
        with self.assertRaises(StateError):
            successors(DecisionCode.HOLD)


class DecisionCodeTests(unittest.TestCase):
    def test_account_unavailable_is_an_entry_blocker(self) -> None:
        self.assertEqual(DecisionCode.ACCOUNT_UNAVAILABLE.value, "ACCOUNT_UNAVAILABLE")
        self.assertEqual(DecisionCode("ACCOUNT_UNAVAILABLE"), DecisionCode.ACCOUNT_UNAVAILABLE)
        self.assertFalse(DecisionCode.ACCOUNT_UNAVAILABLE.value.startswith("EXIT_"))
        self.assertFalse(DecisionCode.ACCOUNT_UNAVAILABLE.value.startswith("ROUTE_"))

    def test_foreign_position_present_is_an_entry_blocker(self) -> None:
        self.assertEqual(DecisionCode.FOREIGN_POSITION_PRESENT.value, "FOREIGN_POSITION_PRESENT")
        self.assertEqual(DecisionCode("FOREIGN_POSITION_PRESENT"), DecisionCode.FOREIGN_POSITION_PRESENT)
        self.assertFalse(DecisionCode.FOREIGN_POSITION_PRESENT.value.startswith(("EXIT_", "ROUTE_")))
        self.assertNotEqual(DecisionCode.FOREIGN_POSITION_PRESENT, DecisionCode.FOREIGN_ORDER_PRESENT)

    def test_entries_suspended_is_an_entry_blocker(self) -> None:
        self.assertEqual(DecisionCode.ENTRIES_SUSPENDED.value, "ENTRIES_SUSPENDED")
        self.assertEqual(DecisionCode("ENTRIES_SUSPENDED"), DecisionCode.ENTRIES_SUSPENDED)
        self.assertFalse(DecisionCode.ENTRIES_SUSPENDED.value.startswith(("EXIT_", "ROUTE_")))
        self.assertNotEqual(DecisionCode.ENTRIES_SUSPENDED, DecisionCode.ENTRIES_DISABLED)

    def test_rejected_since_daily_close_is_an_entry_blocker(self) -> None:
        # Phase 3b 23.4: quiet after Reject; read through the REENTRY_COOLDOWN gate, never an exit or a route.
        self.assertEqual(DecisionCode.REJECTED_SINCE_DAILY_CLOSE.value, "REJECTED_SINCE_DAILY_CLOSE")
        self.assertEqual(DecisionCode("REJECTED_SINCE_DAILY_CLOSE"), DecisionCode.REJECTED_SINCE_DAILY_CLOSE)
        self.assertFalse(DecisionCode.REJECTED_SINCE_DAILY_CLOSE.value.startswith(("EXIT_", "ROUTE_")))
        self.assertNotEqual(DecisionCode.REJECTED_SINCE_DAILY_CLOSE, DecisionCode.REENTRY_COOLDOWN)

    def test_foreign_activity_note_vocabulary(self) -> None:
        self.assertEqual((UNAPPROVED_POSITIONS_NOTE, OPEN_ORDERS_UNREADABLE_NOTE),
                         ("UNAPPROVED_POSITIONS", "OPEN_ORDERS_UNREADABLE"))
        self.assertEqual(unapproved_position_ids(()), ())
        self.assertEqual(unapproved_position_ids(["OPEN_ORDERS_UNREADABLE", "UNAPPROVED_POSITIONS"]), ())
        self.assertEqual(unapproved_position_ids(["ACCOUNT_UNAVAILABLE", "UNAPPROVED_POSITIONS:SYNTH-B,SYNTH-A"]),
                         ("SYNTH-A", "SYNTH-B"))
        # Only the first matching note counts.
        self.assertEqual(unapproved_position_ids(iter(["UNAPPROVED_POSITIONS:SYNTH-A", "UNAPPROVED_POSITIONS:SYNTH-C"])),
                         ("SYNTH-A",))
        self.assertEqual(unapproved_position_ids(["UNAPPROVED_POSITIONS_ON_REREAD:SYNTH-A"]), ())

    def test_decision_code_values_are_unique_and_equal_to_names(self) -> None:
        for code in DecisionCode:
            self.assertEqual(code.value, code.name)


if __name__ == "__main__":
    unittest.main()
