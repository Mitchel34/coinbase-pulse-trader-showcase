# Attribution and provenance

Copyright (c) 2026 Mitchel34.

Selected first-party modules and synthetic unit tests were extracted from
Mitchel34's Coinbase Pulse Trader project. The publication uses a fresh Git
history. Module descriptions, historical example identifiers, package metadata,
and project documentation were adapted for this standalone showcase. The demo
and additional boundary, serialization, and integrity-limit tests were created
specifically for it, with assistance from OpenAI Codex.

The reviewed source subset comprises `models.py`, `indicators.py`, `events.py`,
the package initializer, `graph/{__init__,states,snapshot,intents}.py`, and
`tests/test_{states,events_chain,indicators}.py`. No third-party copyright or
license notices were found in those files; no existing notices were removed.
Source history for the subset identified one first-party author identity.
This bounded provenance review is not a comprehensive legal audit.

The runtime uses Python's standard library, including `sqlite3`. No interpreter,
SQLite implementation, third-party source, market dataset, SDK, or dependency
binary is vendored. Setuptools and wheel are build tools supplied by the build
environment, not repository content. Their respective upstream rights remain
with their owners.

Coinbase is named to identify the original project's subject. No endorsement
or affiliation is claimed. No open-source license has been selected or added.
