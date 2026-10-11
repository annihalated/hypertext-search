import asyncio
import csv
import gzip
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from hypertext_search.discovery import Config, Engine, Response, Seed, Store, Transport, export, normalize, read_seeds


class FakeClock:
    def __init__(self):
        self.time = 0

    def monotonic(self):
        return self.time

    async def sleep(self, seconds):
        self.time += seconds


class FakeTransport:
    def __init__(self, responses, clock):
        self.responses = responses
        self.calls = []
        self.clock = clock

    def request(self, url, config):
        self.calls.append((url, self.clock.monotonic()))
        result = self.responses.get(url, Response(404, {}, b""))
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(body, content_type="text/html", status=200, **headers):
    return Response(status, {"content-type": content_type, **headers}, body.encode() if isinstance(body, str) else body)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Offline tests must not access the network")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def engine(tmp_path, responses, seeds=None, **config):
    seeds = seeds or [Seed("one", "One", "https://example.test/", "example.test")]
    store = Store(tmp_path / "discovery.sqlite3")
    run = store.start(seeds, Config("test@example.test", **config))
    clock = FakeClock()
    transport = FakeTransport(responses, clock)
    return Engine(store, run, transport, clock)


def rows(e, table):
    return list(e.store.db.execute(f"SELECT * FROM {table} WHERE run=?", (e.run,)))


def urls(e):
    return {r["url"] for r in rows(e, "candidates")}


def test_seed_collection():
    assert len(read_seeds(Path("data/sites.csv"))) == 141


@pytest.mark.parametrize("url,expected", [
    ("HTTPS://Example.test:443/a?utm_source=x&q=yes#fragment", "https://example.test/a?q=yes"),
    ("https://user:password@example.test/a", None),
    ("https://example.test/%2e%2e/escape", None),
    ("javascript:alert(1)", None),
    ("https://example.test/money-stuff/../outside", "https://example.test/outside"),
])
def test_normalization(url, expected):
    assert normalize(url) == expected


def test_path_boundaries():
    seed = Seed("one", "One", "https://example.test/money-stuff", "example.test", "/money-stuff")
    assert seed.allows("https://example.test/money-stuff/post")
    assert not seed.allows("https://example.test/money-stuffing")
    assert not seed.allows("https://example.test/money-stuff/../outside")
    assert not seed.allows("ftp://example.test/money-stuff")


def test_recursive_gzip_sitemap_feed_and_pagination(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/": response('<a href="/archive">Archive</a><a href="/about">About</a><link rel="alternate" type="application/atom+xml" href="https://feeds.test/feed"/>'),
        "https://example.test/sitemap.xml": response('<sitemapindex><sitemap><loc>/nested.xml.gz</loc></sitemap></sitemapindex>', "application/xml"),
        "https://example.test/nested.xml.gz": response(gzip.compress(b'<urlset><url><loc>https://example.test/old-post</loc><lastmod>2001-01-01</lastmod></url><url><loc>https://outside.test/post</loc></url></urlset>'), "application/xml"),
        "https://feeds.test/feed": response('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New</title><link href="https://example.test/new-post"/><published>2026-01-01</published></entry><link rel="next" href="/next"/></feed>', "application/atom+xml"),
        "https://feeds.test/next": response('<feed><entry><link href="https://example.test/old-post"/></entry></feed>', "application/xml"),
        "https://example.test/archive": response('<a href="/archive?page=2" rel="next">Next</a><a href="/post-one">One</a>'),
        "https://example.test/archive?page=2": response('<a href="/archive" rel="next">Next</a><a href="/post-two">Two</a>'),
    })
    asyncio.run(e.run_queue())
    assert {"https://example.test/old-post", "https://example.test/new-post", "https://example.test/about", "https://example.test/post-two"} <= urls(e)
    assert "https://outside.test/post" not in urls(e)
    assert not any(u in {"https://example.test/about", "https://example.test/post-one", "https://example.test/old-post"} for u, _ in e.transport.calls)
    assert any(r["code"] == "pagination_loop" for r in rows(e, "warnings"))
    assert len([r for r in rows(e, "provenance") if r["url"].endswith("old-post")]) == 2
    assert not any(r["state"] in {"pending", "leased"} for r in rows(e, "jobs"))
    export(e.store, e.run, tmp_path / "report")
    assert "unverified" in (tmp_path / "report/report.md").read_text()
    assert json.loads((tmp_path / "report/warnings.json").read_text())
    with (tmp_path / "report/seeds.csv").open() as f:
        summary = next(csv.DictReader(f))
    assert summary["site_id"] == "one" and int(summary["requests"]) == len(e.transport.calls)


def test_robots_block_and_failed_host_isolation(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/robots.txt": response("User-agent: *\nDisallow: /\n", "text/plain"),
        "https://bad.test/robots.txt": response("", status=503),
        "https://good.test/": response('<a href="/about">About</a>'),
    }, seeds=[Seed("one", "One", "https://example.test/", "example.test"),
              Seed("bad", "Bad", "https://bad.test/", "bad.test"),
              Seed("good", "Good", "https://good.test/", "good.test")])
    asyncio.run(e.run_queue())
    assert "https://good.test/about" in urls(e)
    assert not any(u == "https://example.test/" or u == "https://bad.test/" for u, _ in e.transport.calls)
    assert all(r["state"] == "blocked" for r in rows(e, "jobs") if r["site"] in {"one", "bad"})


