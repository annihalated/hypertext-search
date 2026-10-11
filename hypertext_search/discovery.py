from __future__ import annotations

import asyncio
import csv
import gzip
import hashlib
import http.client
import io
import ipaddress
import json
import re
import queue
import socket
import sqlite3
import ssl
import time
import threading
import urllib.robotparser
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

MAX_BODY = 10 * 1024 * 1024
DOWNLOADS = re.compile(r"\.(?:pdf|zip|jpg|jpeg|png|gif|svg|webp|mp3|mp4|css|js|woff2?|exe|dmg)(?:$)", re.I)
LISTING = re.compile(r"(?:^|/)(?:archives?|categories?|tags?|topics?|page|pages|posts|blog|notes)(?:/|$)", re.I)
TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(url: str, base: str = "") -> str | None:
    try:
        p = urlsplit(urljoin(base, url.strip()))
        if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        host = p.hostname.encode("idna").decode().lower()
        if ":" in host:
            host = f"[{host}]"
        port = p.port
        if port and port != (443 if p.scheme.lower() == "https" else 80):
            host += f":{port}"
        # Reject encoded path separators/dot traversal rather than weakening scope.
        if re.search(r"%(?:2f|5c|2e|25)|\\", p.path, re.I):
            return None
        parts = []
        for part in (p.path or "/").split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        path = "/".join(parts) or "/"
        if not path.startswith("/"):
            path = "/" + path
        pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
        return urlunsplit((p.scheme.lower(), host, path, urlencode(pairs), ""))
    except (ValueError, UnicodeError):
        return None


@dataclass(frozen=True)
class Seed:
    site_id: str
    name: str
    homepage_url: str
    hostname: str
    scope_path: str = "/"
    sources: str = ""
    source_ids: str = ""

    def allows(self, url: str, aliases: set[str] | None = None) -> bool:
        normalized = normalize(url)
        if normalized is None:
            return False
        url = normalized
        p = urlsplit(url)
        hosts = {self.hostname.lower()} | (aliases or set())
        scope = self.scope_path.rstrip("/")
        return p.hostname in hosts and (not scope or p.path == scope or p.path.startswith(scope + "/"))


def read_seeds(path: Path) -> list[Seed]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    seeds = [Seed(**{k: row[k] for k in Seed.__dataclass_fields__}) for row in rows]
    if len({s.site_id for s in seeds}) != len(seeds):
        raise ValueError("Duplicate seed IDs")
    for s in seeds:
        u = normalize(s.homepage_url)
        if not u or not s.allows(u) or not s.scope_path.startswith("/"):
            raise ValueError(f"Invalid scope: {s.site_id}")
    return seeds


