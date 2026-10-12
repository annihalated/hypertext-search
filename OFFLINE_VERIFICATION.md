# Scrapy text collection: offline verification

October 12, 2026: `uv run pytest -q` — **22 passed**.
`uv run scrapy list` discovers the `discovery` spider.

The former custom-engine tests were replaced with tests for the new Scrapy
implementation. Integration tests run the real Scrapy engine with an in-memory
fixture download handler, while disabling real DNS/connection helpers. No public
sites were crawled by the agent.

Verified:

- All 141 seed scopes load; path boundaries and URL normalization are enforced.
- Defaults are 50 global requests, one per host, 0.1-second spacing, robots enabled.
- Shared-host requests get separate scope-aware fingerprints; callbacks survive
  native request serialization, and resume preserves its seed/settings snapshot.
- Sitemap indexes, gzipped URL sets, advertised robots sitemaps, Atom feeds,
  feed continuation, archives, and pagination discover candidates and source dates.
- Page bodies are collected for excerpts, old posts and standalone pages.
  Full RSS entries save text without a redundant post-page request.
- External feed links, sitemap indexes, robots sitemaps, and redirects never
  fetch unrelated sites. This includes a platform-wide Substack sitemap fixture.
- Both Substack subdomains and native robots requests use the same Scrapy slot.
- RSS content:encoded is saved as text; scripts and navigation are excluded.
  RSS excerpt observations are preserved after the fuller page text replaces
  them in the current documents table.
- Native robots middleware blocks forbidden archive requests; unavailable robots
  proceeds and is recorded; native retry succeeds after a synthetic HTTP 503.
- Redirect path escapes are stopped; observed www aliases are retained.
- Native duplicate filtering suppresses loops and retains candidate provenance.
- A real JOBDIR run pauses with pending disk-queued requests, resumes through the
  native scheduler, and visits all 80 archive routes without repeat downloads.
- Repeated document bodies, query traps, XML entity declarations, and compressed
  size limits produce warnings without expanding the affected routes.
- Private/literal network addresses are rejected.
- Local reports export candidates, provenance, documents, attempts, requests and warnings.

Scrapy INFO logging by default is used without a custom logging handler. Real
crawls are left to the user. Start a fresh `text-1` job after the scope/text update; the old
custom crawler database was removed at the user's explicit request.

These tests establish fixture behavior, not live-site coverage. The next review
uses the report produced by the user's collection-wide crawl.
