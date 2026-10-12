"""Project-specific guards plugged into Scrapy's downloader."""
import hashlib
import ipaddress
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from scrapy.exceptions import IgnoreRequest
from scrapy.resolver import CachingThreadedResolver
from scrapy.utils.request import RequestFingerprinter


def public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast:
        raise IgnoreRequest(f"Non-public network address: {value}")
    return value


class PublicResolver(CachingThreadedResolver):
    def getHostByName(self, name, timeout=()):
        return super().getHostByName(name, timeout).addCallback(public_address)


class ScopeFingerprinter(RequestFingerprinter):
    """Each scope gets its own callback for shared-host sitemap resources."""
    def fingerprint(self, request):
        context = f"{request.meta.get('site', '')}:{request.meta.get('kind', '')}"
        return hashlib.sha256(super().fingerprint(request) + context.encode()).digest()


class PlatformSlotMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        return instance

    def process_request(self, request):
        host = urlsplit(request.url).hostname or ""
        known = self.crawler.spider.state.setdefault("substack_hosts", set())
        if host == "substack.com" or host.endswith(".substack.com") or host in known:
            request.meta["download_slot"] = "substack"

    def process_response(self, request, response):
        if response.headers.get("X-Served-By", b"").lower() == b"substack":
            self.crawler.spider.state.setdefault("substack_hosts", set()).add(urlsplit(response.url).hostname)
        return response


class ScopeMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        return instance

    def process_request(self, request):
        p = urlsplit(request.url)
        if p.scheme not in {"http", "https"}:
            raise IgnoreRequest("Only HTTP(S) discovery resources are allowed")
        if p.username or p.password:
            raise IgnoreRequest("URLs containing credentials are rejected")
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            pass
        else:
            public_address(p.hostname)
        site = request.meta.get("site")
        if site is None:  # Scrapy's own robots request; DNS guard still applies.
            chain = request.meta.get("redirect_urls", [])
            if chain and p.hostname.removeprefix("www.") != urlsplit(chain[0]).hostname.removeprefix("www."):
                raise IgnoreRequest("Robots redirect outside publication host")
            return
        spider = self.crawler.spider
        seed = spider.seeds[site]
        chain = request.meta.get("redirect_urls", [])
        if any(urlsplit(u).scheme == "https" for u in chain) and p.scheme != "https":
            raise IgnoreRequest("HTTPS downgrade redirect")
        kind = request.meta["kind"]
        if not seed.allows_resource(request.url, kind, spider.aliases(site)):
            if (not chain or p.hostname.removeprefix("www.") != seed.hostname.removeprefix("www.")
                    or not seed.allows_resource(request.url, kind, {p.hostname})):
                raise IgnoreRequest(f"Outside publication scope: {request.url}")
            spider.aliases(site).add(p.hostname)
            self.crawler.discovery_audit.record(dict(event="alias", site=site, url=request.url, source=chain[-1]))
        if kind in {"html", "page"} and spider.feed_covers(site, request.url):
            raise IgnoreRequest("Full text already saved from RSS")


    def process_exception(self, request, exception):
        if isinstance(exception, IgnoreRequest):
            return
        self.crawler.discovery_audit.record(dict(
            event="attempt_error", site=request.meta.get("site", ""), url=request.url, error=str(exception)))
        if request.meta.get("dont_obey_robotstxt"):
            self.crawler.discovery_audit.record(dict(event="warning", site="", url=request.url,
                code="robots_unavailable", detail=f"{exception}; proceeding with native robots behavior"))


class RetryAfterMiddleware:
    """Let native RetryMiddleware handle retries after the requested pause."""
    async def process_response(self, request, response):
        if response.status not in {429, 503} or not response.headers.get("Retry-After"):
            return response
        value = response.headers["Retry-After"].decode()
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return response
        if math.isfinite(seconds) and seconds > 0:
            from twisted.internet import reactor
            from twisted.internet.task import deferLater
            from scrapy.utils.defer import maybe_deferred_to_future
            await maybe_deferred_to_future(deferLater(reactor, seconds, lambda: None))
        return response
