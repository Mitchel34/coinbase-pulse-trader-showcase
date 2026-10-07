"""Declared state vocabulary and transition tables.

Eight enum-based machines describe workflow lifecycles. assert_transition
checks membership in the declared graph; it does not persist or execute a
transition. This module has no I/O and uses only the standard library.
"""
from __future__ import annotations

from enum import Enum, unique
from typing import Iterable, Mapping


class StateError(RuntimeError):
    """Raised on an undeclared transition."""


@unique
class SlotKind(str, Enum):
    PULSE = "PULSE"
    GUARDIAN = "GUARDIAN"
    CONSUME = "CONSUME"
    EVIDENCE = "EVIDENCE"
    WATCHDOG = "WATCHDOG"


@unique
class SlotState(str, Enum):
    """Lifecycle of one scheduled slot.  ``ATTEMPTED`` is written before any network call."""

    SCHEDULED = "SCHEDULED"
    ATTEMPTED = "ATTEMPTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_PROTECTION_INCOMPLETE = "COMPLETED_PROTECTION_INCOMPLETE"
    REFUSED = "REFUSED"          # terminal_reason carries the refusal code
    LATE_SKIPPED = "LATE_SKIPPED"
    LOCK_BUSY = "LOCK_BUSY"
    DB_BUSY = "DB_BUSY"
    ABANDONED = "ABANDONED"      # stale heartbeat detected by a later process
    DID_NOT_RUN = "DID_NOT_RUN"  # backfilled by slots.audit()


@unique
class PulseStep(str, Enum):
    """Ordered steps of a PULSE slot.  Refusal edges exist only from the first four."""

    ATTEMPTED = "ATTEMPTED"
    POLICY_LOADED = "POLICY_LOADED"
    CLOCK_CHECKED = "CLOCK_CHECKED"
    LOCK_ACQUIRED = "LOCK_ACQUIRED"
    SNAPSHOT_CAPTURED = "SNAPSHOT_CAPTURED"
    RECONCILED = "RECONCILED"
    PROTECTION_EVALUATED = "PROTECTION_EVALUATED"
    PROTECTION_EXECUTED = "PROTECTION_EXECUTED"
    EVIDENCE_LOADED = "EVIDENCE_LOADED"
    ENTRIES_EVALUATED = "ENTRIES_EVALUATED"
    ENTRIES_ROUTED = "ENTRIES_ROUTED"
    ENTRIES_EXECUTED = "ENTRIES_EXECUTED"
    FINALIZED = "FINALIZED"
    REFUSED = "REFUSED"


@unique
class ReservationState(str, Enum):
    """Write-ahead order reservation keyed by client_order_id."""

    RESERVED = "RESERVED"
    REQUEST_MATERIALIZED = "REQUEST_MATERIALIZED"
    PREVIEW_REFUSED = "PREVIEW_REFUSED"
    PREVIEW_ONLY = "PREVIEW_ONLY"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REFUSED = "REFUSED"
    UNCERTAIN = "UNCERTAIN"
    SETTLED = "SETTLED"
    ABANDONED_NOT_SUBMITTED = "ABANDONED_NOT_SUBMITTED"
    ABANDONED_UNVERIFIED = "ABANDONED_UNVERIFIED"
    HOLD_FOR_HUMAN = "HOLD_FOR_HUMAN"


@unique
class ProtectiveOrderState(str, Enum):
    PLANNED = "PLANNED"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    REPLACING = "REPLACING"
    TRIGGERED_FILLED = "TRIGGERED_FILLED"
    CANCELLED_ORPHAN = "CANCELLED_ORPHAN"
    CANCELLED_BY_LIQUIDATION = "CANCELLED_BY_LIQUIDATION"
    REJECTED_FAIR_VALUE = "REJECTED_FAIR_VALUE"
    REFUSED = "REFUSED"


@unique
class ProposalState(str, Enum):
    CREATED = "CREATED"
    NOTIFIED = "NOTIFIED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"
    CONSUMING = "CONSUMING"
    CONSUMED_FILLED = "CONSUMED_FILLED"
    CONSUMED_REFUSED = "CONSUMED_REFUSED"
    REQUOTE_REQUIRED = "REQUOTE_REQUIRED"
    CONSUMED_UNCERTAIN = "CONSUMED_UNCERTAIN"
    # Terminal; a shadow consumption with a materialized receipt (Phase 3 section 3.1).  Kept distinct from
    # CONSUMED_REFUSED so shadow plumbing is never counted as a venue refusal or a matched consumption.
    CONSUMED_PREVIEW_ONLY = "CONSUMED_PREVIEW_ONLY"


