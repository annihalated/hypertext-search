# Hypertext Search

Common commands (run `make` to list them):

```sh
make crawl SITE=nadia.xyz
make serve
make reset
make test
make deploy
```

`make reset` asks for confirmation; stop running crawls and the server first.
`make deploy` publishes the current local results while crawling and stats stay local.


A Scrapy project that collects public text across the 141 publication scopes
in `data/sites.csv`. It uses RSS first and fetches pages when needed. A local search workbench lets you crawl one publication at a time and inspect saved text.

## Install and verify

```sh
uv sync
uv run pytest -q
uv run scrapy list
```

## Run discovery

```sh
uv run scrapy crawl discovery
```

Crawls default to INFO logging: one status and URL line per downloaded response,
progress, actionable warnings, errors, and final statistics. Routine canonical,
script-presence, and non-HTML notices are kept in the audit database and DEBUG logs.
For detailed request and item logs, use `scrapy crawl discovery -s LOG_LEVEL=DEBUG`.
The user agent identifies the project with its GitHub repository URL.

Defaults: 50 concurrent requests globally, one at a time per host, and 0.1-second
spacing for ordinary hosts. All Substack subdomains share one Scrapy download
slot with one simultaneous request and one-second spacing; custom domains join
that slot after a response identifies itself as Substack. Explicit robots restrictions are obeyed. When robots retrieval fails,
Scrapy proceeds; the failure is logged and recorded. Published crawl delays and
Retry-After responses can slow requests down.

Press Ctrl-C once and wait for a clean shutdown. Resume with the same command.
Scrapy's JOBDIR stores its scheduler, duplicate filter, and spider state in
`data/crawls/text-1`. Use a separate directory for a fresh run:

```sh
uv run scrapy crawl discovery -s JOBDIR=data/crawls/text-2
```

Do not run two crawls in the same JOBDIR. Resume with the same settings and
Scrapy version. The seed snapshot is retained in the job directory. The former
custom crawler database was deleted at the user's request; old custom commands
and queue formats are not supported. The earlier Scrapy discovery job also
uses an incompatible format and contains unwanted external requests. The default
now points to a fresh `text-1` job; do not resume the old discovery queue.

For a different seed file, add `-a seeds=path/to/sites.csv` on a fresh run.
For Scrapy-native item exports, add `-o data/observations.jsonl`; this appends
records and remains usable across resume batches.

## Reports

After stopping or finishing a crawl:

```sh
uv run python -m hypertext_search report
```

For another run:

```sh
uv run python -m hypertext_search report --jobdir data/crawls/text-2
```

The report folder includes `report.md`, `seeds.csv`, `candidates.csv`,
`provenance.csv`, `documents.csv`, `attempts.csv`, `requests.csv`, and `warnings.json`.
`documents.csv` contains titles, text, dates, authors, content source, and an
inferred completeness label. SQLite keeps the best available text per URL and
the observation history retains earlier text versions.
The job directory also contains `results.sqlite3`, retained response bodies,
`discovery-config.json`, and Scrapy statistics. This database is an output
pipeline and audit trail, not a second crawler queue.

Candidate URLs remain unverified until fetched. Types, feed completeness, and
suspected coverage gaps are inference. `content:encoded` and Atom content are
treated as full text unless they contain excerpt/paywall markers; descriptions
and summaries are conservatively fetched for page text. Public paywall excerpts
are retained as partial text, without attempting to access protected content.
Dates are source-provided evidence, not verified publication dates. Shared-host
robots fetches are attributed to each matching scope, so per-seed request counts
can overlap. External resource robots requests remain in the full attempts
export. Exhausting known routes does not prove full historical coverage.

## Implementation

Scrapy owns networking, scheduling, disk queues, deduplication, retries,
redirects, compression, robots checks, logging, and feed exports. One spider
handles the collection, so concurrency settings apply to the whole crawl.
The spider uses selectors for HTML/feeds and Scrapy's Sitemap parser for indexes
and URL sets. It yields candidate and text items. Full RSS entries skip their
page fetches. Excerpts, historical posts, standalone pages, and listings collect
public page text. HTML text prefers article/main content and removes common
navigation, script, form, and footer text; unusual layouts may still need adapters.
Only the listed publication hosts, verified www aliases, and path scopes can be
fetched. External feed links, sitemap index entries, and redirects are rejected.
Scoped seeds may inspect conventional root robots/sitemap files, but their
candidates and other resource routes remain scope-filtered.

Project-specific components enforce publication path scopes and public network
addresses, distinguish shared sitemap callbacks by scope, apply robots crawl
delays, delay retries for Retry-After, and persist observations through a pipeline
and Scrapy signals. Requests have 30-second download and 10-second DNS timeouts,
five redirects, two retries, and a 10 MiB response limit. HTTP compression and
compressed sitemap expansion are bounded. XML entity declarations are rejected.

Calendar/query traps and repeated document bytes stop expansion with warnings.
Archive identification is heuristic and client-side navigation is not executed.
There is no small per-site request cap. Native JOBDIR resume requires a clean
shutdown; it does not promise recovery of every in-flight request after a crash.

See the official [Scrapy tutorial](https://docs.scrapy.org/en/latest/intro/tutorial.html),
[job persistence](https://docs.scrapy.org/en/latest/topics/jobs.html), and
[settings](https://docs.scrapy.org/en/latest/topics/settings.html).

## Local search and single-site crawls

```sh
uv run python -m hypertext_search serve
```

Open http://127.0.0.1:8000. Search supports words matched together and a site
filter. Excerpts preserve paragraphs, headings, lists, emphasis, links, and code
using sanitized HTML; original site styling is omitted. `/crawl` starts or resumes one selected publication and streams its log with automatic one-second updates;
`/stats` shows indexed document counts by site and text completeness. The search site dropdown includes only sites with indexed documents. Refresh
search to see new results. The first search builds a local full-text index from existing
crawl databases; indexing a large collection can take a moment.

Each selected publication uses `data/crawls/single-<site_id>`, isolated from the
old all-site crawl. Only one crawl can run through the interface at a time.
Stop gracefully before starting another; closing the server also stops its
crawl. Existing data remains intact. WordPress comment actions and nonce links
are rejected before scheduling, while ordinary pagination remains available.
Do not separately launch another crawler into the same job directory.

The equivalent command-line crawl is:

```sh
uv run python -m hypertext_search crawl https://100r.co
```

The server binds to loopback only. Use `--port 8001` for a different local port.

You can also use a hostname, such as `crawl 100r.co`. The URL must match a
publication in `data/sites.csv`; saved progress resumes automatically.

To clear all saved crawl results, queues, logs, and the search index:

```sh
uv run python -m hypertext_search reset
```

Stop the server and crawls first. Reset asks you to type `yes` before deleting
data and preserves `data/sites.csv`.

## Fly.io search deployment

The hosted app serves a read-only snapshot of local search. Crawling, logs, and
`/stats` remain local; hosted `/crawl`, `/stats`, and POST controls return 404.
No crawl databases, raw responses, or credentials are included in the image.

Update the hosted collection after crawling locally:

```sh
uv run python -m scripts.snapshot
fly deploy --ha=false
```

The snapshot is built with SQLite backup for a consistent database. Deployments
replace it with the current local results. The single 512 MB Fly Machine in Singapore stays running, with automatic
stopping disabled.
