"""Typed, inert descriptions of proposed effects.

Content digests support replay comparison. UUID5 identifiers are derived from
lineage and attempt number; they are identifiers, not authentication tokens.
No object in this module can submit an order or access an account.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, Optional, Union

from pulse_trader.graph.snapshot import _canonical, digest

INTENT_SCHEMA_VERSION = 1
_NAMESPACE = uuid.NAMESPACE_URL
_LINEAGE_PREFIX = "coinbase-pulse-trader-v3"


def client_order_id(lineage: str, attempt: int = 1) -> str:
    """Deterministic uuid5; ``lineage`` must be unique per (slot, asset, effect, direction)."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return str(uuid.uuid5(_NAMESPACE, f"{_LINEAGE_PREFIX}:{lineage}:{attempt}"))


@dataclass(frozen=True)
class Attestation:
    """Why the adapter may act.  ``POLICY`` = deterministic exit or AUTO entry; ``APPROVAL`` = human consent row."""

    kind: Literal["POLICY", "APPROVAL"]
    reference: str                       # policy digest, or approvals.id
    policy_digest: str


@dataclass(frozen=True)
class EntryIntent:
    schema_version: int
    slot_id: str
    asset: str
    product_id: str
    side: Literal["BUY"]
    contracts: Decimal
    reference_price: Decimal             # mark at snapshot
    max_fill_price: Decimal              # consent envelope: reference * (1 + policy.entry.max_fill_slippage)
    stop_price: Decimal
    stop_limit_price: Decimal
    stop_distance_fraction: Decimal
    sigma_horizon: Decimal               # forecast sigma over the holding horizon used for the stop
    risk_unit_usd: Decimal               # R frozen for this position
    loss_at_stop_usd: Decimal            # d*N + fees + slippage + adverse funding
    max_loss_usd: Decimal                # consent envelope
    policy_digest: str
    snapshot_digest: str
    sizing_digest: str
    gate_record: Mapping[str, str]       # computed gate name -> PASSED | code
    created_at: str

    @property
    def lineage(self) -> str:
        return f"{self.slot_id}:{self.asset}:OPEN:LONG"

    def client_order_id(self, attempt: int = 1) -> str:
        return client_order_id(self.lineage, attempt)

    def core(self) -> dict:
        """Fields bound by consent; excludes the quote envelope and timestamps."""
        return {
            "schema_version": self.schema_version, "slot_id": self.slot_id, "asset": self.asset,
            "product_id": self.product_id, "side": self.side, "contracts": str(self.contracts),
            "stop_distance_fraction": str(self.stop_distance_fraction), "risk_unit_usd": str(self.risk_unit_usd),
            "policy_digest": self.policy_digest, "sizing_digest": self.sizing_digest,
            "gate_record": dict(self.gate_record),
        }

    def core_digest(self) -> str:
        return digest(self.core())

    def envelope(self) -> dict:
        return {"reference_price": str(self.reference_price), "max_fill_price": str(self.max_fill_price),
                "stop_price": str(self.stop_price), "stop_limit_price": str(self.stop_limit_price),
                "max_loss_usd": str(self.max_loss_usd), "loss_at_stop_usd": str(self.loss_at_stop_usd)}

    def envelope_digest(self) -> str:
        return digest(self.envelope())


@dataclass(frozen=True)
class ExitIntent:
    schema_version: int
    slot_id: str
    asset: str
    product_id: str
    side: Literal["SELL", "BUY"]
    contracts: Decimal
    reasons: tuple[str, ...]             # DecisionCode values, most severe first
    reference_price: Decimal
    policy_digest: str
    snapshot_digest: str
    created_at: str
    protective_order_client_id: Optional[str] = None   # own resting stop to cancel-confirm first

    @property
    def lineage(self) -> str:
        return f"{self.slot_id}:{self.asset}:CLOSE:{'LONG' if self.side == 'SELL' else 'SHORT'}"

    def client_order_id(self, attempt: int = 1) -> str:
        return client_order_id(self.lineage, attempt)

    def core_digest(self) -> str:
        return digest({"slot_id": self.slot_id, "asset": self.asset, "product_id": self.product_id,
                       "side": self.side, "contracts": str(self.contracts), "reasons": list(self.reasons),
                       "policy_digest": self.policy_digest})