@dataclass(frozen=True)
class Config:
    contact: str
    concurrency: int = 4
    interval: float = 1.0
    connect_timeout: float = 10.0
    total_timeout: float = 30.0
    max_bytes: int = MAX_BODY
    redirects: int = 5
    retries: int = 2

    def __post_init__(self):
        if not self.contact.strip() or any(c in self.contact for c in "\r\n"):
            raise ValueError("An operator contact is required")
        if self.concurrency < 1 or self.interval < 1 or self.max_bytes < 1:
            raise ValueError("Invalid concurrency, interval, or body limit")
        if self.connect_timeout <= 0 or self.total_timeout <= 0 or self.redirects < 0 or self.retries < 0:
            raise ValueError("Invalid timeout/retry limits")

    @property
    def user_agent(self):
        return f"HypertextSearch/0.1 (+{self.contact})"


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def inflate(data: bytes, limit: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        body = f.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Decompressed response exceeds size limit")
    return body


class Transport:
    """Direct HTTP with pinned public DNS addresses; no proxies or automatic redirects."""

    def request(self, url: str, config: Config) -> Response:
        p = urlsplit(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        deadline = time.monotonic() + config.total_timeout
        resolved = queue.Queue(maxsize=1)

        def resolve():
            try:
                resolved.put(socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM))
            except Exception as exc:
                resolved.put(exc)

        threading.Thread(target=resolve, daemon=True).start()
        try:
            addresses = resolved.get(timeout=min(config.connect_timeout, config.total_timeout))
        except queue.Empty:
            raise TimeoutError("DNS resolution timeout") from None
        if isinstance(addresses, Exception):
            raise addresses
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global
                                or ipaddress.ip_address(a[4][0]).is_multicast for a in addresses):
            raise ValueError("Non-public network address rejected")
        connection = (http.client.HTTPSConnection(p.hostname, port, timeout=config.connect_timeout,
                      context=ssl.create_default_context()) if p.scheme == "https"
                      else http.client.HTTPConnection(p.hostname, port, timeout=config.connect_timeout))

        def pinned_connect(address, timeout=None, source_address=None):
            last = None
            for family, socktype, proto, _, sockaddr in addresses:
                sock = socket.socket(family, socktype, proto)
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Total request timeout")
                    sock.settimeout(min(config.connect_timeout, remaining))
                    sock.connect(sockaddr)
                    return sock
                except OSError as exc:
                    sock.close()
                    last = exc
            raise last or OSError("No address")

        connection._create_connection = pinned_connect
        try:
            connection.connect()
            connection.sock.settimeout(max(.001, deadline - time.monotonic()))
            connection.request("GET", urlunsplit(("", "", p.path or "/", p.query, "")),
                               headers={"User-Agent": config.user_agent, "Accept-Encoding": "gzip"})
            response = connection.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            if int(headers.get("content-length", "0")) > config.max_bytes:
                raise ValueError("Response exceeds size limit")
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Total request timeout")
                if connection.sock:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, config.max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > config.max_bytes:
                    raise ValueError("Response exceeds size limit")
            result = bytes(body)
            if headers.get("content-encoding", "").lower() == "gzip":
                result = inflate(result, config.max_bytes)
            return Response(response.status, headers, result)
        finally:
            connection.close()


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None
        self.base = None
        self.scripts = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and self.base is None:
            self.base = a.get("href")
        if tag == "script":
            self.scripts = True
        if tag in {"a", "link"} and a.get("href"):
            link = {"href": a["href"], "rel": a.get("rel", "").lower(),
                    "type": a.get("type", "").lower(), "text": a.get("title", "")}
            self.links.append(link)
            if tag == "a":
                self.current = link

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, config TEXT, seeds TEXT);
CREATE TABLE IF NOT EXISTS seeds(run INTEGER, site TEXT, snapshot TEXT, resolved TEXT, PRIMARY KEY(run,site));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, run INTEGER, site TEXT, url TEXT, kind TEXT,
 source TEXT, state TEXT DEFAULT 'pending', error TEXT, UNIQUE(run,site,url,kind));
CREATE TABLE IF NOT EXISTS resources(id INTEGER PRIMARY KEY, run INTEGER, site TEXT, url TEXT,
 kind TEXT, status INTEGER, headers TEXT, body BLOB, hash TEXT, fetched TEXT);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, run INTEGER, site TEXT, url TEXT,
 status INTEGER, error TEXT, fetched TEXT);
CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY, run INTEGER, site TEXT, url TEXT,
 type TEXT, UNIQUE(run,site,url));
CREATE TABLE IF NOT EXISTS provenance(id INTEGER PRIMARY KEY, run INTEGER, site TEXT, url TEXT,
 source TEXT, route TEXT, text TEXT, date TEXT, observed TEXT,
 UNIQUE(run,site,url,source,route,text,date));