@unique
class ConsumeRunState(str, Enum):
    """Lifecycle of one ``pulse-consume`` run (Phase 3 section 9).  Every non-terminal state can reach FAILED."""

    STARTED = "STARTED"
    SKIPPED_LOCK_BUSY = "SKIPPED_LOCK_BUSY"
    LOCK_ACQUIRED = "LOCK_ACQUIRED"
    SNAPSHOT = "SNAPSHOT"
    EVALUATED = "EVALUATED"
    EXECUTED = "EXECUTED"
    DONE = "DONE"
    FAILED = "FAILED"


@unique
class GuardianTickState(str, Enum):
    STARTED = "STARTED"
    LOCK_ACQUIRED = "LOCK_ACQUIRED"
    SKIPPED_LOCK_BUSY = "SKIPPED_LOCK_BUSY"
    SLOTS_AUDITED = "SLOTS_AUDITED"
    SNAPSHOT = "SNAPSHOT"
    RECONCILED = "RECONCILED"
    EVALUATED = "EVALUATED"
    EXECUTED = "EXECUTED"
    OK = "OK"
    PROTECTION_INCOMPLETE = "PROTECTION_INCOMPLETE"
    FAILED = "FAILED"


@unique
class ProtectionState(str, Enum):
    """Per-position protection record."""

    NONE = "NONE"
    ADOPTED_UNPROTECTED = "ADOPTED_UNPROTECTED"
    UNPROTECTED = "UNPROTECTED"
    INITIAL = "INITIAL"
    ARMED = "ARMED"
    CLOSED = "CLOSED"


@unique
class DecisionCode(str, Enum):
    """Per-asset evaluation outcomes.  Every asset in every pulse ends in exactly one."""

    # Exits (always AUTO)
    EXIT_L2_BREAKER = "EXIT_L2_BREAKER"
    EXIT_HARD_STOP = "EXIT_HARD_STOP"
    EXIT_LIQUIDATION_CUSHION = "EXIT_LIQUIDATION_CUSHION"
    EXIT_MARGIN_RATIO = "EXIT_MARGIN_RATIO"
    EXIT_PRE_RAMP_SOLVENCY = "EXIT_PRE_RAMP_SOLVENCY"
    EXIT_MAX_HOLD = "EXIT_MAX_HOLD"
    EXIT_UNTRADABLE = "EXIT_UNTRADABLE"
    EXIT_GIVEBACK = "EXIT_GIVEBACK"
    EXIT_MANUAL = "EXIT_MANUAL"
    EXIT_UNPROTECTED = "EXIT_UNPROTECTED"
    HOLD = "HOLD"
    # Entry blockers (deterministic, monotone-reducing)
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
    VOLATILITY_UNAVAILABLE = "VOLATILITY_UNAVAILABLE"
    VOLATILITY_REGIME_BLOCK = "VOLATILITY_REGIME_BLOCK"
    NARRATIVE_UNAVAILABLE = "NARRATIVE_UNAVAILABLE"
    NARRATIVE_VETO = "NARRATIVE_VETO"
    ADVERSE_FUNDING_LIMIT = "ADVERSE_FUNDING_LIMIT"
    FUNDING_COST_UNKNOWN = "FUNDING_COST_UNKNOWN"
    REENTRY_COOLDOWN = "REENTRY_COOLDOWN"
    # The operator rejected a proposal for this asset since the last 00:00 UTC daily close; read through the
    # REENTRY_COOLDOWN gate (Phase 3b 23.4).  Entries only, that asset only; it ends by itself at the next close.
    REJECTED_SINCE_DAILY_CLOSE = "REJECTED_SINCE_DAILY_CLOSE"
    POSITION_EXISTS = "POSITION_EXISTS"
    POSITION_CAP = "POSITION_CAP"
    ENTRY_CAP = "ENTRY_CAP"
    INFEASIBLE = "INFEASIBLE"
    CAPITAL_INELIGIBLE = "CAPITAL_INELIGIBLE"
    L1_BREAKER_HALT = "L1_BREAKER_HALT"
    BLACKOUT_WINDOW = "BLACKOUT_WINDOW"
    ENTRIES_DISABLED = "ENTRIES_DISABLED"
    ENTRIES_SUSPENDED = "ENTRIES_SUSPENDED"   # an active hitl_suspensions row, read through the ENTRIES_DISABLED gate (Phase 3 8.4)
    ENTRY_AUTHORIZATION_EXPIRED = "ENTRY_AUTHORIZATION_EXPIRED"
    # Phase 3b 26: before the activation's entry_authorized_at; read through the ENTRY_AUTHORIZATION gate.  Entries only.
    ENTRY_AUTHORIZATION_NOT_YET_VALID = "ENTRY_AUTHORIZATION_NOT_YET_VALID"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    UNRESOLVED_RESERVATION = "UNRESOLVED_RESERVATION"
    FOREIGN_ORDER_PRESENT = "FOREIGN_ORDER_PRESENT"
    FOREIGN_POSITION_PRESENT = "FOREIGN_POSITION_PRESENT"   # a futures position on a product outside the approved map
    ASSET_NOT_ENABLED = "ASSET_NOT_ENABLED"
    UNTRADABLE = "UNTRADABLE"
    ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"   # balance summary unreadable at capture: entries blocked, exits unaffected
    # Entry routes
    ROUTE_HITL_PROPOSAL = "ROUTE_HITL_PROPOSAL"
    ROUTE_AUTO_EXECUTION = "ROUTE_AUTO_EXECUTION"


