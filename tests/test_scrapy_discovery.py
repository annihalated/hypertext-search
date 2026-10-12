import base64
import csv
import gzip
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse
from scrapy.settings import Settings
from scrapy.utils.request import request_from_dict

from hypertext_search.middlewares import ScopeFingerprinter, public_address
from hypertext_search.report import export
from hypertext_search.spiders.discovery import DiscoverySpider
from hypertext_search.urls import Seed, normalize, read_seeds


ROOT = Path(__file__).resolve().parents[1]


def settings(jobdir):
    value = Settings()
    value.setmodule("hypertext_search.settings")
    value.set("JOBDIR", str(jobdir), priority="cmdline")
    return value


def seed_csv(path, hosts=("example.test",), scopes=None):
    fields = list(Seed.__dataclass_fields__)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for i, host in enumerate(hosts):
            scope = scopes[i] if scopes else "/"
            writer.writerow(dict(site_id=str(i), name=host, homepage_url=f"https://{host}{scope}",
                                 hostname=host, scope_path=scope, sources="fixture", source_ids=f"fixture:{i}"))


def crawl(tmp_path, fixtures, hosts=("example.test",), scopes=None, extra=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    seeds = tmp_path / "seeds.csv"
    seed_csv(seeds, hosts, scopes)
    fixtures_path = tmp_path / "fixtures.json"
    fixtures_path.write_text(json.dumps(fixtures))
    jobdir = tmp_path / "job"
    command = [sys.executable, "-m", "scrapy", "crawl", "discovery", "-a", f"seeds={seeds}",
               "-s", f"JOBDIR={jobdir}", "-s", f"FIXTURE_PATH={fixtures_path}",
               "-s", f"FIXTURE_CALLS={tmp_path / 'calls.txt'}", "-s", "LOG_LEVEL=ERROR",
               "-s", 'DOWNLOAD_HANDLERS={"http":"tests.scrapy_fixtures.FixtureDownloadHandler","https":"tests.scrapy_fixtures.FixtureDownloadHandler"}',
               *extra]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr
    assert "[scrapy.utils.signal] ERROR" not in result.stderr, result.stderr
    assert "[scrapy.core.scraper] ERROR" not in result.stderr, result.stderr
    with sqlite3.connect(jobdir / "results.sqlite3") as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT data FROM observations")]
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    return jobdir, events, calls


def page(body, typ="text/html", status=200, **headers):
    return dict(body=body, status=status, headers={"Content-Type": typ, **headers})


@pytest.mark.parametrize("url,expected", [
    ("HTTPS://EXAMPLE.test:443/a?utm_source=x&q=y#z", "https://example.test/a?q=y"),
    ("https://example.test/money/../outside", "https://example.test/outside"),
    ("https://example.test/%2e%2e/outside", None),
    ("https://user:password@example.test/", None),
    ("javascript:alert(1)", None),
])
def test_normalize(url, expected):
    assert normalize(url) == expected


def test_collection_and_scope():
    assert len(read_seeds(ROOT / "data/sites.csv")) == 141
    seed = Seed("one", "One", "https://example.test/money", "example.test", "/money", "", "")
    assert seed.allows("https://example.test/money/post")
    assert not seed.allows("https://example.test/money-stuff")
    assert not seed.allows("https://example.test/money/../outside")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "::1", "224.0.0.1"])
def test_private_address_guard(address):
    with pytest.raises(Exception, match="Non-public"):
        public_address(address)


def test_settings_and_scope_fingerprint(tmp_path):
    config = settings(tmp_path)
    assert config.getint("CONCURRENT_REQUESTS") == 50
    assert config.getint("CONCURRENT_REQUESTS_PER_DOMAIN") == 1
    assert config.getfloat("DOWNLOAD_DELAY") == .1
    assert config.getbool("ROBOTSTXT_OBEY")
    assert config["LOG_LEVEL"] == "INFO"
    fingerprinter = ScopeFingerprinter()
    a = Request("https://example.test/sitemap.xml", meta={"site": "a", "kind": "sitemap"})
    b = a.replace(meta={"site": "b", "kind": "sitemap"})
    assert fingerprinter.fingerprint(a) != fingerprinter.fingerprint(b)
    assert fingerprinter.fingerprint(a) == fingerprinter.fingerprint(a.copy())


