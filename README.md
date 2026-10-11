# Hypertext Search

Discovery-only tooling for the 141 publication scopes in `data/sites.csv`.
Candidate URLs are not verified HTML pages, extracted documents, or searchable
results. There is no index or website in this slice.

## Local verification

```sh
uv sync
uv run pytest -q
uv run python -m hypertext_search --help
```

Tests use synthetic responses and clocks. Network resolution and connections
are disabled in the test suite.

## Discovery commands

The public run is awaiting PLAN.md review gate 2 approval. After approval, use
an actual operator contact in the user agent:

```sh
uv run python -m hypertext_search start --contact 'mailto:YOUR_EMAIL'
uv run python -m hypertext_search resume 1
uv run python -m hypertext_search report 1 --output data/discovery-report
```

`start` and `resume` access the network; `report` reads only SQLite. Use `--db`
before the subcommand to select another database. A resume retains the original
seed and configuration snapshot. Start a new run to change configuration.

The CLI locks the database against simultaneous commands. Ctrl-C preserves
completed observations and leaves interrupted leases recoverable by `resume`.
Blocked and failed jobs are retained for review; resume continues pending work,
without automatically repeating exhausted failures or bypassing robots rules.

## Discovery and safeguards

The runner fetches robots files, homepages, advertised feeds and sitemaps,
conventional endpoints, sitemap indexes, explicit archives, and pagination.
It collects candidate links without fetching ordinary post bodies. Archive
identification is heuristic; uncertain routes and possible client-rendered
navigation require report review. Repeated documents stop further expansion.

Publication hostname and path boundaries apply to candidates and HTML routes.
Observed www redirects may add aliases after robots checks; arbitrary HTML host
redirects require review. Advertised external feeds and sitemap resources can be
fetched without widening candidate scope. Probe redirects to unrelated hosts
are blocked. Fragment and recognized tracking parameters are removed; meaningful
queries, path case, and trailing slashes are preserved.

Defaults: four global jobs, no simultaneous requests to the same host, at least
one second between host request starts, longer robots crawl delays and
Retry-After delays honored. Requests have 10-second DNS/connect and 30-second
total deadlines, five redirects, two transient retries, and a 10 MiB wire and
decompressed response limit. DNS results are checked for public addresses and
pinned through the connection. XML entity declarations are rejected.

SQLite retains queue states, fetch attempts, response bodies and headers, hashes,
candidate provenance, aliases, and warnings. There is no small per-site request
cap. Calendar navigation, excessive query dimensions, high-cardinality query
values, repeated targets, and repeated response bodies produce warnings.
These heuristics cannot prove that every URL trap or archive is detectable.

The export includes `report.md`, `seeds.csv`, `candidates.csv`, `provenance.csv`,
`attempts.csv`, `jobs.csv`, and `warnings.json`. Candidate types and suspected
gaps are inference; source dates are unverified date evidence. Exhausting the
known queue does not prove complete historical coverage.
