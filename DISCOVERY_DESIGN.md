# Discovery design — Scrapy migration

The user approved replacing the custom crawler with Scrapy's recommended project
structure. Follow-up choices: 50 concurrent requests globally, 0.1-second spacing
per host, robots as opt-out, repository URL as contact, Scrapy INFO
logging, and a fresh run after deleting the old custom database. Subsequent approval
expanded this slice to public text collection, RSS first, with strict site
boundaries and shared Substack throttling. The user runs real
crawls; implementation and verification do not launch public-site crawls.

## Scrapy responsibilities

- Standard `scrapy.cfg`, settings, spiders, middleware, extension, and pipeline.
- One spider for the collection, so global concurrency is actually global.
- Native HTTP downloader, TLS verification, robots middleware and Protego parser.
- Native scheduler, downloader-aware priority queue, duplicate filter, retry and
  redirect middleware, compression limits, and INFO logging.
- JOBDIR owns disk queues and spider state. Native SQLite-backed queue classes
  improve queue durability. Clean Ctrl-C shutdown and the same command resume
  the job. Each new run needs a distinct JOBDIR.
- Native feed exporters are available with `-o` or `-O`.

## Project responsibilities

Read all 141 seed scopes from CSV. Snapshot seeds and effective crawl settings
for a run; refuse a resume with changed settings. Each callback carries its seed
and discovery kind using serializable request metadata/callback arguments.

Normalize HTTP(S) URLs, remove fragments and tracking parameters, and enforce
hostname plus path boundaries. Record observed www aliases without allowing an
unrelated HTML host redirect or path escape. External feeds, sitemap index entries, and redirects are rejected before
fetching. Conventional root robots and sitemap endpoints are permitted for path-
scoped publications; other resource routes obey the same publication path bounds. Reject credentials and non-public addresses through downloader middleware
and Scrapy's DNS resolver extension point.

Discover homepages, advertised and conventional feeds/sitemaps, recursive sitemap
indexes, archives, pagination, and standalone links. Use Scrapy selectors and
its Sitemap parser. Feed content is saved directly when available. Full-content fields skip the
post page; excerpts and uncertain descriptions fall back to public page text.
Traverse in-scope HTML links for historical and standalone pages. Preserve
candidate provenance, source dates, and text observations, including earlier
versions. Text completeness is inferred, not verified; paywall markers retain
partial public content. Detect calendar/query expansion,
duplicate scheduled targets, and repeated document hashes; record uncertain
navigation and adapter needs as gaps.

ROBOTSTXT_OBEY stays enabled. Explicit restrictions are obeyed; inaccessible
robots files no longer block the host. Scrapy's native robots behavior controls
that fallback. Apply published crawl delays to native downloader slots and
Retry-After pauses before native retries. Keep one simultaneous request per host. All Substack subdomains (including
native robots requests) share the native `substack` download slot, with one
simultaneous request and a one-second delay. Custom domains join after headers
identify them as Substack. Other hosts retain 0.1-second spacing and 50 global
requests. Full RSS text and page text are stored through the item pipeline.
The fresh job defaults to `data/crawls/text-1`; format-version checks reject old
discovery queues.

## Outputs

An item pipeline and signal extension write observations, request outcomes,
response hashes and bodies, and failures to the job directory. SQLite holds
results only; there is no parallel custom queue. Reports are a local-only command
and export every seed's counts, outcomes, routes, samples, date evidence,
provenance, unfinished observed requests, and warnings.

Candidate counts are not verified searchable pages. Types and suspected gaps are
inference. Queue exhaustion is not proof of complete historical coverage.

## Verification

Offline tests use a synthetic download handler with Scrapy's real engine,
robots/retry/redirect middleware, scheduler, pipeline, and state persistence.
They do not contact public sites. See OFFLINE_VERIFICATION.md for results.

Official references: [tutorial](https://docs.scrapy.org/en/latest/intro/tutorial.html),
[JOBDIR](https://docs.scrapy.org/en/latest/topics/jobs.html),
[robots middleware](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#robotstxtmiddleware),
[item pipelines](https://docs.scrapy.org/en/latest/topics/item-pipeline.html),
[extensions](https://docs.scrapy.org/en/latest/topics/extensions.html), and
[request fingerprints](https://docs.scrapy.org/en/latest/topics/request-response.html#request-fingerprints).