def test_requests_serializable_and_snapshot(tmp_path):
    seeds = tmp_path / "seeds.csv"
    seed_csv(seeds)
    crawler = Crawler(DiscoverySpider, settings(tmp_path / "job"))
    spider = DiscoverySpider.from_crawler(crawler, seeds=str(seeds))
    request = spider.request("https://example.test/archive", "0")
    recovered = request_from_dict(request.to_dict(spider=spider), spider=spider)
    assert recovered.callback == spider.parse
    seeds.unlink()
    resumed = DiscoverySpider.from_crawler(Crawler(DiscoverySpider, settings(tmp_path / "job")), seeds=str(seeds))
    assert resumed.seeds == spider.seeds
    config = settings(tmp_path / "job")
    config.set("DOWNLOAD_DELAY", .2, priority="cmdline")
    with pytest.raises(ValueError, match="original settings"):
        DiscoverySpider.from_crawler(Crawler(DiscoverySpider, config))


def test_real_engine_discovery_and_resume(tmp_path):
    fixtures = {
        "https://example.test/": page('<a href="/archive">Archive</a><a href="/about">About</a><link rel="alternate" type="application/atom+xml" href="/feed"/>'),
        "https://example.test/robots.txt": page("User-agent: *\nDisallow: /private\nSitemap: https://example.test/advertised.xml", "text/plain"),
        "https://example.test/advertised.xml": page('<sitemapindex><sitemap><loc>/nested.xml.gz</loc></sitemap></sitemapindex>', "application/xml"),
        "https://example.test/nested.xml.gz": dict(base64=base64.b64encode(gzip.compress(b'<urlset><url><loc>https://example.test/historic</loc><lastmod>2001-01-01</lastmod></url><url><loc>https://outside.test/post</loc></url></urlset>')).decode(), headers={"Content-Type": "application/gzip"}),
        "https://example.test/archive": page('<a href="/archive?page=2" rel="next">Next</a><a href="/private/archive">Archive</a><a href="/post">Post</a><a href="/calendar/2027">Next month</a>'),
        "https://example.test/archive?page=2": page('<a href="/archive" rel="next">Next</a><a href="/historic">Historic</a>'),
        "https://example.test/feed": page('<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="https://example.test/new"/><content type="html">Complete new post</content><published>2026-01-01</published></entry><link rel="next" href="/feed-next"/></feed>', "application/atom+xml"),
        "https://example.test/feed-next": page('<feed><entry><link href="https://example.test/historic"/></entry></feed>', "application/xml"),
    }
    jobdir, events, calls = crawl(tmp_path, fixtures)
    urls = {e["url"] for e in events if e["event"] == "candidate"}
    assert {"https://example.test/about", "https://example.test/historic", "https://example.test/new"} <= urls
    assert "https://outside.test/post" not in urls
    assert "https://example.test/private/archive" not in calls
    assert "https://example.test/post" in calls
    assert "https://example.test/new" not in calls
    assert "https://example.test/calendar/2027" not in calls
    assert any(e.get("code") == "duplicate_request" for e in events)
    assert any("robots.txt" in e.get("error", "") for e in events if e["event"] == "failure")
    assert json.loads((jobdir / "stats.json").read_text()).get("scheduler/unserializable", 0) == 0
    assert len([e for e in events if e["event"] == "candidate" and e["url"].endswith("historic")]) >= 2
    before = len(calls)
    _, _, resumed_calls = crawl(tmp_path, fixtures)
    assert len(resumed_calls) == before
    export(jobdir, tmp_path / "report")
    assert "unverified" in (tmp_path / "report/report.md").read_text()


def test_robots_failure_optout_and_native_retry(tmp_path):
    _, events, calls = crawl(tmp_path, {
        "https://example.test/robots.txt": {"error": "Synthetic DNS failure"},
        "https://example.test/": [page("", status=503), page('<a href="/about">About</a>')],
    })
    assert any(e["event"] == "candidate" and e["url"].endswith("about") for e in events)
    assert calls.count("https://example.test/") == 2
    assert any(e.get("code") == "robots_unavailable" for e in events)


def test_redirect_scope_and_shared_sitemaps(tmp_path):
    _, events, calls = crawl(tmp_path, {
        "https://example.test/money": page("", status=301, Location="https://www.example.test/money"),
        "https://www.example.test/money": page('<a href="/money/archive">Archive</a><a href="/outside">Outside</a>'),
        "https://www.example.test/money/archive": page("", status=302, Location="/outside"),
        "https://example.test/sitemap.xml": page('<urlset><url><loc>https://example.test/money/post</loc></url><url><loc>https://example.test/notes/post</loc></url></urlset>', "application/xml"),
    }, hosts=("example.test", "example.test"), scopes=("/money", "/notes"))
    assert "https://www.example.test/outside" not in calls
    candidates = {(e["site"], e["url"]) for e in events if e["event"] == "candidate"}
    assert ("0", "https://example.test/money/post") in candidates
    assert ("1", "https://example.test/notes/post") in candidates
    assert any(e["event"] == "alias" for e in events)