CREATE TABLE IF NOT EXISTS warnings(id INTEGER PRIMARY KEY, run INTEGER, site TEXT, url TEXT, code TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS aliases(run INTEGER, site TEXT, host TEXT, source TEXT, PRIMARY KEY(run,site,host));
CREATE INDEX IF NOT EXISTS queue ON jobs(run,state);
CREATE INDEX IF NOT EXISTS resource_hash ON resources(run,site,hash);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.depth = 0

    @contextmanager
    def transaction(self):
        savepoint = f"discovery_{self.depth}"
        self.depth += 1
        self.db.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
            self.db.execute(f"RELEASE {savepoint}")
            if self.depth == 1:
                self.db.commit()
        except BaseException:
            self.db.execute(f"ROLLBACK TO {savepoint}")
            self.db.execute(f"RELEASE {savepoint}")
            raise
        finally:
            self.depth -= 1

    def start(self, seeds: list[Seed], config: Config) -> int:
        with self.transaction():
            run = self.db.execute("INSERT INTO runs(started,config,seeds) VALUES(?,?,?)",
                       (now(), json.dumps(asdict(config)), json.dumps([asdict(s) for s in seeds]))).lastrowid
            for seed in seeds:
                self.db.execute("INSERT INTO seeds VALUES(?,?,?,?)", (run, seed.site_id, json.dumps(asdict(seed)), None))
                home = normalize(seed.homepage_url)
                self.enqueue(run, seed.site_id, home, "html", "seed")
                root = urlunsplit((*urlsplit(home)[:2], "/", "", ""))
                for path, kind in [("sitemap.xml", "sitemap"), ("sitemap_index.xml", "sitemap"),
                                   ("feed/", "feed"), ("atom.xml", "feed"), ("rss.xml", "feed")]:
                    # Feed probes stay inside the publication scope.
                    base = root if kind == "sitemap" else urljoin(root, seed.scope_path.rstrip("/") + "/")
                    self.enqueue(run, seed.site_id, urljoin(base, path), kind, "probe")
        return run

    def enqueue(self, run, site, url, kind, source):
        if url:
            self.db.execute("INSERT OR IGNORE INTO jobs(run,site,url,kind,source) VALUES(?,?,?,?,?)", (run, site, url, kind, source))

    def warn(self, run, site, url, code, detail):
        self.db.execute("INSERT INTO warnings(run,site,url,code,detail) VALUES(?,?,?,?,?)", (run, site, url, code, detail))

    def candidate(self, run, seed, url, source, route, text="", date="", aliases=None):
        if not url or not seed.allows(url, aliases) or DOWNLOADS.search(urlsplit(url).path):
            return
        inferred = "listing" if LISTING.search(urlsplit(url).path) else "unknown"
        self.db.execute("INSERT OR IGNORE INTO candidates(run,site,url,type) VALUES(?,?,?,?)", (run, seed.site_id, url, inferred))
        self.db.execute("INSERT OR IGNORE INTO provenance(run,site,url,source,route,text,date,observed) VALUES(?,?,?,?,?,?,?,?)",
                        (run, seed.site_id, url, source, route, text.strip(), date, now()))


class Clock:
    def monotonic(self):
        return time.monotonic()

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class Engine:
    def __init__(self, store: Store, run: int, transport=None, clock=None):
        self.store, self.run = store, run
        row = store.db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
        if row is None:
            raise ValueError("Unknown run")
        self.config = Config(**json.loads(row["config"]))
        self.seeds = {s["site_id"]: Seed(**s) for s in json.loads(row["seeds"])}
        self.transport = transport or Transport()
        self.clock = clock or Clock()
        self.locks = {}
        self.next_start = {}
        self.robots = {}
        self.robot_locks = {}
        self.aliases = {s: set() for s in self.seeds}
        for a in store.db.execute("SELECT * FROM aliases WHERE run=?", (run,)):
            self.aliases[a["site"]].add(a["host"])

    async def request(self, seed, url, delay=1):
        host = urlsplit(url).hostname
        async with self.locks.setdefault(host, asyncio.Lock()):
            await self.clock.sleep(max(0, self.next_start.get(host, 0) - self.clock.monotonic()))
            self.next_start[host] = self.clock.monotonic() + max(self.config.interval, delay)
            try:
                response = await asyncio.to_thread(self.transport.request, url, self.config)
                if len(response.body) > self.config.max_bytes:
                    raise ValueError("Response exceeds size limit")
                self.store.db.execute("INSERT INTO attempts(run,site,url,status,fetched) VALUES(?,?,?,?,?)",
                                      (self.run, seed.site_id, url, response.status, now()))
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    try:
                        pause = float(retry_after)
                    except ValueError:
                        try:
                            pause = parsedate_to_datetime(retry_after).timestamp() - datetime.now(timezone.utc).timestamp()
                        except (ValueError, TypeError, OverflowError):
                            pause = 0
                    self.next_start[host] = max(self.next_start[host], self.clock.monotonic() + max(0, pause))
                self.store.db.commit()
                return response
            except Exception as exc:
                self.store.db.execute("INSERT INTO attempts(run,site,url,error,fetched) VALUES(?,?,?,?,?)",
                                      (self.run, seed.site_id, url, str(exc), now()))
                self.store.db.commit()
                raise

    async def retry_request(self, seed, url, delay=1):
        for attempt in range(self.config.retries + 1):
            try:
                response = await self.request(seed, url, delay)
                if response.status != 429 and response.status < 500:
                    return response
                if attempt == self.config.retries:
                    return response
            except (OSError, TimeoutError, http.client.HTTPException):
                if attempt == self.config.retries:
                    raise
            await self.clock.sleep(2 ** attempt)

    async def rules(self, seed, url):
        p = urlsplit(url)
        origin = urlunsplit((p.scheme, p.netloc, "", "", ""))
        async with self.robot_locks.setdefault(origin, asyncio.Lock()):
            if origin in self.robots:
                parser = self.robots[origin]
                if parser is not None:
                    for sitemap in parser.site_maps() or []:
                        self.store.enqueue(self.run, seed.site_id, normalize(sitemap, origin + "/robots.txt"), "sitemap", origin + "/robots.txt")
                    self.store.db.commit()
                return self.robots[origin]
            robots_url = origin + "/robots.txt"
            try:
                # Robots may upgrade HTTPS or use the www spelling. Other host
                # redirects require review and cannot silently widen scope.
                current = robots_url
                visited = set()
                for _ in range(self.config.redirects + 1):
                    if current in visited:
                        raise ValueError("Robots redirect loop")
                    visited.add(current)
                    response = await self.retry_request(seed, current)
                    if response.status in {301, 302, 303, 307, 308}:
                        target = normalize(response.headers.get("location", ""), current)
                        if not response.headers.get("location") or not target:
                            raise ValueError("Invalid robots redirect")
                        a, b = urlsplit(current), urlsplit(target)
                        if (a.hostname.removeprefix("www.") != b.hostname.removeprefix("www.")
                                or (a.scheme == "https" and b.scheme != "https")):
                            raise ValueError("Robots redirect needs review")
                        current = target
                        continue
                    break
                else:
                    raise ValueError("Robots redirect limit")
                parser = urllib.robotparser.RobotFileParser(robots_url)
                if response.status == 404:
                    parser.parse([])
                elif response.status == 200:
                    parser.parse(response.body.decode("utf-8", "replace").splitlines())
                else:
                    raise ValueError(f"Robots unavailable: HTTP {response.status}")
                with self.store.transaction():
                    self.resource(seed, current, "robots", response)
                    for sitemap in parser.site_maps() or []:
                        target = normalize(sitemap, current)
                        self.store.enqueue(self.run, seed.site_id, target, "sitemap", current)
                self.robots[origin] = parser
            except Exception as exc:
                self.robots[origin] = None
                self.store.warn(self.run, seed.site_id, robots_url, "robots_unavailable", str(exc))
                self.store.db.commit()
            return self.robots[origin]

    def resource(self, seed, url, kind, response):
        digest = hashlib.sha256(response.body).hexdigest()
        self.store.db.execute("INSERT INTO resources(run,site,url,kind,status,headers,body,hash,fetched) VALUES(?,?,?,?,?,?,?,?,?)",
            (self.run, seed.site_id, url, kind, response.status, json.dumps(response.headers), response.body, digest, now()))
        return digest

    async def fetch(self, seed, job):
        url = job["url"]
        visited = set()
        for step in range(self.config.redirects + 1):
            if url in visited:
                raise ValueError("Redirect loop")
            visited.add(url)
            rules = await self.rules(seed, url)
            if rules is None:
                return url, None, "robots unavailable"
            if not rules.can_fetch(self.config.user_agent, url):
                return url, None, "robots disallowed"
            delay = rules.crawl_delay(self.config.user_agent) or rules.crawl_delay("*") or 1
            rate = rules.request_rate(self.config.user_agent)
            if rate and rate.requests:
                delay = max(delay, rate.seconds / rate.requests)
            response = await self.retry_request(seed, url, delay)
            if response.status not in {301, 302, 303, 307, 308}:
                return url, response, None
            target = normalize(response.headers.get("location", ""), url)
            if not response.headers.get("location") or not target:
                raise ValueError("Invalid redirect")
            if step == self.config.redirects:
                raise ValueError("Redirect limit")
            a, b = urlsplit(url), urlsplit(target)
            if a.scheme == "https" and b.scheme != "https":
                raise ValueError("HTTPS downgrade redirect")
            if a.scheme == "http" and b.scheme == "https" and a.hostname == b.hostname:
                self.store.warn(self.run, seed.site_id, url, "https_upgrade", target)
                self.store.db.commit()
            if (job["kind"] != "html" and job["source"] == "probe" and
                    a.hostname != b.hostname and
                    a.hostname.removeprefix("www.") != b.hostname.removeprefix("www.")):
                raise ValueError(f"Unadvertised external probe redirect needs review: {target}")
            if a.hostname != b.hostname and job["kind"] == "html":
                if a.hostname.removeprefix("www.") != b.hostname.removeprefix("www."):
                    raise ValueError(f"Host redirect needs review: {target}")
                if not Seed(seed.site_id, seed.name, target, b.hostname, seed.scope_path).allows(target):
                    raise ValueError(f"Alias redirect outside path scope: {target}")
                other = await self.rules(seed, target)
                if other is None or not other.can_fetch(self.config.user_agent, target):
                    return target, None, "alias robots unavailable/disallowed"
                self.aliases[seed.site_id].add(b.hostname)
                self.store.db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?,?,?)", (self.run, seed.site_id, b.hostname, url))
                self.store.warn(self.run, seed.site_id, url, "observed_alias", target)
                self.store.db.commit()
            if job["kind"] == "html" and not seed.allows(target, self.aliases[seed.site_id]):
                raise ValueError(f"Redirect outside publication scope: {target}")
            url = target
        raise ValueError("Redirect limit")

    def schedule_listing(self, seed, url, source, text, rel):
        if not seed.allows(url, self.aliases[seed.site_id]):
            return
        p = urlsplit(url)
        if re.search(r"\b(?:next|previous|prev)\s+month\b", text, re.I) or re.search(r"/calendar(?:/|$)", p.path):
            self.store.warn(self.run, seed.site_id, url, "calendar_trap", f"Navigation from {source}")
            return
        pairs = parse_qsl(p.query, keep_blank_values=True)
        if len(pairs) > 3 or len({k for k, _ in pairs}) != len(pairs):
            self.store.warn(self.run, seed.site_id, url, "query_trap", f"Expanding query from {source}")
            return
        # Linear numeric pagination is intentionally unbounded. Other query
        # dimensions are quarantined once they exhibit large cardinality.
        for key, value in pairs:
            if key.lower() in {"page", "paged", "p", "offset", "start"} and value.isdigit():
                continue
            previous = self.store.db.execute("SELECT url FROM jobs WHERE run=? AND site=? AND kind='html'", (self.run, seed.site_id))
            values = {v for row in previous for k, v in parse_qsl(urlsplit(row[0]).query) if k == key}
            if value not in values and len(values) >= 50:
                self.store.warn(self.run, seed.site_id, url, "query_trap", f"More than 50 distinct values for {key}")
                return
        if self.store.db.execute("SELECT 1 FROM jobs WHERE run=? AND site=? AND url=? AND kind='html'", (self.run, seed.site_id, url)).fetchone():
            if "next" in rel.split() or re.search(r"\bnext\b", text, re.I):
                self.store.warn(self.run, seed.site_id, url, "pagination_loop", f"Repeated target from {source}")
            return
        self.store.enqueue(self.run, seed.site_id, url, "html", source)

    def parse_html(self, seed, url, body):
        parser = Links()
        parser.feed(body.decode("utf-8", "replace"))
        base = normalize(parser.base or url, url) or url
        aliases = self.aliases[seed.site_id]
        archive_routes = 0
        for link in parser.links:
            target = normalize(link["href"], base)
            if not target:
                continue
            text, rel, typ = link["text"], link["rel"], link["type"]
            if "canonical" in rel.split():
                self.store.warn(self.run, seed.site_id, url, "canonical_hint", target)
                continue
            if "alternate" in rel.split() and ("rss" in typ or "atom" in typ):
                self.store.enqueue(self.run, seed.site_id, target, "feed", url)
                continue
            if target == base and not link["href"].strip():
                continue
            if "sitemap" in rel.split() or re.search(r"sitemap[^/]*\.xml(?:\.gz)?$", urlsplit(target).path, re.I):
                self.store.enqueue(self.run, seed.site_id, target, "sitemap", url)
                continue
            if DOWNLOADS.search(urlsplit(target).path):
                continue
            self.store.candidate(self.run, seed, target, url, "html_link", text, aliases=aliases)
            if not seed.allows(target, aliases):
                continue
            listing = (LISTING.search(urlsplit(target).path) or
                       re.search(r"\b(?:archive|archives|older|newer|next|previous|categories|all posts)\b", text, re.I) or
                       {"next", "prev"}.intersection(rel.split()) or
                       re.search(r"/(?:19|20)\d{2}(?:/\d{1,2})?/?$", urlsplit(target).path) or
                       any(k.lower() in {"page", "paged", "offset"} for k, _ in parse_qsl(urlsplit(target).query)))
            # A post underneath /blog/ is a candidate, not an archive route.
            if listing and (not LISTING.search(urlsplit(target).path) or
                            re.search(r"/(?:archives?|categories?|tags?|topics?|page|pages)(?:/|$)", urlsplit(target).path, re.I) or
                            re.search(r"/(?:blog|posts|notes)/?$", urlsplit(target).path, re.I) or
                            re.search(r"\b(?:older|newer|next|previous|archive|all posts)\b", text, re.I) or
                            {"next", "prev"}.intersection(rel.split()) or urlsplit(target).query):
                archive_routes += 1
                self.schedule_listing(seed, target, url, text, rel)
        if parser.scripts:
            self.store.warn(self.run, seed.site_id, url, "client_navigation_possible", "Scripts observed; client-rendered links are not executed")
        if url == normalize(seed.homepage_url) and archive_routes == 0:
            self.store.warn(self.run, seed.site_id, url, "archive_gap", "No explicit archive route observed on homepage")

    def parse_xml(self, seed, url, body, kind):
        if body.startswith(b"\x1f\x8b"):
            body = inflate(body, self.config.max_bytes)
        # XML entity declarations are rejected before parsing, including UTF-16/32.
        if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", body.replace(b"\x00", b""), re.I):
            raise ValueError("XML declarations/entities rejected")
        root = ET.fromstring(body)
        local = lambda e: e.tag.rsplit("}", 1)[-1].lower()
        child_text = lambda e, name: next((c.text or "" for c in e if local(c) == name), "")
        tag = local(root)
        aliases = self.aliases[seed.site_id]
        if tag not in {"sitemapindex", "urlset", "rss", "feed", "rdf"}:
            raise ValueError(f"Unsupported XML root: {tag}")
        if tag == "sitemapindex":
            for entry in root:
                target = normalize(child_text(entry, "loc"), url)
                self.store.enqueue(self.run, seed.site_id, target, "sitemap", url)
        elif tag == "urlset":
            for entry in root:
                target = normalize(child_text(entry, "loc"), url)
                self.store.candidate(self.run, seed, target, url, "sitemap", date=child_text(entry, "lastmod"), aliases=aliases)
                if target and seed.allows(target, aliases) and re.search(r"/(?:archives?|categories?|tags?|page)(?:/|$)", urlsplit(target).path):
                    self.schedule_listing(seed, target, url, "", "")
        else:
            for entry in root.iter():
                if local(entry) in {"item", "entry"}:
                    date = child_text(entry, "published") or child_text(entry, "pubdate") or child_text(entry, "date") or child_text(entry, "updated")
                    for link in entry:
                        if local(link) == "link" and link.get("rel", "alternate") == "alternate":
                            target = normalize(link.get("href") or link.text or "", url)
                            self.store.candidate(self.run, seed, target, url, "feed", child_text(entry, "title"), date, aliases)
                elif local(entry) == "link" and entry.get("rel") == "next":
                    target = normalize(entry.get("href", ""), url)
                    self.store.enqueue(self.run, seed.site_id, target, "feed", url)

    async def process(self, job):
        seed = self.seeds[job["site"]]
        try:
            url, response, blocked = await self.fetch(seed, job)
            with self.store.transaction():
                if blocked:
                    self.store.warn(self.run, seed.site_id, url, "robots_blocked", blocked)
                    state, error = "blocked", blocked
                elif response.status != 200:
                    self.resource(seed, url, job["kind"], response)
                    state, error = "failed", f"HTTP {response.status}"
                    self.store.warn(self.run, seed.site_id, url, "http_failure", error)
                else:
                    digest = hashlib.sha256(response.body).hexdigest()
                    duplicate = self.store.db.execute("SELECT url FROM resources WHERE run=? AND site=? AND kind=? AND hash=? AND status=200",
                                                      (self.run, seed.site_id, job["kind"], digest)).fetchone()
                    self.resource(seed, url, job["kind"], response)
                    content_type = response.headers.get("content-type", "").lower()
                    is_html = not content_type or "html" in content_type
                    if job["kind"] == "html" and is_html:
                        self.store.db.execute("UPDATE seeds SET resolved=? WHERE run=? AND site=? AND ?='seed'", (url, self.run, seed.site_id, job["source"]))
                        self.store.candidate(self.run, seed, url, job["url"], "fetched_listing", aliases=self.aliases[seed.site_id])
                    if duplicate:
                        self.store.warn(self.run, seed.site_id, url, "repeated_document", f"Same content as {duplicate[0]}; expansion stopped")
                    else:
                        try:
                            with self.store.transaction():
                                if job["kind"] == "html":
                                    if not is_html:
                                        raise ValueError(f"Discovery page is not HTML: {content_type}")
                                    self.parse_html(seed, url, response.body)
                                else:
                                    self.parse_xml(seed, url, response.body, job["kind"])
                        except Exception as exc:
                            self.store.warn(self.run, seed.site_id, url, "parse_failure", str(exc))
                            self.store.db.execute("UPDATE jobs SET state='failed',error=? WHERE id=?", (str(exc), job["id"]))
                            return
                    state, error = "done", None
                self.store.db.execute("UPDATE jobs SET state=?,error=? WHERE id=?", (state, error, job["id"]))
        except Exception as exc:
            with self.store.transaction():
                self.store.warn(self.run, seed.site_id, job["url"], "resource_failure", str(exc))
                self.store.db.execute("UPDATE jobs SET state='failed',error=? WHERE id=?", (str(exc), job["id"]))

    async def run_queue(self, max_jobs=None):
        # CLI holds an exclusive database lock; abandoned leases are safe to recover.
        self.store.db.execute("UPDATE jobs SET state='pending' WHERE run=? AND state='leased'", (self.run,))
        self.store.db.commit()
        active = set()
        launched = 0
        try:
            while True:
                while len(active) < self.config.concurrency and (max_jobs is None or launched < max_jobs):
                    job = self.store.db.execute("SELECT * FROM jobs WHERE run=? AND state='pending' ORDER BY id LIMIT 1", (self.run,)).fetchone()
                    if not job:
                        break
                    self.store.db.execute("UPDATE jobs SET state='leased' WHERE id=?", (job["id"],))
                    self.store.db.commit()
                    active.add(asyncio.create_task(self.process(job)))
                    launched += 1
                if not active:
                    break
                finished, active = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    task.result()
        finally:
            if active:
                # Allow bounded in-flight requests to finish before closing SQLite.
                await asyncio.gather(*active, return_exceptions=True)
            self.store.db.commit()


