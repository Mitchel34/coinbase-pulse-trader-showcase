"""State declarations, serializable snapshots, and inert intent data objects.

This showcase has no capture, decision-policy, or execution adapters.
"""
from __future__ import annotations

from .intents import (
    ApprovedIntent,
    Attestation,
    EntryIntent,
    ExitIntent,
    Intent,
    ProtectiveOrderIntent,
    client_order_id,
)
from .snapshot import (
    AccountView,
    ClockView,
    EvidenceEvent,
    EvidenceView,
    HourlyBar,
    MarketView,
    OpenOrder,
    PulseSnapshot,
    Quote,
    SnapshotError,
    canonical_dict,
    digest,
    snapshot_from_dict,
)
from .states import (
    DecisionCode,
    GuardianTickState,
    ProposalState,
    ProtectionState,
    ProtectiveOrderState,
    PulseStep,
    ReservationState,
    SlotKind,
    SlotState,
    StateError,
    assert_transition,
    is_terminal,
    reachable,
    successors,
    terminal_states,
)

__all__ = [
    "ApprovedIntent", "Attestation", "EntryIntent", "ExitIntent", "Intent", "ProtectiveOrderIntent",
    "client_order_id", "AccountView", "ClockView", "EvidenceEvent", "EvidenceView", "HourlyBar",
    "MarketView", "OpenOrder", "PulseSnapshot", "Quote", "SnapshotError", "canonical_dict", "digest",
    "snapshot_from_dict", "DecisionCode", "GuardianTickState",
    "ProposalState", "ProtectionState", "ProtectiveOrderState", "PulseStep", "ReservationState",
    "SlotKind", "SlotState", "StateError", "assert_transition", "is_terminal", "reachable", "successors",
    "terminal_states",
]