# Snapshot note vocabulary shared by capture, the gates and the guardian (docs/PHASE2_CONTRACTS.md 13.4.1).
UNAPPROVED_POSITIONS_NOTE = "UNAPPROVED_POSITIONS"
OPEN_ORDERS_UNREADABLE_NOTE = "OPEN_ORDERS_UNREADABLE"


def unapproved_position_ids(notes: Iterable[str]) -> tuple[str, ...]:
    """Product ids from the first note starting ``UNAPPROVED_POSITIONS:`` (comma-separated), sorted; ``()`` when none."""
    prefix = f"{UNAPPROVED_POSITIONS_NOTE}:"
    for note in notes:
        if isinstance(note, str) and note.startswith(prefix):
            return tuple(sorted(part for part in note[len(prefix):].split(",") if part))
    return ()


# --------------------------------------------------------------------------
# Transition tables.  Keys are current states; values are the permitted next states.
# A state absent from a table (or mapped to an empty set) is terminal.
# --------------------------------------------------------------------------

_SLOT_TERMINAL = frozenset({
    SlotState.COMPLETED, SlotState.COMPLETED_PROTECTION_INCOMPLETE, SlotState.REFUSED,
    SlotState.LATE_SKIPPED, SlotState.LOCK_BUSY, SlotState.DB_BUSY, SlotState.ABANDONED,
    SlotState.DID_NOT_RUN,
})

