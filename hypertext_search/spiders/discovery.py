import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import scrapy
from scrapy import Selector
from scrapy.utils.gz import gunzip
from scrapy.utils.sitemap import Sitemap

from hypertext_search.urls import DOWNLOADS, LISTING, NAVIGATION, Seed, normalize, read_seeds
from hypertext_search.text import feed_content, page_document
from hypertext_search.formatting import feed_html


class DiscoverySpider(scrapy.Spider):
    """Collect public text inside the listed publication scopes, RSS first."""
    name = "discovery"

    def __init__(self, seeds="data/sites.csv", site=None, **kwargs):
        super().__init__(**kwargs)
        self.selected_site = site
        self.seed_path = seeds
        self.state = {}

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        jobdir = crawler.settings.get("JOBDIR")
        if not jobdir:
            raise ValueError("Set JOBDIR to a separate directory for each discovery run")
        spider.jobdir = Path(jobdir)
        spider.jobdir.mkdir(parents=True, exist_ok=True)
        snapshot = spider.jobdir / "discovery-config.json"
        keys = ["CONCURRENT_REQUESTS", "CONCURRENT_REQUESTS_PER_DOMAIN", "DOWNLOAD_DELAY",
                "ROBOTSTXT_OBEY", "USER_AGENT", "DOWNLOAD_TIMEOUT", "DOWNLOAD_MAXSIZE", "RETRY_TIMES", "REDIRECT_MAX_TIMES"]
        config = {key: (crawler.settings.getbool(key) if key == "ROBOTSTXT_OBEY"
                        else crawler.settings[key] if key == "USER_AGENT"
                        else crawler.settings.getfloat(key) if key == "DOWNLOAD_DELAY"
                        else crawler.settings.getint(key)) for key in keys}
        config["DOWNLOAD_SLOTS"] = crawler.settings.getdict("DOWNLOAD_SLOTS")
        if snapshot.exists():
            original = json.loads(snapshot.read_text())
            if original.get("format") != 2 or original["settings"] != config:
                raise ValueError("Resume with the original settings, or use a new JOBDIR")
            seeds = [Seed(**row) for row in original["seeds"]]
        else:
            seeds = read_seeds(spider.seed_path)
            if spider.selected_site:
                seeds = [s for s in seeds if s.site_id == spider.selected_site]
                if not seeds:
                    raise ValueError("Unknown site ID")
            snapshot.write_text(json.dumps({"format": 2, "seeds": [asdict(s) for s in seeds], "settings": config}, indent=2) + "\n")
        if spider.selected_site and {s.site_id for s in seeds} != {spider.selected_site}:
            raise ValueError("JOBDIR belongs to a different site selection; use a fresh directory")
        spider.seeds = {seed.site_id: seed for seed in seeds}
        return spider

    def aliases(self, site):
        return self.state.setdefault("aliases", {}).setdefault(site, set())

    def feed_covers(self, site, url):
        return normalize(url) in self.state.setdefault("feed_full", {}).setdefault(site, set())

    def warning(self, site, url, code, detail):
        log = self.logger.debug if code in {"canonical_hint", "client_navigation_possible", "not_html"} else self.logger.warning
        log("%s %s: %s", code, url, detail)
        return dict(event="warning", site=site, url=url, code=code, detail=detail)

    def request(self, url, site, kind="html", source="", route="advertised"):
        if not self.seeds[site].allows_resource(url, kind, self.aliases(site)):
            return self.warning(site, url, "outside_scope", f"Rejected {kind} link from {source}")
        return scrapy.Request(url, callback=self.parse, errback=self.failed,
                              priority=100 if kind == "feed" else -10 if kind == "page" else 0,
                              cb_kwargs={"site": site, "kind": kind},
                              meta={"site": site, "kind": kind, "source": source, "route": route})

    async def start(self):
        # Start requests are deduplicated on resume, unlike Spider's default
        # start_urls (which deliberately use dont_filter=True).
        for seed in self.seeds.values():
            home = normalize(seed.homepage_url)
            origin = urlunsplit((*urlsplit(home)[:2], "/", "", ""))
            # RSS precedes pages and sitemaps in each host's start queue.
            scoped = urljoin(origin, seed.scope_path.rstrip("/") + "/")
            yield self.request(urljoin(scoped, "feed"), seed.site_id, "feed", "seed", "probe")
            yield self.request(home, seed.site_id, source="seed", route="seed")
            yield self.request(origin + "robots.txt", seed.site_id, "robots", "seed", "robots")
            for path in ["sitemap.xml", "sitemap_index.xml"]:
                yield self.request(origin + path, seed.site_id, "sitemap", "seed", "probe")
            for path in ["feed/", "atom.xml", "rss.xml"]:
                yield self.request(urljoin(origin, seed.scope_path.rstrip("/") + "/" + path), seed.site_id, "feed", "seed", "probe")

    def candidate(self, site, url, source, route, text="", date=""):
        if not url or not self.seeds[site].allows(url, self.aliases(site)) or DOWNLOADS.search(urlsplit(url).path):
            return None
        return dict(event="candidate", site=site, url=url, source=source, route=route,
                    text=text.strip(), date=date, type="listing" if self.listing(url, text) else "unknown")

    @staticmethod
    def listing(url, text="", rel=""):
        p = urlsplit(url)
        return bool(LISTING.search(p.path) or NAVIGATION.search(text) or
                    {"next", "prev"}.intersection(rel.split()) or
                    re.search(r"/(?:blog|posts|notes)/?$|/(?:19|20)\d{2}(?:/\d{1,2})?/?$", p.path) or
                    any(k.lower() in {"page", "paged", "offset", "start"} for k, _ in parse_qsl(p.query)))

    def follow_listing(self, site, url, source, text="", rel=""):
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
        if re.search(r"/calendar(?:/|$)", urlsplit(url).path, re.I) or re.search(r"\b(?:next|prev(?:ious)?) month\b", text, re.I):
            yield self.warning(site, url, "calendar_trap", source)
            return
        if len(pairs) > 3 or len({k for k, _ in pairs}) != len(pairs):
            yield self.warning(site, url, "query_trap", source)
            return
        dimensions = self.state.setdefault("query_values", {}).setdefault(site, {})
        for key, value in pairs:
            if key.lower() in {"page", "paged", "offset", "start", "p"} and value.isdigit():
                continue
            values = dimensions.setdefault(key, set())
            if value not in values and len(values) >= 50:
                yield self.warning(site, url, "query_trap", f"More than 50 values for {key}")
                return
            values.add(value)
        kind = "html" if self.listing(url, text, rel) else "page"
        if not self.feed_covers(site, url):
            yield self.request(url, site, kind, source=source, route="archive" if kind == "html" else "page_link")

    def parse(self, response, site, kind):
        if kind == "robots":
            if response.status == 200:
                for sitemap in re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", response.text):
                    url = normalize(sitemap, response.url)
                    if url:
                        yield self.request(url, site, "sitemap", response.url)
            return
        hashes = self.state.setdefault("response_hashes", {}).setdefault(site, {})
        digest = hashlib.sha256(response.body).hexdigest()
        identity = f"{kind}:{digest}"
        if identity in hashes:
            if kind in {"html", "page"} and "html" in response.headers.get("Content-Type", b"text/html").decode().lower():
                item = self.candidate(site, response.url, response.meta.get("source", ""), "fetched_listing")
                if item:
                    yield item
                document = page_document(response, site)
                if document and not self.feed_covers(site, response.url):
                    yield document
            yield self.warning(site, response.url, "repeated_document", f"Same bytes as {hashes[identity]}; expansion stopped")
            return
        hashes[identity] = response.url
        if kind in {"html", "page"}:
            content_type = response.headers.get("Content-Type", b"").decode().lower()
            if content_type and "html" not in content_type:
                yield self.warning(site, response.url, "not_html", content_type)
                return
            item = self.candidate(site, response.url, response.meta.get("source", ""), "fetched_listing")
            if item:
                yield item
            if not self.feed_covers(site, response.url):
                document = page_document(response, site)
                if document:
                    yield document
                else:
                    yield self.warning(site, response.url, "no_text", "No public HTML text found")
            yield from self.parse_html(response, site)
        else:
            try:
                body = response.body
                if body.startswith(b"\x1f\x8b"):
                    body = gunzip(body, max_size=self.crawler.settings.getint("DOWNLOAD_MAXSIZE"))
                if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", body.replace(b"\x00", b""), re.I):
                    raise ValueError("XML entity declarations rejected")
                if kind == "sitemap":
                    yield from self.parse_sitemap(response, site, body)
                else:
                    yield from self.parse_feed(response, site, body)
            except Exception as exc:
                yield self.warning(site, response.url, "parse_failure", str(exc))

    def parse_html(self, response, site):
        archives = 0
        for link in response.css("a[href], link[href]"):
            url = normalize(link.attrib["href"], response.urljoin(""))
            if not url:
                continue
            rel = link.attrib.get("rel", "").lower()
            typ = link.attrib.get("type", "").lower()
            text = " ".join(link.xpath(".//text()").getall()) or link.attrib.get("title", "")
            if "canonical" in rel.split():
                yield self.warning(site, response.url, "canonical_hint", url)
            elif "alternate" in rel.split() and ("rss" in typ or "atom" in typ):
                yield self.request(url, site, "feed", response.url)
            elif "sitemap" in rel.split() or re.search(r"sitemap[^/]*\.xml(?:\.gz)?$", urlsplit(url).path, re.I):
                yield self.request(url, site, "sitemap", response.url)
            else:
                candidate = self.candidate(site, url, response.url, "html_link", text)
                if candidate:
                    yield candidate
                    if self.listing(url, text, rel):
                        archives += 1
                    yield from self.follow_listing(site, url, response.url, text, rel)
        if response.css("script"):
            yield self.warning(site, response.url, "client_navigation_possible", "Scripts observed; client navigation is not executed")
        if response.meta.get("source") == "seed" and not archives:
            yield self.warning(site, response.url, "archive_gap", "No explicit archive route observed")

    def parse_sitemap(self, response, site, body):
        sitemap = Sitemap(body)
        if sitemap.type not in {"sitemapindex", "urlset"}:
            raise ValueError(f"Unsupported sitemap root: {sitemap.type}")
        for entry in sitemap:
            url = normalize(entry["loc"], response.url)
            if not url:
                continue
            if sitemap.type == "sitemapindex":
                yield self.request(url, site, "sitemap", response.url)
            else:
                candidate = self.candidate(site, url, response.url, "sitemap", date=entry.get("lastmod", ""))
                if candidate:
                    yield candidate
                    yield from self.follow_listing(site, url, response.url)

    def parse_feed(self, response, site, body):
        xml = Selector(body=body, type="xml")
        xml.remove_namespaces()
        if xml.xpath("name(/*)").get().lower() not in {"rss", "feed", "rdf"}:
            raise ValueError("Unsupported feed root")
        for entry in xml.xpath("//item | //entry"):
            date = entry.xpath("string(published | pubDate | date | updated)").get() or ""
            text = entry.xpath("string(title)").get() or ""
            body_text, field, completeness = feed_content(entry)
            for link in entry.xpath("link[not(@rel) or @rel='alternate']"):
                url = normalize(link.attrib.get("href") or link.xpath("string(.)").get(), response.url)
                item = self.candidate(site, url, response.url, "feed", text, date)
                if item:
                    yield item
                    if body_text:
                        yield dict(event="document", site=site, url=url, title=text, body=body_text,
                                   body_html=feed_html(entry, field, response.url),
                                   author=entry.xpath("string(author/name | author | creator)").get() or "",
                                   date=date, source_url=response.url, source_kind="feed",
                                   content_field=field, completeness=completeness)
                        if completeness == "full":
                            self.state.setdefault("feed_full", {}).setdefault(site, set()).add(url)
                    if completeness != "full":
                        yield from self.follow_listing(site, url, response.url)
        for href in xml.xpath("//link[@rel='next']/@href").getall():
            url = normalize(href, response.url)
            if url:
                yield self.request(url, site, "feed", response.url)

    def failed(self, failure):
        request = failure.request
        if str(failure.value) == "Full text already saved from RSS":
            yield dict(event="skipped", site=request.meta["site"], url=request.url, reason=str(failure.value))
            return
        yield dict(event="failure", site=request.meta["site"], url=request.url,
                   kind=request.meta["kind"], source=request.meta.get("source", ""), error=str(failure.value))