def test_redirect_boundaries_and_observed_www_alias(tmp_path):
    seed = Seed("one", "One", "https://example.test/money-stuff", "example.test", "/money-stuff")
    e = engine(tmp_path, {
        seed.homepage_url: response("", status=301, location="https://www.example.test/money-stuff"),
        "https://www.example.test/money-stuff": response('<a href="/money-stuff/archive">Archive</a><a href="/outside">Outside</a>'),
        "https://www.example.test/money-stuff/archive": response("", status=302, location="/outside"),
    }, seeds=[seed])
    asyncio.run(e.run_queue())
    assert e.aliases["one"] == {"www.example.test"}
    assert "https://www.example.test/outside" not in urls(e)
    assert not any(u == "https://www.example.test/outside" for u, _ in e.transport.calls)
    assert any("outside publication scope" in r["detail"] for r in rows(e, "warnings"))


def test_retry_after_and_host_pacing(tmp_path):
    e = engine(tmp_path, {"https://example.test/": [response("", status=429, **{"retry-after": "7"}), response("done")]})
    result = asyncio.run(e.retry_request(e.seeds["one"], "https://example.test/"))
    assert result.status == 200
    times = [t for _, t in e.transport.calls]
    assert times[1] - times[0] >= 7


def test_retries_are_bounded(tmp_path):
    e = engine(tmp_path, {"https://example.test/": response("", status=503)})
    assert asyncio.run(e.retry_request(e.seeds["one"], "https://example.test/")).status == 503
    assert len(e.transport.calls) == 3


def test_resume_recovers_leases_and_uses_snapshot(tmp_path):
    e = engine(tmp_path, {"https://example.test/": response('<a href="/archive">Archive</a>'),
                           "https://example.test/archive": response('<a href="/historic">Historic</a>')})
    asyncio.run(e.run_queue(max_jobs=1))
    done = {r["url"] for r in rows(e, "jobs") if r["state"] == "done"}
    e.store.db.execute("UPDATE jobs SET state='leased' WHERE state='pending'")
    e.store.db.commit()
    path = tmp_path / "discovery.sqlite3"
    run = e.run
    e.store.db.close()
    resumed = Engine(Store(path), run, e.transport, e.clock)
    before = len(e.transport.calls)
    asyncio.run(resumed.run_queue())
    assert "https://example.test/historic" in urls(resumed)
    assert not any(u in done for u, _ in e.transport.calls[before:])
    assert resumed.config.contact == "test@example.test"


def test_traps_repeated_body_and_oversized_resource(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/": response('<a href="/archive">Archive</a><a href="/calendar/2027">Next month</a><a href="/archive?a=1&a=2">Archive</a>'),
        "https://example.test/archive": response('<a href="/archive?page=2">Next</a>'),
        "https://example.test/archive?page=2": response('<a href="/archive?page=2">Next</a>'),
        "https://example.test/feed/": response(b"x" * 501, "application/xml"),
    }, max_bytes=500)
    asyncio.run(e.run_queue())
    codes = {r["code"] for r in rows(e, "warnings")}
    assert {"calendar_trap", "query_trap", "repeated_document"} <= codes
    assert any("size limit" in r["error"] for r in rows(e, "jobs") if r["error"])


def test_entities_and_compressed_bombs(tmp_path):
    e = engine(tmp_path, {}, max_bytes=100)
    with pytest.raises(ValueError, match="entities"):
        e.parse_xml(e.seeds["one"], "https://example.test/sitemap.xml", b'<!DOCTYPE x [<!ENTITY e "x">]><urlset/>', "sitemap")
    with pytest.raises(ValueError, match="size limit"):
        e.parse_xml(e.seeds["one"], "https://example.test/sitemap.xml", gzip.compress(b"x" * 101), "sitemap")


def test_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="Non-public"):
        Transport().request("https://example.test/", Config("test@example.test"))


def test_shared_host_robots_sitemaps_apply_to_each_seed(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/robots.txt": response("User-agent: *\nSitemap: https://example.test/advertised.xml", "text/plain"),
        "https://example.test/advertised.xml": response('<urlset><url><loc>https://example.test/a/post</loc></url><url><loc>https://example.test/b/post</loc></url></urlset>', "application/xml"),
    }, seeds=[Seed("a", "A", "https://example.test/a", "example.test", "/a"),
              Seed("b", "B", "https://example.test/b", "example.test", "/b")])
    asyncio.run(e.run_queue())
    assert {(r["site"], r["url"]) for r in rows(e, "candidates")} == {
        ("a", "https://example.test/a/post"), ("b", "https://example.test/b/post")}