TRANSITIONS: Mapping[type, Mapping[Enum, frozenset]] = {
    SlotState: {
        SlotState.SCHEDULED: frozenset({SlotState.ATTEMPTED, SlotState.DID_NOT_RUN}),
        SlotState.ATTEMPTED: frozenset({
            SlotState.RUNNING, SlotState.LATE_SKIPPED, SlotState.LOCK_BUSY, SlotState.DB_BUSY,
            SlotState.REFUSED, SlotState.ABANDONED,
        }),
        SlotState.RUNNING: frozenset({
            SlotState.COMPLETED, SlotState.COMPLETED_PROTECTION_INCOMPLETE, SlotState.REFUSED,
            SlotState.DB_BUSY, SlotState.ABANDONED,
        }),
    },
    PulseStep: {
        PulseStep.ATTEMPTED: frozenset({PulseStep.POLICY_LOADED, PulseStep.REFUSED}),
        PulseStep.POLICY_LOADED: frozenset({PulseStep.CLOCK_CHECKED, PulseStep.REFUSED}),
        PulseStep.CLOCK_CHECKED: frozenset({PulseStep.LOCK_ACQUIRED, PulseStep.REFUSED}),
        PulseStep.LOCK_ACQUIRED: frozenset({PulseStep.SNAPSHOT_CAPTURED, PulseStep.REFUSED}),
        PulseStep.SNAPSHOT_CAPTURED: frozenset({PulseStep.RECONCILED, PulseStep.REFUSED}),
        # From here on there is no refusal edge: protection must run.
        PulseStep.RECONCILED: frozenset({PulseStep.PROTECTION_EVALUATED}),
        PulseStep.PROTECTION_EVALUATED: frozenset({PulseStep.PROTECTION_EXECUTED}),
        PulseStep.PROTECTION_EXECUTED: frozenset({PulseStep.EVIDENCE_LOADED}),
        PulseStep.EVIDENCE_LOADED: frozenset({PulseStep.ENTRIES_EVALUATED}),
        PulseStep.ENTRIES_EVALUATED: frozenset({PulseStep.ENTRIES_ROUTED}),
        PulseStep.ENTRIES_ROUTED: frozenset({PulseStep.ENTRIES_EXECUTED, PulseStep.FINALIZED}),
        PulseStep.ENTRIES_EXECUTED: frozenset({PulseStep.FINALIZED}),
    },
    ReservationState: {
        ReservationState.RESERVED: frozenset({
            ReservationState.REQUEST_MATERIALIZED, ReservationState.PREVIEW_REFUSED,
            ReservationState.PREVIEW_ONLY, ReservationState.ABANDONED_NOT_SUBMITTED,
        }),
        ReservationState.REQUEST_MATERIALIZED: frozenset({
            ReservationState.SUBMITTING, ReservationState.PREVIEW_ONLY,
            ReservationState.ABANDONED_NOT_SUBMITTED,
        }),
        ReservationState.SUBMITTING: frozenset({
            ReservationState.SUBMITTED, ReservationState.REFUSED, ReservationState.UNCERTAIN,
            ReservationState.FILLED, ReservationState.PARTIAL,
            ReservationState.ABANDONED_NOT_SUBMITTED, ReservationState.ABANDONED_UNVERIFIED,
            ReservationState.HOLD_FOR_HUMAN,
        }),
        ReservationState.SUBMITTED: frozenset({
            ReservationState.FILLED, ReservationState.PARTIAL, ReservationState.REFUSED,
            ReservationState.UNCERTAIN,
        }),
        ReservationState.UNCERTAIN: frozenset({
            ReservationState.FILLED, ReservationState.PARTIAL, ReservationState.REFUSED,
            ReservationState.ABANDONED_UNVERIFIED, ReservationState.HOLD_FOR_HUMAN,
            # RESTING only: the venue reports the acknowledged order live (Phase 2, section 0).
            ReservationState.SUBMITTED,
        }),
        ReservationState.HOLD_FOR_HUMAN: frozenset({
            ReservationState.FILLED, ReservationState.PARTIAL, ReservationState.REFUSED,
            ReservationState.ABANDONED_UNVERIFIED,
        }),
        ReservationState.FILLED: frozenset({ReservationState.SETTLED}),
        ReservationState.PARTIAL: frozenset({ReservationState.SETTLED}),
    },
    ProtectiveOrderState: {
        ProtectiveOrderState.PLANNED: frozenset({ProtectiveOrderState.SUBMITTED, ProtectiveOrderState.REFUSED}),
        ProtectiveOrderState.SUBMITTED: frozenset({
            ProtectiveOrderState.OPEN, ProtectiveOrderState.REFUSED, ProtectiveOrderState.REJECTED_FAIR_VALUE,
        }),
        ProtectiveOrderState.OPEN: frozenset({
            ProtectiveOrderState.TRIGGERED_FILLED, ProtectiveOrderState.CANCELLED_ORPHAN,
            ProtectiveOrderState.REPLACING, ProtectiveOrderState.CANCELLED_BY_LIQUIDATION,
        }),
        ProtectiveOrderState.REPLACING: frozenset({
            ProtectiveOrderState.OPEN, ProtectiveOrderState.REFUSED,
            # A ratchet cancel may land on a stop that already filled, or after the venue
            # cancelled it (orphan / liquidation); Phase 2, section 0 and section 9.
            ProtectiveOrderState.TRIGGERED_FILLED, ProtectiveOrderState.CANCELLED_ORPHAN,
            ProtectiveOrderState.CANCELLED_BY_LIQUIDATION,
        }),
    },
    ProposalState: {
        ProposalState.CREATED: frozenset({
            ProposalState.NOTIFIED, ProposalState.APPROVED, ProposalState.REJECTED,
            ProposalState.EXPIRED, ProposalState.WITHDRAWN,
        }),
        ProposalState.NOTIFIED: frozenset({
            ProposalState.APPROVED, ProposalState.REJECTED, ProposalState.EXPIRED, ProposalState.WITHDRAWN,
        }),
        ProposalState.APPROVED: frozenset({
            ProposalState.CONSUMING, ProposalState.EXPIRED, ProposalState.WITHDRAWN, ProposalState.REJECTED,
        }),
        ProposalState.CONSUMING: frozenset({
            ProposalState.CONSUMED_FILLED, ProposalState.CONSUMED_REFUSED,
            ProposalState.REQUOTE_REQUIRED, ProposalState.CONSUMED_UNCERTAIN,
            ProposalState.CONSUMED_PREVIEW_ONLY,
        }),
    },
    ConsumeRunState: {
        ConsumeRunState.STARTED: frozenset({
            ConsumeRunState.LOCK_ACQUIRED, ConsumeRunState.SKIPPED_LOCK_BUSY, ConsumeRunState.FAILED,
        }),
        # DONE straight from LOCK_ACQUIRED is a recovery-only run (Phase 3 9.6).
        ConsumeRunState.LOCK_ACQUIRED: frozenset({ConsumeRunState.SNAPSHOT, ConsumeRunState.DONE, ConsumeRunState.FAILED}),
        ConsumeRunState.SNAPSHOT: frozenset({ConsumeRunState.EVALUATED, ConsumeRunState.FAILED}),
        # DONE from EVALUATED: deferred, withdrawn or requoted without an order.
        ConsumeRunState.EVALUATED: frozenset({ConsumeRunState.EXECUTED, ConsumeRunState.DONE, ConsumeRunState.FAILED}),
        ConsumeRunState.EXECUTED: frozenset({ConsumeRunState.DONE, ConsumeRunState.FAILED}),
    },
    GuardianTickState: {
        GuardianTickState.STARTED: frozenset({GuardianTickState.LOCK_ACQUIRED, GuardianTickState.SKIPPED_LOCK_BUSY, GuardianTickState.FAILED}),
        GuardianTickState.LOCK_ACQUIRED: frozenset({GuardianTickState.SLOTS_AUDITED, GuardianTickState.FAILED}),
        GuardianTickState.SLOTS_AUDITED: frozenset({GuardianTickState.SNAPSHOT, GuardianTickState.FAILED}),
        GuardianTickState.SNAPSHOT: frozenset({GuardianTickState.RECONCILED, GuardianTickState.FAILED}),
        GuardianTickState.RECONCILED: frozenset({GuardianTickState.EVALUATED}),
        GuardianTickState.EVALUATED: frozenset({GuardianTickState.EXECUTED}),
        GuardianTickState.EXECUTED: frozenset({GuardianTickState.OK, GuardianTickState.PROTECTION_INCOMPLETE}),
    },
    ProtectionState: {
        ProtectionState.NONE: frozenset({ProtectionState.INITIAL, ProtectionState.ADOPTED_UNPROTECTED}),
        ProtectionState.ADOPTED_UNPROTECTED: frozenset({ProtectionState.INITIAL, ProtectionState.UNPROTECTED, ProtectionState.CLOSED}),
        ProtectionState.UNPROTECTED: frozenset({ProtectionState.INITIAL, ProtectionState.CLOSED}),
        ProtectionState.INITIAL: frozenset({ProtectionState.ARMED, ProtectionState.UNPROTECTED, ProtectionState.CLOSED}),
        ProtectionState.ARMED: frozenset({ProtectionState.UNPROTECTED, ProtectionState.CLOSED}),
    },
}

