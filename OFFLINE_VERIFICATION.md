# Discovery slice: offline review

Status: implementation complete; awaiting approval for a public-site run under
PLAN.md review gate 2. No public sites have been crawled.

Validation: `uv run pytest -q` — **25 passed** on October 12, 2026.
`uv run python -m hypertext_search --help` exposes start, resume, and report.
The test suite blocks DNS resolution and socket connections, except a mocked
DNS result for the public-address guard and a synthetic DNS timeout.

| Fixture | Observed result |
| --- | --- |
| Seed collection | All 141 seed scopes load successfully. |
| Recursive and gzipped sitemaps | Nested URL sets discovered; out-of-scope URLs excluded; last-modified evidence retained. |
| Atom and RSS | Candidate links and source dates retained; explicit next-feed links followed, including advertised external resources. |
| Archives and pagination | Explicit archive pages followed; ordinary posts and standalone pages remain candidates without body fetches. |
| Normalization and deduplication | Tracking/fragments removed, path boundary and traversal checks enforced, distinct provenance retained for shared candidates. |
| Robots and isolation | Disallowed routes never fetched; unavailable robots block that host while other seeds finish. Shared-host sitemaps apply to every scope. |
| Redirects | Observed www alias recorded after rules checks; path escape, unrelated probe redirect, and redirect loops blocked. Robots www redirect handled. |
| Pacing and concurrency | No overlapping requests per host, at most four global requests, three-second published delay and seven-second Retry-After honored. |
| Retries and limits | Three attempts at most for a persistent transient status; oversized bodies, compressed bombs, and XML declarations rejected. |
| Traps and loops | Calendar links, duplicate query keys, repeated pagination targets, and repeated bodies produce warnings and stop expansion. |
| Persistence | Reopened database resumes pending and abandoned leased jobs using its original snapshot; completed jobs are not fetched again. |
| Interrupted work and transactions | Cancellation leaves a recoverable lease; partial observations and new jobs roll back together. |
| Failure evidence | Parsing failures retain response bodies; non-HTML responses do not become fetched HTML candidates. |
| Reports | Per-seed request counts match attempts; candidate/provenance CSVs and warnings exported; assessment explicitly labels unverified URLs and inferred types. |

Implementation uses the existing standard-library transport and parsers rather
than the proposed httpx/BeautifulSoup dependencies. Public DNS address pinning,
bounded decompression, and disabled automatic redirects remain explicit.
pytest is the only added dependency, in the development group.

Before the real run, supply an operator email or contact URL for the user agent.
This review establishes fixture behavior, not live-site compatibility or
complete discovery. The collection-wide report will identify blocked routes,
ambiguous archives, redirects needing review, and adapter candidates.
