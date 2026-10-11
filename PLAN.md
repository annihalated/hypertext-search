# Hypertext Search: handoff and first implementation slice

## Product direction

Build a public, Google-like full-text search engine over the sites in Hypertext
and Map. Search every discoverable public HTML page: posts, notes, standalone
pages, archives, and category listings. Prefer content pages over listings in
ranking. The eventual deployment runs crawling and search on one persistent
server, with new-content discovery several times daily.

Preserve changed page versions internally, but do not build a public history
viewer in the initial release. Keep confirmed unavailable pages searchable with
an availability badge and their original URL.

No roundup classifier or post-type filter. Recommendation stars and MCP are
deferred entirely. Semantic search is undecided; keyword FTS is the baseline.
The broader implementation specification was removed at the user's request;
this file records the current agreed direction rather than restoring that spec.

## Working agreement

The user wants to decide design and implementation one small slice at a time.
Present concrete design choices before implementing a slice, then present its
results for review. Wait for approval at each review gate before proceeding.
Do not automatically implement the whole application.

The user requested implementation, then explicitly narrowed the immediate work
to cleaning the folder, initializing uv and Git, and creating a public GitHub
repository named `hypertext-search`. Finish that setup first. The next discussion
is the discovery design described below.

## Current state and setup handoff

- Workspace: `/Users/nihalsahu/workbench/hypertext-search`.
- Preserved `data/sites.csv`, `data/hypertext_blogs.csv`, and `data/map_blogs.csv`.
- Verified 141 unique site scopes, 167 source rows, and 26 overlaps. Every source
  reference is accounted for; IDs are unique and URLs are syntactically valid.
- Bloomberg has the path scope `/money-stuff`; do not crawl the whole host.
- Removed the old README, CODEX_PROMPT.md, and .DS_Store as requested.
- Initialized a minimal uv project named `hypertext-search`, requiring Python
  3.12 or newer, with no dependencies yet. `uv sync` succeeded using Python 3.14.7.
- Added `pyproject.toml`, `uv.lock`, `.venv`, and `.gitignore`. The ignore rules
  preserve seed CSVs while excluding other data-directory contents, caches,
  virtual environments, and environment secrets.
- An existing, untracked `hypertext_search/discovery.py` was found during setup
  on October 12, 2026. It has not been reviewed or verified and is preserved
  locally outside the setup commit. No public-site discovery run was performed.
- `DISCOVERY_DESIGN.md` records a proposal awaiting review gate 1 approval.
- Git is initialized on `main`. The initial setup commit is `cd10ac7`.
- Public repository: https://github.com/annihalated/hypertext-search.
  `origin` is connected and `main` has been pushed successfully.

### Setup completed

Setup was completed after inspecting the workspace and authenticated GitHub
account. The seed CSVs were preserved byte-for-byte. Their existing CRLF line
endings produce Git whitespace-check notices; they were not rewritten.
Next: review and approve the discovery design before implementing the slice.

## First slice: discover URLs across all 141 seeds

Run the first discovery experiment on the user's Mac, against the whole seed
collection rather than a small sample. It is discovery-only: fetch feeds,
sitemaps, homepages, archives, and listing pages needed to discover URLs, but do
not extract candidate page bodies, build an FTS index, or implement the website.

Follow feeds, sitemap indexes, archive links, and pagination until known archive
routes are exhausted. Collect candidate public HTML URLs, including standalone
pages and listings, with source provenance and discovery route.

There is no small per-site request cap. Honor robots rules, use an identifiable
user agent, pace requests at one per host per second, and default to four
concurrent requests globally. Bound individual request timeouts, retries,
redirects, and response sizes. Detect loops and expanding URL traps. Respect
publication scopes and report blocked or unfinished work explicitly.

Persist queues and progress so interrupted runs resume. Isolate site failures.
Exhausting known discovery routes does not establish complete historical
coverage, and candidate counts are not counts of verified searchable pages.

## Review gates

### 1. Discovery design — before implementation

Present URL-scope rules, discovery strategy, crawl safeguards, proposed
dependencies, persistence approach, and report format. Resolve the user's choices
and get approval before implementing this slice.

### 2. Offline verification — before the real run

Review synthetic fixture results for sitemap recursion, archive pagination,
scope enforcement, deduplication, robots handling, loop detection, and resumable
queues. Approve the collection-wide run after these checks pass. Tests must not
crawl public sites.

### 3. Collection-wide discovery assessment

Present a report containing:

- Every seed's outcome, resolved address, and discovery routes used.
- Candidate counts, requests made, failures, and unfinished work.
- Candidate URL samples and apparent page types, labeled as inference.
- Evidence of historical depth when feeds or URLs provide dates.
- Suspected gaps, aliases, traps, and sources needing adapters.

Preserve candidate-level provenance so counts can be inspected. Distinguish
observed discovery evidence from inferred completeness. Review boundary errors,
duplicates, and missing archives with the user.

### 4. Decide the next slice

After assessing the report, choose whether to improve discovery, investigate
specific sites, or begin extraction. Do not automatically proceed to indexing,
UI implementation, deployment, or any later subsystem.
