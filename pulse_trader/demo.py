"""A deterministic, invented scenario using an in-memory SQLite database."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from decimal import Decimal

from pulse_trader.events import append, ensure_schema, genesis, verify_chain
from pulse_trader.graph.intents import EntryIntent, intent_from_dict, intent_to_dict
from pulse_trader.graph.snapshot import (
    ClockView, EvidenceView, MarketView, PulseSnapshot, Quote, digest, snapshot_from_dict,
)
from pulse_trader.graph.states import SlotState, StateError, assert_transition, machines
from pulse_trader.indicators import calculate
from pulse_trader.models import Candle, Product

SYNTHETIC_TIME = "2000-01-01T00:00:00+00:00"


def synthetic_candles(count: int = 320) -> tuple[Candle, ...]:
    """Arithmetic values only; no recordings or downloaded market data."""
    return tuple(
        Candle(i * 86400, Decimal(99 + i), Decimal(101 + i),
               Decimal(100 + i), Decimal(100 + i), Decimal(10 + i))
        for i in range(count)
    )


def synthetic_snapshot() -> PulseSnapshot:
    product = Product(
        asset="BTC", product_id="SYNTH-PRODUCT", spot_product_id="SYNTH-SPOT",
        price=Decimal("100"), index_price=Decimal("100"), contract_size=Decimal("1"),
        long_margin_rate=Decimal("0.1"), short_margin_rate=Decimal("0.1"),
        funding_rate=None, funding_interval_seconds=0, tradable=False,
    )
    return PulseSnapshot(
        schema_version=1, slot_id="synthetic-slot-001", slot_kind="PULSE",
        scheduled_for=SYNTHETIC_TIME, captured_at=SYNTHETIC_TIME,
        clock=ClockView(SYNTHETIC_TIME, None, None), permissions={"execution": False},
        products={"BTC": product},
        quotes={"SYNTH-PRODUCT": Quote("SYNTH-PRODUCT", Decimal("99"), Decimal("101"), SYNTHETIC_TIME)},
        positions={}, open_orders=(), account=None,
        market=MarketView(daily={"BTC": synthetic_candles()}, daily_status={"BTC": "SYNTHETIC"}),
        evidence=EvidenceView(status="SYNTHETIC"),
        policy_digest=digest({"scenario": "synthetic-only", "execution": False}),
        notes=("ALL_INPUTS_INVENTED",),
    )


def synthetic_intent(snapshot: PulseSnapshot) -> EntryIntent:
    """An inert data object with invented values, not a strategy decision."""
    return EntryIntent(
        schema_version=1, slot_id=snapshot.slot_id, asset="BTC", product_id="SYNTH-PRODUCT",
        side="BUY", contracts=Decimal("1"), reference_price=Decimal("100"),
        max_fill_price=Decimal("101"), stop_price=Decimal("95"), stop_limit_price=Decimal("94"),
        stop_distance_fraction=Decimal("0.05"), sigma_horizon=Decimal("0.02"),
        risk_unit_usd=Decimal("6"), loss_at_stop_usd=Decimal("6"), max_loss_usd=Decimal("7"),
        policy_digest=snapshot.policy_digest, snapshot_digest=snapshot.digest(),
        sizing_digest=digest({"synthetic_contracts": "1"}),
        gate_record={"showcase": "INERT_EXAMPLE"}, created_at=SYNTHETIC_TIME,
    )


def run_demo() -> dict:
    snapshot = synthetic_snapshot()
    intent = synthetic_intent(snapshot)
    path = (SlotState.SCHEDULED, SlotState.ATTEMPTED, SlotState.RUNNING, SlotState.COMPLETED)
    valid_edges = [(left, assert_transition(left, right)) for left, right in zip(path, path[1:])]
    try:
        assert_transition(SlotState.SCHEDULED, SlotState.COMPLETED)
    except StateError:
        invalid_refused = True
    else:
        raise RuntimeError("An undeclared transition was accepted.")

    roundtrip = snapshot_from_dict(snapshot.to_dict())
    rebuilt_intent = intent_from_dict(intent_to_dict(intent))
    checks = {
        "snapshot_roundtrip_equal": roundtrip.digest() == snapshot.digest(),
        "intent_roundtrip_equal": rebuilt_intent == intent,
        "intent_id_repeatable": intent.client_order_id() == rebuilt_intent.client_order_id(),
        "different_attempt_has_different_id": intent.client_order_id(1) != intent.client_order_id(2),
    }
    if not all(checks.values()):
        raise RuntimeError("Deterministic serialization check failed.")

    with closing(sqlite3.connect(":memory:")) as db:
        ensure_schema(db)
        genesis(db, {"synthetic-input": snapshot.digest()}, SYNTHETIC_TIME)
        for left, right in valid_edges:
            append(db, scope_id=snapshot.slot_id, step_id=right.value, kind="demo.transition",
                   payload={"from": left.value, "to": right.value}, recorded_at=SYNTHETIC_TIME)
        append(db, scope_id=snapshot.slot_id, step_id="snapshot", kind="demo.snapshot",
               payload={"digest": snapshot.digest()}, recorded_at=SYNTHETIC_TIME)
        append(db, scope_id=snapshot.slot_id, step_id="intent", kind="demo.intent",
               payload=intent_to_dict(intent), recorded_at=SYNTHETIC_TIME)
        before = verify_chain(db)
        blocked = 0
        for statement in ("UPDATE events SET payload_json='{}' WHERE seq=3", "DELETE FROM events WHERE seq=3"):
            try:
                db.execute(statement)
            except sqlite3.IntegrityError:
                blocked += 1
        db.rollback()
        # This deliberate bypass affects only this disposable in-memory database.
        db.execute("DROP TRIGGER events_no_update")
        db.execute("UPDATE events SET payload_json='{}' WHERE seq=3")
        db.commit()
        after = verify_chain(db)
        if not before.ok or blocked != 2 or after.ok or after.first_bad_seq != 3:
            raise RuntimeError("Event-integrity demonstration failed.")

    return {
        "mode": "synthetic-offline",
        "state_machines": len(tuple(machines())),
        "valid_path": [state.value for state in path],
        "invalid_transition_refused": invalid_refused,
        "determinism": checks,
        "snapshot_sha256": snapshot.digest(),
        "synthetic_candle_count": calculate(list(synthetic_candles())).completed_candles,
        "events": {"count": before.count, "verified_before_tamper": before.ok,
                   "ordinary_mutations_blocked": blocked, "tampering_detected": not after.ok,
                   "first_bad_sequence": after.first_bad_seq},
    }


def main() -> None:
    print(json.dumps(run_demo(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
