"""Text and metadata from public HTML and RSS/Atom payloads."""
import re
from scrapy import Selector
from .formatting import page_html

PARTIAL = re.compile(r"(?:continue reading|read (?:the )?(?:full|rest)|this post is for paid|subscribe to read|upgrade to (?:read|paid))", re.I)
EXCLUDED = " or ".join(f"ancestor::{tag}" for tag in ("script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"))


def plain_text(html):
    selector = Selector(text=html, type="html")
    return " ".join(" ".join(selector.xpath(f"//body//text()[not({EXCLUDED})]").getall()).split())


def page_document(response, site):
    sections = response.css("article") or response.css("main") or response.css("body")
    body = " ".join(" ".join(section.xpath(f".//text()[not({EXCLUDED})]").getall()) for section in sections)
    body = " ".join(body.split())
    if not body:
        return None
    title = (response.css('meta[property="og:title"]::attr(content)').get() or
             response.xpath("string(//h1[1])").get() or response.css("title::text").get() or "")
    return dict(event="document", site=site, url=response.url, title=title.strip(), body=body, body_html=page_html(response, title.strip(), response.css('meta[property="article:published_time"]::attr(content)').get() or ""),
                author=response.css('meta[name="author"]::attr(content)').get() or "",
                date=response.css('meta[property="article:published_time"]::attr(content)').get() or "",
                source_url=response.url, source_kind="page", content_field="html",
                completeness="partial" if PARTIAL.search(body) else "public_page")


def feed_content(entry):
    # remove_namespaces() makes RSS content:encoded available as encoded.
    for field in ("encoded", "content", "description", "summary"):
        node = entry.xpath(field)
        if not node:
            continue
        html = "".join(node[0].xpath("./node()").getall())
        body = plain_text(html)
        if body:
            full = field in {"encoded", "content"} and not PARTIAL.search(body)
            return body, field, "full" if full else "partial" if PARTIAL.search(body) or field == "summary" else "unknown"
    return "", "", "unknown"
