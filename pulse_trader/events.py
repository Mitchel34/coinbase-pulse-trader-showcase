"""Hash-chained SQLite event log.

Append joins the caller transaction or starts its own. Update/delete triggers
reject ordinary rewrites; verify_chain checks contiguous sequence numbers,
links, and payload hashes. Detecting tail deletion or a fully recomputed
chain requires comparison with a separately trusted count and head.

The module performs I/O only through the SQLite connection it receives.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator, Mapping, Optional

EVENT_SCHEMA_VERSION = 1
GENESIS_PREV_HASH = "0" * 64
GENESIS_SCOPE_ID = "genesis"
GENESIS_STEP_ID = "archive"
GENESIS_KIND = "genesis.archive_manifest"

EVENTS_SCHEMA: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  recorded_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE
)""",
    "CREATE INDEX IF NOT EXISTS events_scope_seq ON events(scope_id, seq)",
    "CREATE INDEX IF NOT EXISTS events_step_kind_seq ON events(step_id, kind, seq)",
    """CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
)

_COLUMNS = "seq, scope_id, step_id, kind, schema_version, recorded_at, payload_json, prev_hash, hash"


class EventLogError(RuntimeError):
    """Raised when the log would be started twice, a payload cannot be canonicalised, or the table is missing."""


@dataclass(frozen=True)
class EventRecord:
    """One appended row.  ``payload`` is the stored (canonical) form, never the caller's object."""

    seq: int
    hash: str
    prev_hash: str
    scope_id: str
    step_id: str
    kind: str
    schema_version: int
    recorded_at: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ChainReport:
    """``head`` is the last stored hash even when the chain is broken; ``first_bad_seq`` locates the break."""

    ok: bool
    count: int
    head: Optional[str]
    first_bad_seq: Optional[int]


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("non-finite Decimal is not canonically serialisable")
        return str(value)
    raise TypeError(f"{type(value).__name__} is not canonically serialisable")


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, Decimal as string, NaN/Infinity refused."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_default)
    except (TypeError, ValueError) as error:
        raise EventLogError(f"Payload is not canonically serialisable: {error}") from error


def event_hash(*, prev_hash: str, seq: int, scope_id: str, step_id: str, kind: str,
               schema_version: int, recorded_at: str, payload: Any) -> str:
    """sha256(prev_hash + canonical JSON of the row body).  ``payload`` must be the stored form."""
    body = canonical_json({
        "seq": seq, "scope_id": scope_id, "step_id": step_id, "kind": kind,
        "schema_version": schema_version, "recorded_at": recorded_at, "payload": payload,
    })
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def ensure_schema(db: sqlite3.Connection) -> None:
    """Create the table, indexes and immutability triggers if absent.  Idempotent."""
    for statement in EVENTS_SCHEMA:
        db.execute(statement)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventLogError(f"{field} must be a non-empty string.")
    return value