def test_native_jobdir_pause_resume(tmp_path):
    fixtures = {"https://example.test/": page("".join(f'<a href="/archive/{i}">Archive {i}</a>' for i in range(80)))}
    fixtures.update({f"https://example.test/archive/{i}": page(f'<a href="/post-{i}">Post {i}</a>') for i in range(80)})
    config = ("-s", "CONCURRENT_REQUESTS=2", "-s", "DOWNLOAD_DELAY=0")
    jobdir, _, first_calls = crawl(tmp_path, fixtures, extra=(*config, "-s", "CLOSESPIDER_PAGECOUNT=10"))
    assert json.loads((jobdir / "finish.json").read_text())["reason"] == "closespider_pagecount"
    with sqlite3.connect(jobdir / "results.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM requests WHERE state='pending'").fetchone()[0] > 0
    _, events, all_calls = crawl(tmp_path, fixtures, extra=config)
    assert json.loads((jobdir / "finish.json").read_text())["reason"] == "finished"
    archives = [url for url in all_calls if "/archive/" in url]
    assert len(set(archives)) == 80 and len(archives) == 80
    assert len(all_calls) > len(first_calls)
    assert len({e["url"] for e in events if e["event"] == "candidate" and "/post-" in e["url"]}) == 80


def test_repeated_documents_entities_and_compressed_limit(tmp_path):
    _, events, calls = crawl(tmp_path, {
        "https://example.test/": page('<a href="/archive">Archive</a><a href="/archive?a=1&a=2">Archive</a>'),
        "https://example.test/archive": page('<a href="/archive?page=2">Next</a>'),
        "https://example.test/archive?page=2": page('<a href="/archive?page=2">Next</a>'),
        "https://example.test/sitemap.xml": page('<!DOCTYPE urlset [<!ENTITY x "bad">]><urlset/>', "application/xml"),
        "https://example.test/sitemap_index.xml": dict(base64=base64.b64encode(gzip.compress(b"x" * 1025)).decode(), headers={"Content-Type": "application/gzip"}),
    }, extra=("-s", "DOWNLOAD_MAXSIZE=1024"))
    codes = {e.get("code") for e in events}
    assert {"repeated_document", "query_trap", "parse_failure"} <= codes
    assert "https://example.test/archive?a=1&a=2" not in calls


def test_publication_only_sitemaps_feeds_and_redirects(tmp_path):
    fixtures = {
        "https://example.test/feed": page('<feed><link rel="next" href="https://unlisted.test/feed"/><entry><link href="https://unlisted.test/post"/><content>Foreign text</content></entry></feed>', "application/xml"),
        "https://example.test/": page('<a href="https://unlisted.test/post">External</a><link rel="alternate" type="application/rss+xml" href="https://unlisted.test/feed"/>'),
        "https://example.test/robots.txt": page("Sitemap: https://unlisted.test/sitemap.xml", "text/plain"),
        "https://example.test/sitemap.xml": page('<sitemapindex><sitemap><loc>https://substack.com/sitemap-sitemap.xml</loc></sitemap><sitemap><loc>https://unlisted.test/sitemap.xml</loc></sitemap><sitemap><loc>https://example.test/own.xml</loc></sitemap></sitemapindex>', "application/xml"),
        "https://example.test/own.xml": page('<urlset><url><loc>https://example.test/old</loc></url><url><loc>https://unlisted.test/post</loc></url><url><loc>https://example.test/redirect</loc></url></urlset>', "application/xml"),
        "https://example.test/old": page('<article><h1>Old</h1><p>Historical body text.</p></article>'),
        "https://example.test/redirect": page("", status=302, Location="https://unlisted.test/post"),
        "https://example.test/feed/": page("", status=302, Location="https://unlisted.test/feed"),
    }
    _, events, calls = crawl(tmp_path, fixtures)
    assert not any("unlisted.test" in u or "substack.com" in u for u in calls)
    assert any(e.get("code") == "outside_scope" for e in events)
    assert any(e["event"] == "document" and "Historical body text" in e["body"] for e in events)


