# Validation record

Validated on 2026-10-07. This record covers the standalone public subset only.

| Check | Result |
| --- | --- |
| Source suite, Python 3.14.7 | 64 tests passed, zero skips |
| Source suite, Python 3.10.17 | 64 tests passed, zero skips |
| Fresh wheel build | Passed with Python 3.11.13, setuptools 80.9.0, wheel 0.45.1; package indexes and dependency resolution disabled |
| Fresh virtual-environment installation | Passed on Python 3.14.7 with `--no-index --no-deps`; zero runtime dependencies |
| Installed-package suite | 64 tests passed, zero skips, outside the checkout with isolated Python and an audit hook rejecting socket operations |
| Installed demo and console command | Passed; six events verified, two ordinary mutations blocked, injected corruption detected at sequence 3 |
| Repeatability | Identical demo report across the tested Python 3.10 and 3.14 runs |
| Wheel contents | Package modules match reviewed source bytes; only package code and distribution metadata included |

## Publication review

The retained source and tests were reviewed file by file, then the complete
public file set was checked for credentials, contact details, local user paths,
IP addresses, account/order identifiers, private URLs, unexpected files, and
unreviewed imports. Invented fixtures use explicit synthetic labels or simple
arithmetic values. No runtime database or generated output is published.

The repository is initialized independently. No prior Git ancestry, releases,
archives, logs, CI artifacts, market recordings, or operational configuration
are included. The public package contains only the reviewed modules, one new
synthetic demo, tests, fresh documentation, and packaging metadata. No CI
workflow is included.

## Limits of these results

These checks do not validate the larger application or any live service.
No exchange connectivity, trading, profitability, hardware-backed protection,
concurrency stress, or production deployment was tested. Tests explicitly show
that internal hash verification cannot detect a coherently rewritten chain or
tail truncation without a separately trusted head/count. The provenance review
is bounded to the selected files and does not constitute legal clearance for
unrelated code.

The checks are local recorded results, not a hosted CI status or a security
certification. The source supports Python 3.10+ syntax; only the specific
versions above were exercised.
