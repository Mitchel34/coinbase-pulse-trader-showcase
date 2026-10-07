"""Synthetic end-to-end checks and explicit limits of the showcase core."""
from __future__ import annotations

import ast
import sqlite3
import unittest
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pulse_trader
from pulse_trader.demo import SYNTHETIC_TIME, run_demo, synthetic_candles, synthetic_intent, synthetic_snapshot
from pulse_trader.events import append, ensure_schema, event_hash, verify_chain
from pulse_trader.graph.intents import client_order_id, intent_from_dict, intent_to_dict
from pulse_trader.graph.snapshot import SnapshotError, digest, snapshot_from_dict
from pulse_trader.indicators import IndicatorError, calculate


class ShowcaseTests(unittest.TestCase):
    def test_demo_detects_tampering_and_refuses_invalid_transition(self):
        report = run_demo()
        self.assertTrue(report["invalid_transition_refused"])
        self.assertEqual(report["events"], {
            "count": 6, "verified_before_tamper": True, "ordinary_mutations_blocked": 2,
            "tampering_detected": True, "first_bad_sequence": 3,
        })
        self.assertTrue(all(report["determinism"].values()))

    def test_repeated_demo_results_are_identical(self):
        self.assertEqual(run_demo(), run_demo())

    def test_demo_runs_with_socket_creation_blocked(self):
        with patch("socket.socket", side_effect=AssertionError("Network access attempted")):
            self.assertEqual(run_demo()["mode"], "synthetic-offline")

    def test_package_imports_only_reviewed_standard_library_modules(self):
        allowed = {"__future__", "contextlib", "dataclasses", "decimal", "enum", "hashlib",
                   "json", "sqlite3", "statistics", "typing", "uuid", "pulse_trader"}
        root = Path(pulse_trader.__file__).parent
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                else:
                    names = []
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, str(path.name))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec", "__import__", "open"})


class SerializationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = synthetic_snapshot()
        self.intent = synthetic_intent(self.snapshot)

    def test_snapshot_roundtrip_preserves_payload_and_digest(self):
        restored = snapshot_from_dict(self.snapshot.to_dict())
        self.assertEqual(restored.to_dict(), self.snapshot.to_dict())
        self.assertEqual(restored.digest(), self.snapshot.digest())

    def test_mapping_order_does_not_change_digest(self):
        data = self.snapshot.to_dict()
        self.assertEqual(digest(data), digest(dict(reversed(list(data.items())))))

    def test_changed_snapshot_value_changes_digest(self):
        changed = replace(self.snapshot, slot_id="synthetic-slot-002")
        self.assertNotEqual(changed.digest(), self.snapshot.digest())

    def test_missing_unknown_and_wrong_typed_fields_are_refused(self):
        for mutation in ("missing", "unknown", "wrong_bool", "wrong_decimal", "wrong_version"):
            data = self.snapshot.to_dict()
            if mutation == "missing":
                del data["captured_at"]
            elif mutation == "unknown":
                data["unexpected"] = True
            elif mutation == "wrong_bool":
                data["permissions"]["execution"] = "false"
            elif mutation == "wrong_decimal":
                data["products"]["BTC"]["price"] = 100
            else:
                data["schema_version"] = 2
            with self.subTest(mutation=mutation), self.assertRaises(SnapshotError):
                snapshot_from_dict(data)

    def test_shallow_frozen_limit_is_explicit(self):
        with self.assertRaises(FrozenInstanceError):
            self.snapshot.slot_id = "synthetic-change"
        previous = self.snapshot.digest()
        self.snapshot.permissions["execution"] = True
        self.assertNotEqual(self.snapshot.digest(), previous)

    def test_decimal_spelling_is_preserved_not_normalized(self):
        self.assertNotEqual(digest(Decimal("1.0")), digest(Decimal("1.00")))

    def test_nonfinite_decimal_roundtrip_is_not_market_validation(self):
        data = self.snapshot.to_dict()
        data["products"]["BTC"]["funding_rate"] = "NaN"
        self.assertEqual(snapshot_from_dict(data).to_dict(), data)

    def test_intent_roundtrip_and_identity(self):
        rebuilt = intent_from_dict(intent_to_dict(self.intent))
        self.assertEqual(rebuilt, self.intent)
        self.assertEqual(rebuilt.core_digest(), self.intent.core_digest())
        self.assertEqual(rebuilt.client_order_id(), self.intent.client_order_id())

    def test_attempt_and_lineage_separate_identifiers(self):
        self.assertNotEqual(client_order_id("synthetic-a", 1), client_order_id("synthetic-a", 2))
        self.assertNotEqual(client_order_id("synthetic-a", 1), client_order_id("synthetic-b", 1))
        with self.assertRaises(ValueError):
            client_order_id("synthetic-a", 0)

    def test_quote_envelope_is_separate_from_core_identity(self):
        changed = replace(self.intent, max_fill_price=Decimal("102"))
        self.assertEqual(changed.core_digest(), self.intent.core_digest())
        self.assertNotEqual(changed.envelope_digest(), self.intent.envelope_digest())

    def test_intent_unknown_and_missing_fields_are_refused(self):
        data = intent_to_dict(self.intent)
        data["unexpected"] = True
        with self.assertRaises(ValueError):
            intent_from_dict(data)
        del data["unexpected"]
        del data["contracts"]
        with self.assertRaises(ValueError):
            intent_from_dict(data)


class IntegrityLimitTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        ensure_schema(self.db)
        for i in range(3):
            append(self.db, scope_id="synthetic", step_id=str(i), kind="demo",
                   payload={"value": i}, recorded_at=SYNTHETIC_TIME)
        self.trusted = verify_chain(self.db)

    def test_tail_deletion_needs_an_external_count_or_head(self):
        self.db.execute("DROP TRIGGER events_no_delete")
        self.db.execute("DELETE FROM events WHERE seq=3")
        self.db.commit()
        current = verify_chain(self.db)
        self.assertTrue(current.ok)
        self.assertNotEqual(current.count, self.trusted.count)
        self.assertNotEqual(current.head, self.trusted.head)

    def test_complete_rehash_needs_an_external_trusted_head(self):
        self.db.execute("DROP TRIGGER events_no_update")
        previous = "0" * 64
        for seq in range(1, 4):
            checksum = event_hash(prev_hash=previous, seq=seq, scope_id="synthetic",
                                  step_id=str(seq - 1), kind="demo", schema_version=1,
                                  recorded_at=SYNTHETIC_TIME, payload={"value": 99})
            self.db.execute("UPDATE events SET payload_json=?, prev_hash=?, hash=? WHERE seq=?",
                            ('{"value":99}', previous, checksum, seq))
            previous = checksum
        self.db.commit()
        current = verify_chain(self.db)
        self.assertTrue(current.ok)
        self.assertNotEqual(current.head, self.trusted.head)


class SyntheticIndicatorTests(unittest.TestCase):
    def test_duplicate_timestamp_is_refused(self):
        rows = list(synthetic_candles())
        rows[-1] = rows[-2]
        with self.assertRaises(IndicatorError):
            calculate(rows)

    def test_nonpositive_close_and_negative_volume_are_refused(self):
        for change in ({"close": Decimal(0)}, {"volume": Decimal(-1)}):
            rows = list(synthetic_candles())
            rows[-1] = replace(rows[-1], **change)
            with self.assertRaises(IndicatorError):
                calculate(rows)

    def test_input_order_does_not_change_indicators(self):
        rows = list(synthetic_candles())
        self.assertEqual(calculate(rows), calculate(list(reversed(rows))))


if __name__ == "__main__":
    unittest.main()