def test_rss_text_preferred_with_excerpt_and_standalone_fallback(tmp_path):
    feed = '''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
    <item><title>Full post</title><link>https://example.test/full</link><content:encoded><![CDATA[<p>Complete <b>RSS</b> article.</p><script>secretScript()</script>]]></content:encoded><pubDate>2026-01-01</pubDate></item>
    <item><title>Excerpt</title><link>https://example.test/excerpt</link><description><![CDATA[<p>Short excerpt. Continue reading</p>]]></description></item>
    </channel></rss>'''
    jobdir, events, calls = crawl(tmp_path, {
        "https://example.test/feed": page(feed, "application/rss+xml"),
        "https://example.test/": page('<a href="/full">Full</a><a href="/excerpt">Excerpt</a><a href="/about">About</a>'),
        "https://example.test/sitemap.xml": page('<urlset><url><loc>https://example.test/full</loc></url></urlset>', "application/xml"),
        "https://example.test/excerpt": page('<nav>Navigation junk</nav><article><h1>Full excerpt article</h1><p>Complete page body.</p><script>ignore()</script></article><footer>Footer junk</footer>'),
        "https://example.test/about": page('<main><h1>About</h1><p>Standalone biography.</p></main>'),
    })
    assert "https://example.test/full" not in calls
    assert "https://example.test/excerpt" in calls and "https://example.test/about" in calls
    with sqlite3.connect(jobdir / "results.sqlite3") as db:
        documents = {r[0]: json.loads(r[1]) for r in db.execute("SELECT url,data FROM documents")}
    full = documents["https://example.test/full"]
    assert full["body"] == "Complete RSS article." and full["source_kind"] == "feed"
    excerpt = documents["https://example.test/excerpt"]
    assert excerpt["source_kind"] == "page" and "Complete page body." in excerpt["body"]
    assert "Navigation junk" not in excerpt["body"] and "Footer junk" not in excerpt["body"]
    assert "Standalone biography." in documents["https://example.test/about"]["body"]
    # Keep the initial RSS excerpt as an observation even after the page wins.
    assert any(e["event"] == "document" and e["source_kind"] == "feed" and e["url"].endswith("excerpt") for e in events)
    export(jobdir, tmp_path / "report")
    assert (tmp_path / "report/documents.csv").exists()


def test_shared_native_substack_slot_including_robots(tmp_path):
    hosts = ("one.substack.com", "two.substack.com")
    fixtures = {f"https://{h}/feed": page('<rss><channel/></rss>', "application/rss+xml") for h in hosts}
    fixtures.update({f"https://{h}/sitemap.xml": page('<sitemapindex><sitemap><loc>https://substack.com/global.xml</loc></sitemap></sitemapindex>', "application/xml") for h in hosts})
    jobdir, events, calls = crawl(tmp_path, fixtures, hosts=hosts,
        extra=("-s", 'DOWNLOAD_SLOTS={"substack":{"concurrency":1,"delay":0.01,"randomize_delay":false}}'))
    assert not any(u.startswith("https://substack.com/") for u in calls)
    fetches = [e for e in events if e["event"] == "fetch"]
    assert all(e["download_slot"] == "substack" for e in fetches)
    assert any(e["kind"] == "robots" for e in fetches)


def test_scoped_publication_resource_boundaries():
    seed = Seed("a", "A", "https://example.test/money", "example.test", "/money", "", "")
    assert seed.allows_resource("https://example.test/sitemap.xml", "sitemap")
    assert seed.allows_resource("https://example.test/robots.txt", "robots")
    assert not seed.allows_resource("https://example.test/outside/feed", "feed")
    assert not seed.allows_resource("https://example.test/other.xml", "sitemap")
    assert not seed.allows_resource("https://other.test/money/sitemap.xml", "sitemap")


def test_native_robots_redirect_stays_on_host(tmp_path):
    _, events, calls = crawl(tmp_path, {
        "https://example.test/robots.txt": page("", status=302, Location="https://unlisted.test/robots.txt"),
        "https://example.test/feed": page('<rss><channel><item><link>https://example.test/post</link><description>Summary</description></item></channel></rss>', "application/xml"),
        "https://example.test/post": page('<article><p>Public fallback body.</p></article>'),
    })
    assert not any("unlisted.test" in u for u in calls)
    assert any(e["event"] == "document" and "Public fallback body" in e["body"] for e in events)