@dataclass(frozen=True)
class ProtectiveOrderIntent:
    """Resting stop-limit SELL bound to an open long; ``version`` increments on every replace."""

    schema_version: int
    asset: str
    product_id: str
    entry_client_order_id: str
    contracts: Decimal
    stop_price: Decimal
    limit_price: Decimal
    version: int
    policy_digest: str
    created_at: str

    @property
    def lineage(self) -> str:
        return f"{self.entry_client_order_id}:PROTECT:v{self.version}"

    def client_order_id(self, attempt: int = 1) -> str:
        return client_order_id(self.lineage, attempt)

    def core_digest(self) -> str:
        """Identity of one stop version: lineage, size and prices; excludes ``created_at``."""
        return digest({"schema_version": self.schema_version, "asset": self.asset, "product_id": self.product_id,
                       "entry_client_order_id": self.entry_client_order_id, "contracts": str(self.contracts),
                       "stop_price": str(self.stop_price), "limit_price": str(self.limit_price),
                       "version": self.version, "policy_digest": self.policy_digest})


Intent = Union[EntryIntent, ExitIntent, ProtectiveOrderIntent]

INTENT_KINDS: Mapping[type, str] = {EntryIntent: "ENTRY", ExitIntent: "EXIT", ProtectiveOrderIntent: "PROTECTIVE"}
_INTENT_TYPES: Mapping[str, type] = {kind: cls for cls, kind in INTENT_KINDS.items()}
_DECIMAL_FIELDS = frozenset({
    "contracts", "reference_price", "max_fill_price", "stop_price", "stop_limit_price", "stop_distance_fraction",
    "sigma_horizon", "risk_unit_usd", "loss_at_stop_usd", "max_loss_usd", "limit_price",
})


def intent_kind(intent: Intent) -> str:
    """``ENTRY`` | ``EXIT`` | ``PROTECTIVE`` — the ``intents.kind`` column."""
    try:
        return INTENT_KINDS[type(intent)]
    except KeyError:
        raise TypeError(f"{type(intent).__name__} is not an Intent.") from None


def intent_identity(intent: Intent) -> str:
    """The ``intents.intent_id`` / reservation ``intent_id``: the intent's ``core_digest()``."""
    intent_kind(intent)
    return intent.core_digest()


def intent_to_dict(intent: Intent) -> dict[str, Any]:
    """Plain JSON mapping (Decimals as strings, tuples as lists) plus ``kind`` so it round-trips."""
    payload = json.loads(_canonical(intent))
    payload["kind"] = intent_kind(intent)
    return payload


def intent_from_dict(data: Mapping[str, Any]) -> Intent:
    """Inverse of ``intent_to_dict``; strict about the kind and the field set, Decimals rebuilt from text."""
    if not isinstance(data, Mapping):
        raise TypeError("intent_from_dict needs a mapping.")
    kind = data.get("kind")
    cls = _INTENT_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"Unknown intent kind {kind!r}; expected one of {sorted(_INTENT_TYPES)}.")
    declared = {item.name for item in fields(cls)}
    provided = set(data) - {"kind"}
    if provided != declared:
        missing, extra = sorted(declared - provided), sorted(provided - declared)
        raise ValueError(f"{kind} intent fields mismatch: missing {missing}, unexpected {extra}.")
    values: dict[str, Any] = {}
    for name in declared:
        value = data[name]
        if name in _DECIMAL_FIELDS:
            if not isinstance(value, str):
                raise ValueError(f"{kind} intent field {name} must be a decimal string.")
            try:
                value = Decimal(value)
            except InvalidOperation as error:
                raise ValueError(f"{kind} intent field {name} is not a decimal: {value!r}.") from error
        elif name == "reasons":
            value = tuple(str(item) for item in value)
        elif name == "gate_record":
            value = dict(value)
        values[name] = value
    return cls(**values)


@dataclass(frozen=True)
class ApprovedIntent:
    """The adapter's only input.  Built by the router (POLICY) or the consumer (APPROVAL)."""

    intent: Intent
    attestation: Attestation
    attempt: int = 1
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def client_order_id(self) -> str:
        return self.intent.client_order_id(self.attempt)

    def retry(self) -> "ApprovedIntent":
        """A new attempt is a new client id; used only for definitively-rejected pre-trade checks, never for UNCERTAIN."""
        return replace(self, attempt=self.attempt + 1)
