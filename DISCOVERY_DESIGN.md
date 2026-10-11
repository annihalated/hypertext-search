# Discovery design — proposed for review

Status: awaiting approval under PLAN.md review gate 1. No crawler or public-site
run is implemented or authorized by this proposal.

## Scope and URL identity

- Use every row in `data/sites.csv` as a separate publication scope, preserving
  its IDs and references to both source collections.
- Accept HTTP(S) URLs on the seed hostname within its path scope. Match path
  boundaries: `/money-stuff` and its descendants qualify; `/money-stuffing`
  does not. Reject credentials, malformed URLs, and non-web schemes.
- Remove fragments, lowercase hostnames, remove default ports, resolve relative
  URLs, and remove recognized tracking parameters. Preserve meaningful query
  parameters, trailing slashes, and path case. Do not merge URLs using canonical
  tags alone; record canonical hints as evidence.
- Treat HTTP-to-HTTPS upgrades on the same host as aliases. Record other host
  redirects for review; do not silently widen the publication scope. A `www`
  alias requires observed redirects and compatible robots rules. Keep the path
  restriction across redirects.
- Permit external feed/sitemap fetches only when explicitly advertised by an
  allowed page, robots file, or sitemap. Such resources never widen candidate
  scope. Restrict all requests and redirects to public network addresses.

## Discovery strategy

1. Read robots rules, the scoped homepage, advertised feeds and sitemaps, and
   conventional feed/sitemap endpoints. Root robots and sitemap resources may
   sit outside a path scope, but their page candidates remain scope-filtered.
2. Recursively process sitemap indexes and URL sets, including compressed
   sitemaps. Parse RSS and Atom entries and explicit continuation links.
3. Collect in-scope HTML-looking links from fetched discovery documents. Persist
   standalone pages and listings as candidates along with posts and notes.
4. Enqueue explicit archive, category, tag, date-index, and pagination routes.
   Follow pagination to exhaustion. Record ambiguous routes and client-rendered
   navigation as gaps rather than fetching all candidate bodies.
5. Preserve every observation: seed, candidate URL, source document, discovery
   method, timestamp, link text, and any published date or sitemap last-modified
   value. Dates are evidence supplied by the source, not verified publication
   dates.

Candidate counts describe discovered URLs, not verified HTML pages or searchable
documents. Exhausted routes do not prove complete historical coverage.

## Safeguards

- Identifiable user agent naming Hypertext Search, with an operator contact
  configured before a real run. Honor robots rules for every fetched resource;
  record disallowed and unavailable robots outcomes. A robots retrieval failure
  pauses that host; a 404 means no published rules.
- Four global requests at a time; at least one second between request starts
  on each host, with no overlapping requests per host. Honor longer published
  crawl delays and Retry-After responses.
- Proposed limits: 10-second connection timeout, 30-second total request timeout,
  five redirects, two retries for transient failures with backoff, and 10 MiB
  maximum decompressed response size. Record every limit reached.
- Deduplicate scheduled URLs and detect repeated pagination targets, repeated
  document hashes, calendar expansion, and multiplying query combinations.
  Quarantine suspected traps with their evidence for review. No small per-site
  request cap; an operator interruption leaves durable unfinished work.
- Parse XML without external entities. Exclude obvious downloads by URL suffix;
  classify uncertain candidates as unverified. Isolate failures per seed.

## Dependencies and persistence

Propose `httpx` for bounded HTTP requests and `beautifulsoup4` for HTML navigation.
Use Python standard-library XML parsing, robots parsing, gzip, CSV, and SQLite;
disable XML entity declarations and cap decompressed input. Add `pytest` for
offline fixture verification only. Lock dependencies with uv when implemented.

Use `data/discovery.sqlite3` with tables for runs, seeds, resource observations,
queue jobs, fetch attempts, candidate URLs, provenance edges, and warnings.
Queue states are pending, leased, done, blocked, or failed. Commit observations
and derived jobs atomically; expire abandoned leases on resume. Retain retries,
redirect chains, response hashes, robots decisions, and discovery documents so
results remain inspectable. A resume uses the original seed/config snapshot;
configuration changes require an explicit new run.

## Outputs and verification gates

Export a Markdown assessment plus per-seed CSV, candidate CSV, provenance CSV,
and structured warnings. Include every seed's outcome and resolved address,
routes, candidate and request counts, failures, unfinished queues, alias/trap
evidence, date ranges with sources, and representative URLs. Label page types
and suspected coverage gaps as inference.

Before any public-site run, fixtures must exercise recursive/gzipped sitemaps,
feeds, archive pagination, path and redirect boundaries, URL deduplication,
robots decisions, retry limits, oversized responses, loop/trap detection, site
failure isolation, and interruption/resume. Tests use fake HTTP responses and a
fake clock, never public sites. Present results for review gate 2 and obtain
approval for the collection-wide run. Stop for assessment after that run.
