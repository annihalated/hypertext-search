from hypertext_search.formatting import safe_html, excerpt
from hypertext_search.text import page_document
from scrapy.http import HtmlResponse


def test_safe_structure_and_links():
    result = safe_html('<p>Hello <em>world</em></p><ul><li>One</li></ul><script>alert(1)</script><a href="javascript:evil()" onclick="bad()">link</a>')
    assert '<em>world</em>' in result
    assert '<ul><li>One</li></ul>' in result
    assert 'script' not in result and 'onclick' not in result and 'javascript' not in result


def test_excerpt_matching_and_balanced_markup():
    raw = '<p>'+'x'*900+'</p><p>Before</p><ul><li><strong>needle</strong> found</li></ul>'
    result = excerpt(raw, 'needle', limit=30)
    assert '<strong>needle</strong>' in result
    assert result.endswith('</ul>')


def test_page_keeps_paragraphs_without_navigation():
    response = HtmlResponse('https://example.test', body=b'<main><nav>menu</nav><h1>Title</h1><p>A <b>bold</b> idea.</p><p>Next.</p></main>')
    doc = page_document(response, 'site')
    assert '<p>A <b>bold</b> idea.</p><p>Next.</p>' in doc['body_html']
    assert 'menu' not in doc['body_html']


def test_rebuild_compressed_saved_feed(tmp_path):
    import gzip
    import hashlib
    import json
    import sqlite3
    from hypertext_search.reformat import rebuild

    job = tmp_path / 'data/crawls/test'
    (job / 'responses').mkdir(parents=True)
    payload = gzip.compress(b'<rss><channel><item><description><![CDATA[<p>Hello <em>world</em></p>]]></description></item></channel></rss>')
    digest = hashlib.sha256(payload).hexdigest()
    (job / 'responses' / digest).write_bytes(payload)
    db = sqlite3.connect(job / 'results.sqlite3')
    db.execute('CREATE TABLE observations(event,url,data)')
    event = dict(status=200, hash=digest, headers={'Content-Encoding': 'gzip'})
    db.execute('INSERT INTO observations VALUES(?,?,?)', ('fetch', 'https://example.test/feed', json.dumps(event)))
    db.execute('CREATE TABLE documents(site,url,hash,data)')
    doc = dict(body='Hello world', source_url='https://example.test/feed', source_kind='feed')
    db.execute('INSERT INTO documents VALUES(?,?,?,?)', ('site', 'https://example.test/post', 'old', json.dumps(doc)))
    db.commit()
    rebuild(tmp_path)
    updated = json.loads(db.execute('SELECT data FROM documents').fetchone()[0])
    assert updated['body_html'] == '<p>Hello <em>world</em></p>'
    db.close()


def test_highlights_words_without_changing_markup_or_attributes():
    result = safe_html('<p>Power <em>POWER</em> powerful &amp; steel.</p><a href="https://power.test">steel</a>', matches=['power', 'steel'])
    assert '<mark>Power</mark> <em><mark>POWER</mark></em> powerful &amp; <mark>steel</mark>' in result
    assert 'href="https://power.test"' in result
    assert '<mark>steel</mark></a>' in result
    assert safe_html('&lt;script&gt;', matches=['script']) == '&lt;<mark>script</mark>&gt;'


def test_article_heading_and_metadata_are_not_repeated():
    from hypertext_search.formatting import clean_page_html
    raw = '<h1>Site name</h1><ul><li>menu</li></ul><a href="https://example.test/">&lt;-- home</a><h1>Abuse of power</h1><p>February 17, 2016</p><p>Article <em>content</em>.</p><h2>Another heading</h2>'
    result = clean_page_html(raw, 'Abuse of power', '2016-02-17T00:00:00+00:00')
    assert result == '<p>Article <em>content</em>.</p><h2>Another heading</h2>'
