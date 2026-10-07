# Architecture

The showcase keeps reusable modeling and integrity mechanisms from an existing
first-party Python project. The synthetic demo composes these pieces without
importing the surrounding operational application.

## Boundaries

| File | Responsibility | I/O |
| --- | --- | --- |
| `models.py` | Frozen dataclasses for products, candles, positions, and computed values | None |
| `indicators.py` | Window calculations from caller-supplied candles | None |
| `graph/states.py` | Enum vocabulary, adjacency tables, edge checks, reachability | None |
| `graph/snapshot.py` | Snapshot schemas, exact-field decoding, canonical serialization, hashing | None |
| `graph/intents.py` | Inert intent schemas, round-tripping, content identity, UUID5 identifiers | None |
| `events.py` | Hash-chain creation, append, query, and verification | Caller-supplied SQLite connection |
| `demo.py` | Invented scenario and intentionally corrupted event | In-memory SQLite and standard output |

The package imports only the Python standard library and its own modules. The
`BTC` label in the example names a model type; its product identifiers, prices,
timestamps, and series are all invented. Account data is absent, positions and
open orders are empty, and the example product is marked non-tradable.

## State transitions

`assert_transition(current, next)` requires both enum values to belong to the
same declared machine and the destination to appear in the current state's
successor set. It returns the destination or raises `StateError`. It neither
updates persistent state nor enforces call-site discipline by itself.

The demo follows `SCHEDULED -> ATTEMPTED -> RUNNING -> COMPLETED` and rejects
`SCHEDULED -> COMPLETED`. Tests cover all eight graphs, terminal states,
reachability, and representative invalid paths. Historical phase labels in
retained source comments identify the original design's development stages;
they are not dependencies on additional documents or services.

## Reproducible data

Canonical JSON sorts mapping keys and uses compact separators. Decimal values
become strings; dataclasses, tuples, and sets have explicit representations.
Snapshot digests bind serialized content. Intent core and envelope digests
illustrate how stable identity can be separated from a changing price envelope.
UUID5 identifiers encode lineage and attempt number but confer no authority.

Snapshot reconstruction rejects missing/unknown fields and checks selected
types and enums. The retained serializers permit some non-finite Decimal text
and do not validate all semantic relationships. Nested dictionaries remain
mutable despite `frozen=True`. Callers must retain immutable copies or compare
digests when their trust model requires it.

## Event integrity and transactions

Each event binds the previous hash to canonical JSON containing the sequence,
scope, step, kind, schema version, timestamp, and payload. The log rejects
non-finite numeric payload values during append. Appends join an existing
transaction or acquire `BEGIN IMMEDIATE` and commit their own transaction.
The tests show both rollback participation and visibility of committed writes.

Triggers reject normal updates and deletes. Verification recomputes each hash,
checks predecessor links, and checks sequence continuity. Corruption of an
interior payload or deletion of an interior row is detectable. Tail deletion
and a coherent rewrite of all hashes can pass internal verification; compare
with a trusted head/count held outside that writer's control to detect them.
The showcase does not implement that external trust boundary.

The event tests use temporary files when a second SQLite connection is needed.
The demo uses only `:memory:` and has no argument for an existing database.