def _table_present(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone() is not None


def _record(row: Any) -> EventRecord:
    return EventRecord(
        seq=int(row[0]), scope_id=str(row[1]), step_id=str(row[2]), kind=str(row[3]),
        schema_version=int(row[4]), recorded_at=str(row[5]), payload=json.loads(row[6]),
        prev_hash=str(row[7]), hash=str(row[8]),
    )


def append(db: sqlite3.Connection, *, scope_id: str, step_id: str, kind: str, payload: Mapping[str, Any],
           schema_version: int = EVENT_SCHEMA_VERSION, recorded_at: str) -> EventRecord:
    """Append one event.  Joins the caller's open transaction, otherwise runs its own BEGIN IMMEDIATE.

    The sequence number is always head + 1 and the hash binds it, so a concurrent
    writer that slipped in without a write lock fails on the primary key instead of
    silently forking the chain.
    """
    _require_text(scope_id, "scope_id")
    _require_text(step_id, "step_id")
    _require_text(kind, "kind")
    _require_text(recorded_at, "recorded_at")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise EventLogError("schema_version must be a positive integer.")
    if not isinstance(payload, Mapping):
        raise EventLogError("payload must be a mapping.")
    payload_json = canonical_json(payload)
    stored_payload = json.loads(payload_json)
    if not _table_present(db):
        raise EventLogError("events table is missing; run the schema migration first.")
    own_transaction = not db.in_transaction
    if own_transaction:
        db.execute("BEGIN IMMEDIATE")
    try:
        head = db.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq = int(head[0]) + 1 if head else 1
        prev_hash = str(head[1]) if head else GENESIS_PREV_HASH
        digest = event_hash(
            prev_hash=prev_hash, seq=seq, scope_id=scope_id, step_id=step_id, kind=kind,
            schema_version=schema_version, recorded_at=recorded_at, payload=stored_payload,
        )
        db.execute(
            f"INSERT INTO events({_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?)",
            (seq, scope_id, step_id, kind, schema_version, recorded_at, payload_json, prev_hash, digest),
        )
        if own_transaction:
            db.execute("COMMIT")
    except BaseException:
        if own_transaction and db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return EventRecord(
        seq=seq, hash=digest, prev_hash=prev_hash, scope_id=scope_id, step_id=step_id, kind=kind,
        schema_version=schema_version, recorded_at=recorded_at, payload=stored_payload,
    )


def genesis(db: sqlite3.Connection, manifest: Mapping[str, str], recorded_at: str) -> EventRecord:
    """Record the archive manifest (file -> sha256) as event 1.  Refuses if any event already exists."""
    if not isinstance(manifest, Mapping):
        raise EventLogError("Genesis manifest must map file names to sha256 digests.")
    entries: dict[str, str] = {}
    for name, digest in manifest.items():
        if not isinstance(name, str) or not name.strip():
            raise EventLogError("Genesis manifest file names must be non-empty strings.")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise EventLogError(f"Genesis manifest digest for {name!r} must be 64 lowercase hex characters.")
        entries[name] = digest
    if not _table_present(db):
        raise EventLogError("events table is missing; run the schema migration first.")
    own_transaction = not db.in_transaction
    if own_transaction:
        db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM events LIMIT 1").fetchone() is not None:
            raise EventLogError("Genesis refused: the event chain already has events.")
        record = append(
            db, scope_id=GENESIS_SCOPE_ID, step_id=GENESIS_STEP_ID, kind=GENESIS_KIND,
            payload={"manifest": entries, "file_count": len(entries)}, recorded_at=recorded_at,
        )
        if own_transaction:
            db.execute("COMMIT")
    except BaseException:
        if own_transaction and db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return record


def verify_chain(db: sqlite3.Connection) -> ChainReport:
    """Recompute every hash in sequence order.  An empty chain is valid; a missing table is an error."""
    if not _table_present(db):
        raise EventLogError("events table is missing; nothing to verify.")
    rows = db.execute(f"SELECT {_COLUMNS} FROM events ORDER BY seq").fetchall()
    expected_seq = 1
    expected_prev = GENESIS_PREV_HASH
    first_bad: Optional[int] = None
    for row in rows:
        seq = int(row[0])
        try:
            payload = json.loads(row[6])
            recomputed = event_hash(
                prev_hash=str(row[7]), seq=seq, scope_id=str(row[1]), step_id=str(row[2]), kind=str(row[3]),
                schema_version=int(row[4]), recorded_at=str(row[5]), payload=payload,
            )
        except (ValueError, TypeError, EventLogError):
            recomputed = None
        if seq != expected_seq or str(row[7]) != expected_prev or recomputed != str(row[8]):
            first_bad = seq
            break
        expected_seq = seq + 1
        expected_prev = str(row[8])
    head = str(rows[-1][8]) if rows else None
    return ChainReport(ok=first_bad is None, count=len(rows), head=head, first_bad_seq=first_bad)


def head(db: sqlite3.Connection) -> Optional[EventRecord]:
    """The most recent event across every scope, or ``None`` on an empty chain."""
    if not _table_present(db):
        raise EventLogError("events table is missing.")
    row = db.execute(f"SELECT {_COLUMNS} FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    return _record(row) if row else None


def latest(db: sqlite3.Connection, scope_id: str) -> Optional[EventRecord]:
    """The most recent event for one scope, or ``None``."""
    _require_text(scope_id, "scope_id")
    if not _table_present(db):
        raise EventLogError("events table is missing.")
    row = db.execute(
        f"SELECT {_COLUMNS} FROM events WHERE scope_id=? ORDER BY seq DESC LIMIT 1", (scope_id,)
    ).fetchone()
    return _record(row) if row else None


def events_for(db: sqlite3.Connection, scope_id: str) -> Iterator[EventRecord]:
    """Every event for one scope in sequence order.  Rows are fetched eagerly so callers may write while iterating."""
    _require_text(scope_id, "scope_id")
    if not _table_present(db):
        raise EventLogError("events table is missing.")
    rows = db.execute(f"SELECT {_COLUMNS} FROM events WHERE scope_id=? ORDER BY seq", (scope_id,)).fetchall()
    return iter([_record(row) for row in rows])


def events_for_step(db: sqlite3.Connection, step_id: str, kind: Optional[str] = None) -> Iterator[EventRecord]:
    """Every event for one record key (optionally one kind) in sequence order."""
    _require_text(step_id, "step_id")
    if not _table_present(db):
        raise EventLogError("events table is missing.")
    if kind is None:
        rows = db.execute(f"SELECT {_COLUMNS} FROM events WHERE step_id=? ORDER BY seq", (step_id,)).fetchall()
    else:
        rows = db.execute(
            f"SELECT {_COLUMNS} FROM events WHERE step_id=? AND kind=? ORDER BY seq", (step_id, kind)
        ).fetchall()
    return iter([_record(row) for row in rows])