def export(store: Store, run: int, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    db = store.db
    summaries = []
    for seed in db.execute("SELECT * FROM seeds WHERE run=? ORDER BY site", (run,)):
        site = seed["site"]
        snapshot = json.loads(seed["snapshot"])
        states = {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM jobs WHERE run=? AND site=? GROUP BY state", (run, site))}
        routes = [r[0] for r in db.execute("SELECT DISTINCT route FROM provenance WHERE run=? AND site=?", (run, site))]
        counts = lambda table: db.execute(f"SELECT count(*) FROM {table} WHERE run=? AND site=?", (run, site)).fetchone()[0]
        home = db.execute("SELECT state FROM jobs WHERE run=? AND site=? AND source='seed'", (run, site)).fetchone()
        outcome = "unfinished" if states.get("pending", 0) + states.get("leased", 0) else "blocked" if home and home[0] == "blocked" else "failed" if home and home[0] == "failed" else "routes_exhausted_with_gaps" if counts("warnings") else "known_routes_exhausted"
        dates = [dict(r) for r in db.execute("SELECT url,source,date FROM provenance WHERE run=? AND site=? AND date<>'' ORDER BY date", (run, site))]
        summaries.append({"site_id": site, "name": snapshot["name"], "sources": snapshot["sources"],
            "source_ids": snapshot["source_ids"], "resolved": seed["resolved"], "outcome": outcome,
            "routes": ",".join(routes), "candidates": counts("candidates"), "requests": counts("attempts"),
            "failed": states.get("failed", 0), "blocked": states.get("blocked", 0),
            "unfinished": states.get("pending", 0) + states.get("leased", 0), "warnings": counts("warnings"),
            "date_evidence": json.dumps(dates)})
    def write_csv(name, rows, fields):
        with (destination / name).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    write_csv("seeds.csv", summaries, list(summaries[0]) if summaries else ["site_id"])
    for table, fields in [("candidates", ["site", "url", "type"]),
                          ("provenance", ["site", "url", "source", "route", "text", "date", "observed"]),
                          ("attempts", ["site", "url", "status", "error", "fetched"]),
                          ("jobs", ["site", "url", "kind", "source", "state", "error"])]:
        write_csv(f"{table}.csv", [dict(r) for r in db.execute(f"SELECT {','.join(fields)} FROM {table} WHERE run=? ORDER BY id", (run,))], fields)
    warnings = [dict(r) for r in db.execute("SELECT * FROM warnings WHERE run=? ORDER BY id", (run,))]
    (destination / "warnings.json").write_text(json.dumps(warnings, indent=2) + "\n")
    lines = [f"# Discovery assessment: run {run}", "", "Candidate URLs are unverified. Page types are inference. Exhausted known routes do not establish historical completeness.", ""]
    for summary in summaries:
        lines += [f"## {summary['name']} ({summary['site_id']})", "", f"Outcome: {summary['outcome']}; resolved address: {summary['resolved'] or 'unresolved'}",
                  f"Candidates: {summary['candidates']}; requests: {summary['requests']}; failed: {summary['failed']}; blocked: {summary['blocked']}; unfinished: {summary['unfinished']}",
                  f"Routes observed: {summary['routes'] or 'none'}", "", "Candidate samples (type inferred):"]
        for r in db.execute("SELECT url,type FROM candidates WHERE run=? AND site=? ORDER BY id LIMIT 5", (run, summary["site_id"])):
            lines.append(f"- {r['url']} — {r['type']}")
        dates = json.loads(summary["date_evidence"])
        if dates:
            for d in [dates[0], dates[-1]]:
                lines.append(f"- Source date evidence: {d['date']} for {d['url']} from {d['source']} (unverified)")
        lines += ["", "Observed warnings / suspected gaps:"]
        for w in warnings:
            if w["site"] == summary["site_id"]:
                lines.append(f"- {w['code']}: {w['url']} — {w['detail']}")
        lines.append("")
    (destination / "report.md").write_text("\n".join(lines), encoding="utf-8")
