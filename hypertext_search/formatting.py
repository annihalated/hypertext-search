"""Safe structural HTML for stored text and search excerpts."""
import html
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from scrapy import Selector

ALLOWED = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
           'blockquote', 'pre', 'code', 'strong', 'b', 'em', 'i', 'br', 'a', 'hr'}
EXCLUDED = {'script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'noscript', 'svg', 'iframe', 'object'}


def safe_html(raw, base='', limit=None, matches=()):
    root = Selector(text=raw, type='html').root
    remaining = [limit]
    words = sorted(set(matches), key=len, reverse=True)
    pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(word) for word in words) + r')(?!\w)', re.I) if words else None

    def text(value):
        value = value or ''
        if remaining[0] is not None:
            value = value[:max(0, remaining[0])]
            remaining[0] -= len(value)
        if not pattern:
            return html.escape(value)
        parts = []
        end = 0
        for match in pattern.finditer(value):
            parts.extend((html.escape(value[end:match.start()]), '<mark>' + html.escape(match.group()) + '</mark>'))
            end = match.end()
        parts.append(html.escape(value[end:]))
        return ''.join(parts)

    def render(node):
        tag = node.tag if isinstance(node.tag, str) else ''
        if tag in EXCLUDED:
            return ''
        content = text(node.text)
        for child in node:
            content += render(child) + text(child.tail)
        if tag not in ALLOWED:
            return content
        attrs = ''
        if tag == 'a':
            href = urljoin(base, node.get('href', ''))
            if urlsplit(href).scheme in {'http', 'https'}:
                attrs = f' href="{html.escape(href, quote=True)}" rel="noopener noreferrer"'
        if tag in {'br', 'hr'}:
            return f'<{tag}>' if remaining[0] is None or remaining[0] > 0 else ''
        return f'<{tag}{attrs}>{content}</{tag}>' if content else ''

    return render(root)


def clean_page_html(raw, title='', date=''):
    selector = Selector(text=raw, type='html')
    root = selector.xpath('//body')[0].root
    normalized = lambda value: ' '.join(value.split()).casefold()
    # The article heading marks the end of site chrome and article metadata.
    for heading in root.xpath('./h1 | ./h2'):
        if title and normalized(''.join(heading.itertext())) == normalized(title):
            while len(root) and root[0] is not heading:
                root.remove(root[0])
            root.remove(heading)
            break
    if date and len(root):
        try:
            published = datetime.fromisoformat(date.replace('Z', '+00:00'))
            labels = {normalized(published.strftime(fmt)) for fmt in
                      ('%B %d, %Y', '%B %-d, %Y', '%b %d, %Y', '%Y-%m-%d')}
            first = root[0]
            if first.tag in {'p', 'time'} and normalized(''.join(first.itertext())) in labels:
                root.remove(first)
        except ValueError:
            pass
    return safe_html(''.join(Selector(root=node).get() for node in root))


def page_html(response, title='', date=''):
    sections = response.css('article') or response.css('main') or response.css('body')
    raw = safe_html(''.join(section.get() for section in sections), response.url)
    return clean_page_html(raw, title, date)


def feed_html(entry, field, base):
    node = entry.xpath(field)
    return safe_html(''.join(node[0].xpath('./node()').getall()), base) if node else ''


def excerpt(raw, query='', base='', limit=700, matches=()):
    # Keep complete block structure around the first matching paragraph or list.
    selector = Selector(text=raw, type='html')
    blocks = selector.xpath('//body/*')
    start = 0
    if query:
        for index, block in enumerate(blocks):
            if query.lower() in block.xpath('string(.)').get().lower():
                start = max(0, index - 1)
                break
    return safe_html(''.join(block.get() for block in blocks[start:]), base, limit, matches)