def test_parse_failure_retains_response_and_no_nonhtml_candidate(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/": response("binary", "application/octet-stream"),
        "https://example.test/sitemap.xml": response('<urlset><url><loc>https://example.test/post</loc></url>', "application/xml"),
    })
    asyncio.run(e.run_queue())
    assert not urls(e)
    assert any(r["body"] == b"binary" for r in rows(e, "resources"))
    assert len([r for r in rows(e, "warnings") if r["code"] == "parse_failure"]) == 2


def test_unadvertised_probe_redirect_and_redirect_loop(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/sitemap.xml": response("", status=302, location="https://other.test/map"),
        "https://example.test/": response("", status=302, location="/loop"),
        "https://example.test/loop": response("", status=302, location="/"),
    })
    asyncio.run(e.run_queue())
    assert not any(u.startswith("https://other.test") for u, _ in e.transport.calls)
    errors = [r["error"] for r in rows(e, "jobs") if r["error"]]
    assert any("Unadvertised" in error for error in errors)
    assert "Redirect loop" in errors


def test_advertised_external_resource_redirect(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/": response('<link rel="alternate" type="application/rss+xml" href="https://feeds.test/rss"/>'),
        "https://feeds.test/rss": response("", status=302, location="https://cdn.test/rss"),
        "https://cdn.test/rss": response('<rss><channel><item><link>https://example.test/post</link><pubDate>2020-01-01</pubDate></item></channel></rss>', "application/rss+xml"),
    })
    asyncio.run(e.run_queue())
    assert "https://example.test/post" in urls(e)
    assert not e.aliases["one"]


def test_same_host_pacing_crawl_delay_and_global_concurrency(tmp_path):
    seeds = [Seed(str(i), str(i), f"https://site{i}.test/", f"site{i}.test") for i in range(5)]
    e = engine(tmp_path, {}, seeds=seeds)
    lock = threading.Lock()
    active = set()
    maximum = [0]

    def request(url, config):
        host = url.split("/")[2]
        with lock:
            assert host not in active
            active.add(host)
            maximum[0] = max(maximum[0], len(active))
            e.transport.calls.append((url, e.clock.monotonic()))
        time.sleep(.002)
        with lock:
            active.remove(host)
        if url.endswith("robots.txt"):
            return response("User-agent: *\nCrawl-delay: 3", "text/plain")
        return response("", status=404)

    e.transport.request = request
    asyncio.run(e.run_queue())
    assert 1 < maximum[0] <= 4
    # Pacing uses a single worker so another host cannot advance the fake clock
    # between scheduling a thread and its recorded start.
    paced = engine(tmp_path / "paced", {
        "https://example.test/robots.txt": response("User-agent: *\nCrawl-delay: 3", "text/plain"),
    }, concurrency=1)
    asyncio.run(paced.run_queue())
    starts = [t for u, t in paced.transport.calls if not u.endswith("robots.txt")]
    assert all(b - a >= 3 for a, b in zip(starts, starts[1:]))


def test_dns_timeout_is_bounded(monkeypatch):
    ready = threading.Event()

    def unresolved(*args, **kwargs):
        ready.wait(.2)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", unresolved)
    try:
        with pytest.raises(TimeoutError, match="DNS"):
            Transport().request("https://example.test/", Config("test@example.test", connect_timeout=.01, total_timeout=.02))
    finally:
        ready.set()


def test_cancelled_worker_leaves_recoverable_lease(tmp_path):
    e = engine(tmp_path, {})

    async def interrupted(job):
        raise asyncio.CancelledError()

    e.process = interrupted
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(e.run_queue(max_jobs=1))
    assert any(r["state"] == "leased" for r in rows(e, "jobs"))
    resumed = Engine(e.store, e.run, e.transport, e.clock)
    asyncio.run(resumed.run_queue())
    assert not any(r["state"] in {"pending", "leased"} for r in rows(resumed, "jobs"))


def test_transaction_rollback_is_atomic(tmp_path):
    e = engine(tmp_path, {})
    with pytest.raises(RuntimeError):
        with e.store.transaction():
            e.store.candidate(e.run, e.seeds["one"], "https://example.test/post", "test", "test")
            e.store.enqueue(e.run, "one", "https://example.test/new", "html", "test")
            raise RuntimeError("interrupted")
    assert not urls(e)
    assert not any(r["url"].endswith("/new") for r in rows(e, "jobs"))


def test_www_robots_redirect_and_rules_on_alias(tmp_path):
    e = engine(tmp_path, {
        "https://example.test/robots.txt": response("", status=301, location="https://www.example.test/robots.txt"),
        "https://www.example.test/robots.txt": response("User-agent: *\nDisallow: /private", "text/plain"),
        "https://example.test/": response("", status=301, location="https://www.example.test/"),
        "https://www.example.test/": response('<a href="/private/archive">Archive</a><a href="/public">Public</a>'),
    })
    asyncio.run(e.run_queue())
    assert "https://www.example.test/public" in urls(e)
    assert not any(u == "https://www.example.test/private/archive" for u, _ in e.transport.calls)
    assert any(r["state"] == "blocked" and r["url"].endswith("private/archive") for r in rows(e, "jobs"))
