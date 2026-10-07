"""Hash-chained event log: append/verify, tamper detection, immutability triggers, genesis, canonical JSON."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from pulse_trader.events import (
    GENESIS_KIND,
    GENESIS_PREV_HASH,
    ChainReport,
    EventLogError,
    EventRecord,
    append,
    canonical_json,
    ensure_schema,
    event_hash,
    events_for,
    events_for_step,
    genesis,
    head,
    latest,
    verify_chain,
)

T0 = "2000-01-01T00:00:00+00:00"
SHA = "a" * 64


class EventsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        ensure_schema(self.db)
        self.db.commit()

    def raw(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        return connection

    def add(self, n: int, scope: str = "slot-1") -> list[EventRecord]:
        return [
            append(self.db, scope_id=scope, step_id=f"step-{i}", kind=f"kind.{i}", payload={"i": i}, recorded_at=T0)
            for i in range(1, n + 1)
        ]


class AppendAndVerifyTests(EventsCase):
    def test_chain_starts_at_genesis_prev_hash_and_verifies(self) -> None:
        records = self.add(3)
        self.assertEqual([r.seq for r in records], [1, 2, 3])
        self.assertEqual(records[0].prev_hash, GENESIS_PREV_HASH)
        self.assertEqual(records[1].prev_hash, records[0].hash)
        self.assertEqual(records[2].prev_hash, records[1].hash)
        report = verify_chain(self.db)
        self.assertEqual(report, ChainReport(ok=True, count=3, head=records[2].hash, first_bad_seq=None))
        self.assertEqual(head(self.db), records[2])

    def test_empty_chain_is_valid(self) -> None:
        self.assertEqual(verify_chain(self.db), ChainReport(ok=True, count=0, head=None, first_bad_seq=None))
        self.assertIsNone(head(self.db))

    def test_hash_matches_the_declared_definition(self) -> None:
        record = append(self.db, scope_id="s", step_id="t", kind="k", payload={"b": 2, "a": Decimal("1.5")}, recorded_at=T0)
        body = json.dumps(
            {"seq": 1, "scope_id": "s", "step_id": "t", "kind": "k", "schema_version": 1, "recorded_at": T0,
             "payload": {"a": "1.5", "b": 2}},
            sort_keys=True, separators=(",", ":"),
        )
        self.assertEqual(record.hash, hashlib.sha256((GENESIS_PREV_HASH + body).encode("utf-8")).hexdigest())
        self.assertEqual(record.hash, event_hash(prev_hash=GENESIS_PREV_HASH, seq=1, scope_id="s", step_id="t", kind="k",
                                                 schema_version=1, recorded_at=T0, payload={"a": "1.5", "b": 2}))
        stored = self.db.execute("SELECT payload_json, hash FROM events WHERE seq=1").fetchone()
        self.assertEqual(stored, ('{"a":"1.5","b":2}', record.hash))

    def test_required_fields_are_validated(self) -> None:
        for bad in (dict(scope_id=""), dict(step_id=" "), dict(kind=""), dict(recorded_at=""), dict(schema_version=0),
                    dict(schema_version=True), dict(payload=["not", "a", "mapping"])):
            kwargs = dict(scope_id="s", step_id="t", kind="k", payload={}, recorded_at=T0)
            kwargs.update(bad)
            with self.assertRaises(EventLogError, msg=str(bad)):
                append(self.db, **kwargs)
        self.assertEqual(verify_chain(self.db).count, 0)

    def test_verify_requires_the_table(self) -> None:
        bare = sqlite3.connect(":memory:")
        self.addCleanup(bare.close)
        with self.assertRaises(EventLogError):
            verify_chain(bare)
        with self.assertRaises(EventLogError):
            append(bare, scope_id="s", step_id="t", kind="k", payload={}, recorded_at=T0)


class TamperTests(EventsCase):
    def test_tampered_payload_reports_first_bad_seq(self) -> None:
        records = self.add(3)
        raw = self.raw()
        raw.execute("DROP TRIGGER events_no_update")
        raw.execute("UPDATE events SET payload_json=? WHERE seq=2", ('{"i":99}',))
        raw.commit()
        report = verify_chain(self.db)
        self.assertFalse(report.ok)
        self.assertEqual(report.first_bad_seq, 2)
        self.assertEqual(report.count, 3)
        self.assertEqual(report.head, records[2].hash)

    def test_rewritten_hash_is_detected_by_the_next_link(self) -> None:
        self.add(3)
        raw = self.raw()
        raw.execute("DROP TRIGGER events_no_update")
        raw.execute("UPDATE events SET payload_json=?, hash=? WHERE seq=2", ('{"i":99}', "f" * 64))
        raw.commit()
        report = verify_chain(self.db)
        self.assertEqual((report.ok, report.first_bad_seq), (False, 2))

    def test_deleted_row_breaks_the_chain(self) -> None:
        self.add(3)
        raw = self.raw()
        raw.execute("DROP TRIGGER events_no_delete")
        raw.execute("DELETE FROM events WHERE seq=2")
        raw.commit()
        report = verify_chain(self.db)
        self.assertEqual((report.ok, report.count, report.first_bad_seq), (False, 2, 3))

    def test_update_and_delete_are_refused_by_triggers(self) -> None:
        records = self.add(2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE events SET payload_json='{}' WHERE seq=1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM events WHERE seq=2")
        self.db.rollback()
        self.assertEqual(verify_chain(self.db), ChainReport(ok=True, count=2, head=records[1].hash, first_bad_seq=None))
        self.assertEqual(self.db.execute("SELECT payload_json FROM events WHERE seq=1").fetchone()[0], '{"i":1}')


class GenesisTests(EventsCase):
    def test_genesis_records_manifest_as_first_event(self) -> None:
        record = genesis(self.db, {"fixture-a.txt": SHA, "fixture-b.txt": "b" * 64}, T0)
        self.assertEqual((record.seq, record.kind, record.prev_hash), (1, GENESIS_KIND, GENESIS_PREV_HASH))
        self.assertEqual(record.payload, {"manifest": {"fixture-b.txt": "b" * 64, "fixture-a.txt": SHA}, "file_count": 2})
        self.assertTrue(verify_chain(self.db).ok)

    def test_genesis_refuses_when_events_exist(self) -> None:
        self.add(1)
        with self.assertRaises(EventLogError):
            genesis(self.db, {"fixture-a.txt": SHA}, T0)
        self.assertEqual(verify_chain(self.db).count, 1)
        genesis_first = sqlite3.connect(":memory:")
        self.addCleanup(genesis_first.close)
        ensure_schema(genesis_first)
        genesis(genesis_first, {"fixture-a.txt": SHA}, T0)
        with self.assertRaises(EventLogError):
            genesis(genesis_first, {"fixture-a.txt": SHA}, T0)
        self.assertEqual(verify_chain(genesis_first).count, 1)

    def test_manifest_digests_are_validated(self) -> None:
        for manifest in ({"f": "abc"}, {"f": "A" * 64}, {"": SHA}, {"f": 12}, ["not", "a", "mapping"]):
            with self.assertRaises(EventLogError, msg=repr(manifest)):
                genesis(self.db, manifest, T0)  # type: ignore[arg-type]
        self.assertEqual(verify_chain(self.db).count, 0)


class CanonicalJsonTests(EventsCase):
    def test_decimal_payloads_are_canonical_and_order_independent(self) -> None:
        one = {"z": Decimal("1.50"), "a": {"n": Decimal("2")}, "list": [Decimal("0.1"), 3, "x"]}
        two = {"list": [Decimal("0.1"), 3, "x"], "a": {"n": Decimal("2")}, "z": Decimal("1.50")}
        self.assertEqual(canonical_json(one), '{"a":{"n":"2"},"list":["0.1",3,"x"],"z":"1.50"}')
        self.assertEqual(canonical_json(one), canonical_json(two))
        first = append(self.db, scope_id="s", step_id="t", kind="k", payload=one, recorded_at=T0)
        self.assertEqual(first.payload, {"a": {"n": "2"}, "list": ["0.1", 3, "x"], "z": "1.50"})
        expected = event_hash(prev_hash=GENESIS_PREV_HASH, seq=1, scope_id="s", step_id="t", kind="k",
                              schema_version=1, recorded_at=T0, payload=json.loads(canonical_json(two)))
        self.assertEqual(first.hash, expected)
        self.assertTrue(verify_chain(self.db).ok)

    def test_non_canonical_values_are_refused(self) -> None:
        for payload in ({"x": float("nan")}, {"x": float("inf")}, {"x": Decimal("NaN")}, {"x": Decimal("Infinity")},
                        {"x": object()}, {"x": {1, 2}}, {"x": b"bytes"}):
            with self.assertRaises(EventLogError, msg=repr(payload)):
                canonical_json(payload)
            with self.assertRaises(EventLogError, msg=repr(payload)):
                append(self.db, scope_id="s", step_id="t", kind="k", payload=payload, recorded_at=T0)
        self.assertEqual(verify_chain(self.db).count, 0)


class QueryAndTransactionTests(EventsCase):
    def test_latest_and_events_for_filter_by_scope(self) -> None:
        a = self.add(2, scope="slot-a")
        b = self.add(1, scope="slot-b")
        self.assertEqual(latest(self.db, "slot-a"), a[1])
        self.assertEqual(latest(self.db, "slot-b"), b[0])
        self.assertIsNone(latest(self.db, "slot-c"))
        self.assertEqual(list(events_for(self.db, "slot-a")), a)
        self.assertEqual(list(events_for(self.db, "slot-c")), [])
        self.assertEqual(list(events_for_step(self.db, "step-1")), [a[0], b[0]])
        self.assertEqual(list(events_for_step(self.db, "step-1", "kind.1")), [a[0], b[0]])
        self.assertEqual(list(events_for_step(self.db, "step-1", "kind.2")), [])

    def test_append_joins_an_open_transaction_and_rolls_back_with_it(self) -> None:
        self.add(1)
        self.db.execute("BEGIN IMMEDIATE")
        inside = append(self.db, scope_id="s", step_id="t", kind="k", payload={}, recorded_at=T0)
        self.assertEqual(inside.seq, 2)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(verify_chain(self.db).count, 1)
        after = append(self.db, scope_id="s", step_id="t", kind="k", payload={}, recorded_at=T0)
        self.assertEqual(after.seq, 2)  # a rolled-back sequence number is reused, never skipped
        self.assertTrue(verify_chain(self.db).ok)

    def test_standalone_append_is_committed_and_visible_elsewhere(self) -> None:
        record = self.add(1)[0]
        self.assertFalse(self.db.in_transaction)
        other = self.raw()
        self.assertEqual(other.execute("SELECT hash FROM events WHERE seq=1").fetchone()[0], record.hash)

    def test_autocommit_connection_is_supported(self) -> None:
        auto = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(auto.close)
        record = append(auto, scope_id="s", step_id="t", kind="k", payload={"n": 1}, recorded_at=T0)
        self.assertFalse(auto.in_transaction)
        self.assertEqual(verify_chain(self.db), ChainReport(ok=True, count=1, head=record.hash, first_bad_seq=None))


if __name__ == "__main__":
    unittest.main()
