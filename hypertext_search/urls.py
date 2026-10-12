"""Publication scope rules; crawling is owned by Scrapy."""
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

DOWNLOADS = re.compile(r"\.(?:pdf|zip|jpe?g|png|gif|svg|webp|mp3|mp4|css|js|woff2?|exe|dmg)$", re.I)
LISTING = re.compile(r"/(?:archives?|categories?|tags?|topics?|page)(?:/|$)", re.I)
NAVIGATION = re.compile(r"\b(?:archives?|older|newer|next|previous|all posts)\b", re.I)


def normalize(url, base=""):
    try:
        p = urlsplit(urljoin(base, url.strip()))
        if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        if re.search(r"%(?:2f|5c|2e|25)|\\", p.path, re.I):
            return None
        if any(k.lower() in {"like_comment", "_wpnonce", "replytocom", "share", "action"}
               for k, _ in parse_qsl(p.query)):
            return None
        host = p.hostname.encode("idna").decode().lower()
        if ":" in host:
            host = f"[{host}]"
        if p.port and p.port != (443 if p.scheme.lower() == "https" else 80):
            host += f":{p.port}"
        segments = []
        for part in (p.path or "/").split("/"):
            if part == "..":
                if segments:
                    segments.pop()
            elif part != ".":
                segments.append(part)
        pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}]
        return urlunsplit((p.scheme.lower(), host, "/".join(segments) or "/", urlencode(pairs), ""))
    except (ValueError, UnicodeError):
        return None


@dataclass(frozen=True)
class Seed:
    site_id: str
    name: str
    homepage_url: str
    hostname: str
    scope_path: str
    sources: str
    source_ids: str

    def allows(self, url, aliases=()):
        url = normalize(url)
        if not url:
            return False
        p = urlsplit(url)
        scope = self.scope_path.rstrip("/")
        return p.hostname in {self.hostname, *aliases} and (not scope or p.path == scope or p.path.startswith(scope + "/"))


    def allows_resource(self, url, kind, aliases=()):
        url = normalize(url)
        if not url:
            return False
        p = urlsplit(url)
        if p.hostname not in {self.hostname, *aliases}:
            return False
        if self.allows(url, aliases):
            return True
        return (kind == "robots" and p.path == "/robots.txt" or
                kind == "sitemap" and p.path in {"/sitemap.xml", "/sitemap_index.xml"})


def read_seeds(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        seeds = [Seed(**{k: row[k] for k in Seed.__dataclass_fields__}) for row in csv.DictReader(f)]
    if len({s.site_id for s in seeds}) != len(seeds):
        raise ValueError("Duplicate seed IDs")
    for seed in seeds:
        if not seed.scope_path.startswith("/") or not seed.allows(seed.homepage_url):
            raise ValueError(f"Invalid seed scope: {seed.site_id}")
    return seeds
