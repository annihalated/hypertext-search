"""Recover document formatting from locally saved responses; no network requests."""
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from scrapy.utils._compression import _inflate, _unbrotli, _unzstd
from scrapy import Selector
from scrapy.http import HtmlResponse

from .formatting import feed_html, page_html
from .text import feed_content


def rebuild(root=Path('.')):
    recovered = missing = 0
    for database in sorted((root / 'data/crawls').glob('*/results.sqlite3')):
        db = sqlite3.connect(database, timeout=30)
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone():
                continue
            fetches = {}
            for url, raw in db.execute("SELECT url,data FROM observations WHERE event='fetch'"):
                event = json.loads(raw)
                if event.get('status') == 200:
                    fetches[url] = event
            feed_cache = {}
            for site, url, raw in db.execute('SELECT site,url,data FROM documents').fetchall():
                doc = json.loads(raw)
                source = doc.get('source_url', url)
                event = fetches.get(source, {})
                digest = event.get('hash')
                path = database.parent / 'responses' / (digest or 'missing')
                if not digest or not path.is_file():
                    missing += 1
                    continue
                payload = path.read_bytes()
                headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
                for coding in reversed(headers.get('content-encoding', '').split(',')):
                    decoder = {'gzip': gzip.decompress, 'br': _unbrotli, 'deflate': _inflate, 'zstd': _unzstd}.get(coding.strip())
                    if decoder:
                        payload = decoder(payload)
                formatted = ''
                if doc.get('source_kind') == 'feed':
                    if source not in feed_cache:
                        xml = Selector(body=payload, type='xml')
                        xml.remove_namespaces()
                        feed_cache[source] = {}
                        for entry in xml.xpath('//item | //entry'):
                            body, field, _ = feed_content(entry)
                            feed_cache[source][body] = feed_html(entry, field, source)
                    formatted = feed_cache[source].get(doc['body'], '')
                else:
                    response = HtmlResponse(source, body=payload)
                    formatted = page_html(response, doc.get('title', ''), doc.get('date', ''))
                if not formatted:
                    missing += 1
                    continue
                doc['body_html'] = formatted
                digest = hashlib.sha256((doc['body'] + formatted).encode()).hexdigest()
                db.execute('UPDATE documents SET hash=?,data=? WHERE site=? AND url=?',
                           (digest, json.dumps(doc), site, url))
                recovered += 1
            db.commit()
            print(f'{database.parent.name}: formatting rebuilt', flush=True)
        finally:
            db.close()
    print(f'Recovered formatting for {recovered:,} documents; {missing:,} lacked a usable saved response.')