INITIAL_STATE: Mapping[type, Enum] = {
    SlotState: SlotState.SCHEDULED,
    PulseStep: PulseStep.ATTEMPTED,
    ReservationState: ReservationState.RESERVED,
    ProtectiveOrderState: ProtectiveOrderState.PLANNED,
    ProposalState: ProposalState.CREATED,
    ConsumeRunState: ConsumeRunState.STARTED,
    GuardianTickState: GuardianTickState.STARTED,
    ProtectionState: ProtectionState.NONE,
}


def successors(state: Enum) -> frozenset:
    table = TRANSITIONS.get(type(state))
    if table is None:
        raise StateError(f"{type(state).__name__} has no transition table.")
    return table.get(state, frozenset())


def is_terminal(state: Enum) -> bool:
    return not successors(state)


def assert_transition(current: Enum, nxt: Enum) -> Enum:
    """Return ``nxt`` if ``current -> nxt`` is declared; raise StateError otherwise."""
    if type(current) is not type(nxt):
        raise StateError(f"Cannot move {current!r} to {nxt!r}: different machines.")
    if nxt not in successors(current):
        raise StateError(f"Undeclared transition {type(current).__name__}: {current.value} -> {nxt.value}.")
    return nxt


def reachable(machine: type) -> frozenset:
    """All states reachable from the machine's initial state (used by tests)."""
    start = INITIAL_STATE[machine]
    seen: set = {start}
    frontier: list = [start]
    while frontier:
        state = frontier.pop()
        for nxt in successors(state):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return frozenset(seen)


def terminal_states(machine: type) -> frozenset:
    return frozenset(member for member in machine if is_terminal(member))


def slot_terminal_states() -> frozenset:
    return _SLOT_TERMINAL


def machines() -> Iterable[type]:
    return tuple(TRANSITIONS.keys())
