"""Serializable point-in-time data with canonical SHA-256 digests.

Decimals serialize as text, enums as values, and sets as sorted lists.
snapshot_from_dict checks exact field sets and selected types/enums.
The dataclasses are shallowly frozen: nested mappings remain mutable.
Decimal spelling is significant, and non-finite Decimal strings round-trip;
this is a serialization boundary, not complete market-input validation.

There is no clock read, network call, capture implementation, or execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal, Mapping, Optional

from pulse_trader.models import Candle, Position, Product

SNAPSHOT_SCHEMA_VERSION = 1

MarginWindow = Literal["INTRADAY", "OVERNIGHT", "TRANSITION", "UNKNOWN"]
MARGIN_WINDOWS: tuple[str, ...] = ("INTRADAY", "OVERNIGHT", "TRANSITION", "UNKNOWN")
EVIDENCE_DIRECTIONS: tuple[str, ...] = ("risk_off", "neutral")


class SnapshotError(ValueError):
    """Raised when a serialised snapshot cannot be rebuilt exactly; nothing is defaulted."""


def _canonical(value: Any) -> str:
    """Canonical JSON for digesting: sorted keys, Decimals as their text (a non-finite venue value
    such as a NaN funding rate stays the string ``"NaN"`` and round-trips), sets sorted, no float NaN."""
    def default(item: Any) -> Any:
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (frozenset, set)):
            return sorted(item)
        if isinstance(item, tuple):
            return list(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=default, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_dict(value: Any) -> Any:
    """Plain JSON form (str/int/bool/None/list/dict; Decimals as strings) of any dataclass or value.

    This is the only way a dataclass without a contract-specified ``to_dict`` may
    enter a ``record_step`` / event payload.
    """
    return json.loads(_canonical(value))


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Quote:
    """Best bid/ask read at capture time; ``spread_bps`` is relative to the mid."""

    product_id: str
    bid: Decimal
    ask: Decimal
    captured_at: str

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid
        return (self.ask - self.bid) / mid * Decimal(10000) if mid else Decimal("Infinity")


@dataclass(frozen=True)
class OpenOrder:
    """Sanitized open-order view.  Stop fields are kept so resting protection can be verified."""

    order_id: str
    client_order_id: str
    product_id: str
    side: str
    status: str
    order_kind: str                      # e.g. market_market_ioc, stop_limit_stop_limit_gtc
    base_size: Decimal
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    trigger_status: Optional[str] = None
    created_time: Optional[str] = None


@dataclass(frozen=True)
class AccountView:
    """CFM balance summary plus margin-regime facts.  Unknown optional fields stay ``None``."""

    equity: Decimal                      # available_margin, the v1 balance convention
    initial_margin: Decimal
    cash_balance: Decimal
    futures_buying_power: Decimal
    daily_realized_pnl: Decimal
    unrealized_pnl: Decimal
    liquidation_threshold: Optional[Decimal] = None
    liquidation_buffer_amount: Optional[Decimal] = None
    margin_window: MarginWindow = "UNKNOWN"
    margin_window_ends_at: Optional[str] = None
    intraday_margin_enabled: Optional[bool] = None

    @property
    def margin_ratio(self) -> Decimal:
        return self.initial_margin / self.equity if self.equity else Decimal("Infinity")


@dataclass(frozen=True)
class HourlyBar:
    start: int
    close: Decimal


@dataclass(frozen=True)
class MarketView:
    """Completed daily candles (technical score) and hourly closes (volatility) per asset."""

    daily: Mapping[str, tuple[Candle, ...]] = field(default_factory=dict)
    hourly: Mapping[str, tuple[HourlyBar, ...]] = field(default_factory=dict)
    daily_status: Mapping[str, str] = field(default_factory=dict)   # asset -> OK | <error code>
    hourly_status: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceEvent:
    """Typed, veto-only evidence event (reserved; no evidence producer exists yet: see the Phase 4 v2 research roadmap)."""

    event_id: str                        # from the closed allowlist in policy
    direction: Literal["risk_off", "neutral"]
    assets: tuple[str, ...]
    confidence: Decimal
    source_url: str
    published_at: str
    quote_sha256: str


@dataclass(frozen=True)
class EvidenceView:
    status: str                          # AVAILABLE | NARRATIVE_UNAVAILABLE | STALE | INVALID
    artifact_digest: Optional[str] = None
    generated_at: Optional[str] = None
    events: tuple[EvidenceEvent, ...] = ()


@dataclass(frozen=True)
class ClockView:
    local_time: str
    exchange_time: Optional[str]
    skew_seconds: Optional[Decimal]


@dataclass(frozen=True)
class PulseSnapshot:
    """Everything a decision may look at.  Frozen; ``digest()`` is stable across processes."""

    schema_version: int
    slot_id: str
    slot_kind: str
    scheduled_for: str
    captured_at: str
    clock: ClockView
    permissions: Mapping[str, bool]
    products: Mapping[str, Product]
    quotes: Mapping[str, Quote]
    positions: Mapping[str, Position]
    open_orders: tuple[OpenOrder, ...]
    account: Optional[AccountView]      # None: balance summary unreadable at capture (note ACCOUNT_UNAVAILABLE)
    market: MarketView
    evidence: EvidenceView
    policy_digest: str
    notes: tuple[str, ...] = ()          # capture-time degradations, e.g. MARGIN_WINDOW_UNREADABLE

    def digest(self) -> str:
        return digest(self)

    def foreign_open_orders(self, own_client_ids: frozenset[str]) -> tuple[OpenOrder, ...]:
        return tuple(order for order in self.open_orders if order.client_order_id not in own_client_ids)

    def to_dict(self) -> dict[str, Any]:
        return canonical_dict(self)


# --------------------------------------------------------------------------
# Strict inverse of ``to_dict``.  Every helper names the field it refuses.
# --------------------------------------------------------------------------

def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotError(f"{label} must be a JSON object, got {type(value).__name__}.")
    return value


def _exact_keys(payload: Mapping[str, Any], cls: type, label: str) -> None:
    expected = {item.name for item in fields(cls)}
    given = set(payload)
    missing = sorted(expected - given)
    unknown = sorted(given - expected)
    if missing or unknown:
        raise SnapshotError(f"{label}: missing keys {missing}, unknown keys {unknown}.")


def _str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SnapshotError(f"{label} must be a string, got {value!r}.")
    return value


def _optional_str(value: Any, label: str) -> Optional[str]:
    return None if value is None else _str(value, label)


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotError(f"{label} must be a bool, got {value!r}.")
    return value


def _optional_bool(value: Any, label: str) -> Optional[bool]:
    return None if value is None else _bool(value, label)


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotError(f"{label} must be an int, got {value!r}.")
    return value


def _decimal(value: Any, label: str) -> Decimal:
    if not isinstance(value, str):
        raise SnapshotError(f"{label} must be a Decimal string, got {value!r}.")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise SnapshotError(f"{label} is not a Decimal: {value!r}.") from error


def _optional_decimal(value: Any, label: str) -> Optional[Decimal]:
    return None if value is None else _decimal(value, label)


def _list(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise SnapshotError(f"{label} must be a JSON array, got {type(value).__name__}.")
    return value


def _str_tuple(value: Any, label: str) -> tuple[str, ...]:
    return tuple(_str(item, f"{label}[{index}]") for index, item in enumerate(_list(value, label)))


def _clock_from_dict(payload: Any) -> ClockView:
    payload = _mapping(payload, "clock")
    _exact_keys(payload, ClockView, "clock")
    return ClockView(
        local_time=_str(payload["local_time"], "clock.local_time"),
        exchange_time=_optional_str(payload["exchange_time"], "clock.exchange_time"),
        skew_seconds=_optional_decimal(payload["skew_seconds"], "clock.skew_seconds"),
    )


def _product_from_dict(payload: Any, label: str) -> Product:
    payload = _mapping(payload, label)
    _exact_keys(payload, Product, label)
    return Product(
        asset=_str(payload["asset"], f"{label}.asset"),
        product_id=_str(payload["product_id"], f"{label}.product_id"),
        spot_product_id=_str(payload["spot_product_id"], f"{label}.spot_product_id"),
        price=_decimal(payload["price"], f"{label}.price"),
        index_price=_decimal(payload["index_price"], f"{label}.index_price"),
        contract_size=_decimal(payload["contract_size"], f"{label}.contract_size"),
        long_margin_rate=_decimal(payload["long_margin_rate"], f"{label}.long_margin_rate"),
        short_margin_rate=_decimal(payload["short_margin_rate"], f"{label}.short_margin_rate"),
        funding_rate=_optional_decimal(payload["funding_rate"], f"{label}.funding_rate"),
        funding_interval_seconds=_int(payload["funding_interval_seconds"], f"{label}.funding_interval_seconds"),
        tradable=_bool(payload["tradable"], f"{label}.tradable"),
        price_increment=_optional_decimal(payload["price_increment"], f"{label}.price_increment"),
        quote_increment=_optional_decimal(payload["quote_increment"], f"{label}.quote_increment"),
    )


def _quote_from_dict(payload: Any, label: str) -> Quote:
    payload = _mapping(payload, label)
    _exact_keys(payload, Quote, label)
    return Quote(
        product_id=_str(payload["product_id"], f"{label}.product_id"),
        bid=_decimal(payload["bid"], f"{label}.bid"),
        ask=_decimal(payload["ask"], f"{label}.ask"),
        captured_at=_str(payload["captured_at"], f"{label}.captured_at"),
    )


def _position_from_dict(payload: Any, label: str) -> Position:
    payload = _mapping(payload, label)
    _exact_keys(payload, Position, label)
    direction = _str(payload["direction"], f"{label}.direction")
    if direction not in ("LONG", "SHORT"):
        raise SnapshotError(f"{label}.direction must be LONG or SHORT, got {direction!r}.")
    return Position(
        asset=_str(payload["asset"], f"{label}.asset"),
        product_id=_str(payload["product_id"], f"{label}.product_id"),
        direction=direction,
        contracts=_decimal(payload["contracts"], f"{label}.contracts"),
        average_entry_price=_decimal(payload["average_entry_price"], f"{label}.average_entry_price"),
        current_price=_decimal(payload["current_price"], f"{label}.current_price"),
        unrealized_pnl=_decimal(payload["unrealized_pnl"], f"{label}.unrealized_pnl"),
        opened_at=_optional_str(payload["opened_at"], f"{label}.opened_at"),
        provenance=_optional_str(payload["provenance"], f"{label}.provenance"),
    )


def _open_order_from_dict(payload: Any, label: str) -> OpenOrder:
    payload = _mapping(payload, label)
    _exact_keys(payload, OpenOrder, label)
    return OpenOrder(
        order_id=_str(payload["order_id"], f"{label}.order_id"),
        client_order_id=_str(payload["client_order_id"], f"{label}.client_order_id"),
        product_id=_str(payload["product_id"], f"{label}.product_id"),
        side=_str(payload["side"], f"{label}.side"),
        status=_str(payload["status"], f"{label}.status"),
        order_kind=_str(payload["order_kind"], f"{label}.order_kind"),
        base_size=_decimal(payload["base_size"], f"{label}.base_size"),
        limit_price=_optional_decimal(payload["limit_price"], f"{label}.limit_price"),
        stop_price=_optional_decimal(payload["stop_price"], f"{label}.stop_price"),
        trigger_status=_optional_str(payload["trigger_status"], f"{label}.trigger_status"),
        created_time=_optional_str(payload["created_time"], f"{label}.created_time"),
    )


def _account_from_dict(payload: Any) -> Optional[AccountView]:
    if payload is None:
        return None
    payload = _mapping(payload, "account")
    _exact_keys(payload, AccountView, "account")
    window = _str(payload["margin_window"], "account.margin_window")
    if window not in MARGIN_WINDOWS:
        raise SnapshotError(f"account.margin_window must be one of {MARGIN_WINDOWS}, got {window!r}.")
    return AccountView(
        equity=_decimal(payload["equity"], "account.equity"),
        initial_margin=_decimal(payload["initial_margin"], "account.initial_margin"),
        cash_balance=_decimal(payload["cash_balance"], "account.cash_balance"),
        futures_buying_power=_decimal(payload["futures_buying_power"], "account.futures_buying_power"),
        daily_realized_pnl=_decimal(payload["daily_realized_pnl"], "account.daily_realized_pnl"),
        unrealized_pnl=_decimal(payload["unrealized_pnl"], "account.unrealized_pnl"),
        liquidation_threshold=_optional_decimal(payload["liquidation_threshold"], "account.liquidation_threshold"),
        liquidation_buffer_amount=_optional_decimal(payload["liquidation_buffer_amount"], "account.liquidation_buffer_amount"),
        margin_window=window,  # type: ignore[arg-type]
        margin_window_ends_at=_optional_str(payload["margin_window_ends_at"], "account.margin_window_ends_at"),
        intraday_margin_enabled=_optional_bool(payload["intraday_margin_enabled"], "account.intraday_margin_enabled"),
    )


def _candle_from_dict(payload: Any, label: str) -> Candle:
    payload = _mapping(payload, label)
    _exact_keys(payload, Candle, label)
    return Candle(
        start=_int(payload["start"], f"{label}.start"),
        low=_decimal(payload["low"], f"{label}.low"),
        high=_decimal(payload["high"], f"{label}.high"),
        open=_decimal(payload["open"], f"{label}.open"),
        close=_decimal(payload["close"], f"{label}.close"),
        volume=_decimal(payload["volume"], f"{label}.volume"),
    )


def _hourly_bar_from_dict(payload: Any, label: str) -> HourlyBar:
    payload = _mapping(payload, label)
    _exact_keys(payload, HourlyBar, label)
    return HourlyBar(start=_int(payload["start"], f"{label}.start"), close=_decimal(payload["close"], f"{label}.close"))


def _status_map(payload: Any, label: str) -> dict[str, str]:
    payload = _mapping(payload, label)
    return {_str(key, f"{label} key"): _str(value, f"{label}[{key}]") for key, value in payload.items()}


def _market_from_dict(payload: Any) -> MarketView:
    payload = _mapping(payload, "market")
    _exact_keys(payload, MarketView, "market")
    daily = {
        _str(asset, "market.daily key"): tuple(
            _candle_from_dict(item, f"market.daily[{asset}][{index}]")
            for index, item in enumerate(_list(rows, f"market.daily[{asset}]"))
        )
        for asset, rows in _mapping(payload["daily"], "market.daily").items()
    }
    hourly = {
        _str(asset, "market.hourly key"): tuple(
            _hourly_bar_from_dict(item, f"market.hourly[{asset}][{index}]")
            for index, item in enumerate(_list(rows, f"market.hourly[{asset}]"))
        )
        for asset, rows in _mapping(payload["hourly"], "market.hourly").items()
    }
    return MarketView(
        daily=daily, hourly=hourly,
        daily_status=_status_map(payload["daily_status"], "market.daily_status"),
        hourly_status=_status_map(payload["hourly_status"], "market.hourly_status"),
    )


def _evidence_event_from_dict(payload: Any, label: str) -> EvidenceEvent:
    payload = _mapping(payload, label)
    _exact_keys(payload, EvidenceEvent, label)
    direction = _str(payload["direction"], f"{label}.direction")
    if direction not in EVIDENCE_DIRECTIONS:
        raise SnapshotError(f"{label}.direction must be one of {EVIDENCE_DIRECTIONS}, got {direction!r}.")
    return EvidenceEvent(
        event_id=_str(payload["event_id"], f"{label}.event_id"),
        direction=direction,  # type: ignore[arg-type]
        assets=_str_tuple(payload["assets"], f"{label}.assets"),
        confidence=_decimal(payload["confidence"], f"{label}.confidence"),
        source_url=_str(payload["source_url"], f"{label}.source_url"),
        published_at=_str(payload["published_at"], f"{label}.published_at"),
        quote_sha256=_str(payload["quote_sha256"], f"{label}.quote_sha256"),
    )


def _evidence_from_dict(payload: Any) -> EvidenceView:
    payload = _mapping(payload, "evidence")
    _exact_keys(payload, EvidenceView, "evidence")
    return EvidenceView(
        status=_str(payload["status"], "evidence.status"),
        artifact_digest=_optional_str(payload["artifact_digest"], "evidence.artifact_digest"),
        generated_at=_optional_str(payload["generated_at"], "evidence.generated_at"),
        events=tuple(
            _evidence_event_from_dict(item, f"evidence.events[{index}]")
            for index, item in enumerate(_list(payload["events"], "evidence.events"))
        ),
    )


def snapshot_from_dict(payload: Mapping[str, Any]) -> PulseSnapshot:
    """Exact inverse of ``PulseSnapshot.to_dict()``.

    Strict: a missing or unknown key, a wrong type, an unknown margin window or
    evidence direction raises ``SnapshotError``.  ``account`` may be ``null``.
    The rebuilt snapshot digests to the same value as the original.
    """
    payload = _mapping(payload, "snapshot")
    _exact_keys(payload, PulseSnapshot, "snapshot")
    schema_version = _int(payload["schema_version"], "snapshot.schema_version")
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError(f"snapshot.schema_version must be {SNAPSHOT_SCHEMA_VERSION}, got {schema_version}.")
    permissions = {
        _str(key, "snapshot.permissions key"): _bool(value, f"snapshot.permissions[{key}]")
        for key, value in _mapping(payload["permissions"], "snapshot.permissions").items()
    }
    products = {
        _str(asset, "snapshot.products key"): _product_from_dict(item, f"snapshot.products[{asset}]")
        for asset, item in _mapping(payload["products"], "snapshot.products").items()
    }
    quotes = {
        _str(key, "snapshot.quotes key"): _quote_from_dict(item, f"snapshot.quotes[{key}]")
        for key, item in _mapping(payload["quotes"], "snapshot.quotes").items()
    }
    positions = {
        _str(asset, "snapshot.positions key"): _position_from_dict(item, f"snapshot.positions[{asset}]")
        for asset, item in _mapping(payload["positions"], "snapshot.positions").items()
    }
    open_orders = tuple(
        _open_order_from_dict(item, f"snapshot.open_orders[{index}]")
        for index, item in enumerate(_list(payload["open_orders"], "snapshot.open_orders"))
    )
    return PulseSnapshot(
        schema_version=schema_version,
        slot_id=_str(payload["slot_id"], "snapshot.slot_id"),
        slot_kind=_str(payload["slot_kind"], "snapshot.slot_kind"),
        scheduled_for=_str(payload["scheduled_for"], "snapshot.scheduled_for"),
        captured_at=_str(payload["captured_at"], "snapshot.captured_at"),
        clock=_clock_from_dict(payload["clock"]),
        permissions=permissions,
        products=products,
        quotes=quotes,
        positions=positions,
        open_orders=open_orders,
        account=_account_from_dict(payload["account"]),
        market=_market_from_dict(payload["market"]),
        evidence=_evidence_from_dict(payload["evidence"]),
        policy_digest=_str(payload["policy_digest"], "snapshot.policy_digest"),
        notes=_str_tuple(payload["notes"], "snapshot.notes"),
    )
