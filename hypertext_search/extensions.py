"""Audit Scrapy signals without owning scheduling, retries, or the queue."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from scrapy import signals


class DiscoveryAudit:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        crawler.discovery_audit = instance
        for signal, handler in [
            (signals.spider_opened, instance.opened),
            (signals.spider_closed, instance.closed),
            (signals.request_scheduled, instance.scheduled),
            (signals.request_dropped, instance.dropped),
            (signals.response_downloaded, instance.downloaded),
            (signals.robots_parsed, instance.robots_parsed),
            (signals.spider_error, instance.spider_error),
        ]:
            crawler.signals.connect(handler, signal=signal)
        return instance

    def opened(self, spider):
        self.jobdir = spider.jobdir
        (self.jobdir / "finish.json").unlink(missing_ok=True)
        self.db = sqlite3.connect(self.jobdir / "results.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, event TEXT, site TEXT, url TEXT, data TEXT, observed TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS requests(fingerprint TEXT PRIMARY KEY, site TEXT, url TEXT, kind TEXT, state TEXT)")
        self.db.commit()

    def record(self, item):
        item.setdefault("observed", datetime.now(timezone.utc).isoformat())
        self.db.execute("INSERT INTO observations(event,site,url,data,observed) VALUES(?,?,?,?,?)",
                        (item["event"], item.get("site", ""), item["url"], json.dumps(item), item["observed"]))
        if item["event"] in {"failure", "skipped"}:
            state = "failed" if item["event"] == "failure" else "skipped"
            self.db.execute("UPDATE requests SET state=? WHERE site=? AND url=?", (state, item["site"], item["url"]))
        self.db.commit()

    def scheduled(self, request, spider):
        if not request.meta.get("site"):
            return
        self.db.execute("INSERT OR IGNORE INTO requests VALUES(?,?,?,?,?)",
                        (self.crawler.request_fingerprinter.fingerprint(request).hex(), request.meta["site"], request.url, request.meta["kind"], "pending"))
        self.db.commit()

    def dropped(self, request, spider):
        if request.meta.get("site"):
            self.record(dict(event="warning", site=request.meta["site"], url=request.url,
                             code="duplicate_request", detail="Scrapy's duplicate filter suppressed this request"))

    def downloaded(self, response, request, spider):
        spider.logger.info("%s %s", response.status, response.url)
        digest = hashlib.sha256(response.body).hexdigest()
        folder = self.jobdir / "responses"
        folder.mkdir(exist_ok=True)
        path = folder / digest
        if not path.exists():
            path.write_bytes(response.body)
        self.record(dict(event="fetch", site=request.meta.get("site", ""), url=response.url,
                         status=response.status, kind=request.meta.get("kind", "robots"), hash=digest,
                         source=request.meta.get("source", ""),
                         download_slot=request.meta.get("download_slot", ""),
                         headers=dict(response.headers.to_unicode_dict()), redirects=request.meta.get("redirect_urls", [])))
        self.db.execute("UPDATE requests SET state=? WHERE fingerprint=?",
                        ("downloaded" if response.status < 400 else "failed",
                         self.crawler.request_fingerprinter.fingerprint(request).hex()))
        self.db.commit()
        if request.meta.get("dont_obey_robotstxt") and response.status != 200:
            self.record(dict(event="warning", site="", url=response.url, code="robots_unavailable",
                             detail=f"HTTP {response.status}; proceeding with native robots behavior"))

    def robots_parsed(self, robotparser, request):
        # Scrapy checks Allow/Disallow itself. Crawl-delay is a separate
        # directive, so apply it to the native downloader slot.
        delay = robotparser.crawl_delay(self.crawler.settings["USER_AGENT"])
        if delay:
            downloader = self.crawler.engine.downloader
            slot = downloader.slots.get(request.meta.get("download_slot", urlsplit(request.url).hostname))
            if slot:
                slot.delay = max(slot.delay, delay)

    def spider_error(self, failure, response, spider):
        self.record(dict(event="failure", site=response.meta.get("site", ""), url=response.url, error=str(failure.value)))

    def closed(self, spider, reason):
        stats = self.crawler.stats.get_stats()
        (self.jobdir / "stats.json").write_text(json.dumps(stats, indent=2, default=str) + "\n")
        (self.jobdir / "finish.json").write_text(json.dumps({"reason": reason}) + "\n")
        self.db.close()
