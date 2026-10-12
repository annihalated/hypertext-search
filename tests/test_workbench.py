import json
import sqlite3
from pathlib import Path

from hypertext_search.web import Workbench
from hypertext_search.urls import normalize


def test_action_links_rejected_but_pagination_kept():
    assert normalize('https://example.test/post/?like_comment=123&_wpnonce=x') is None
    assert normalize('https://example.test/post/?replytocom=123') is None
    assert normalize('https://example.test/?paged=2') == 'https://example.test/?paged=2'


def test_index_updates_and_deduplicates(tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/sites.csv').write_text(Path('data/sites.csv').read_text())
    job = tmp_path / 'data/crawls/test'
    job.mkdir(parents=True)
    source = sqlite3.connect(job / 'results.sqlite3')
    source.execute('CREATE TABLE documents(site,url,hash,data)')
    doc = dict(title='A title',body='unusual searchable phrase',date='',completeness='full')
    source.execute('INSERT INTO documents VALUES(?,?,?,?)', ('site','https://example.test/','v1',json.dumps(doc)))
    source.commit()
    work = Workbench(tmp_path)
    work.sync()
    assert work.db.execute("SELECT count(*) FROM search WHERE search MATCH 'unusual'").fetchone()[0] == 1
    doc['body'] = 'replacement text'
    source.execute('UPDATE documents SET hash=?,data=?', ('v2',json.dumps(doc)))
    source.commit()
    work.last_sync = 0
    work.sync()
    assert work.db.execute('SELECT count(*) FROM search').fetchone()[0] == 1
    assert work.db.execute("SELECT count(*) FROM search WHERE search MATCH 'replacement'").fetchone()[0] == 1
    work.db.close()
    source.close()


def test_live_log_reads_only_new_bytes_and_handles_site_changes(tmp_path):
    work = Workbench.__new__(Workbench)
    work.root = tmp_path
    work.active_site = 'first'
    work.process = None
    folder = tmp_path / 'data/crawls/single-first'
    folder.mkdir(parents=True)
    log = folder / 'crawl.log'
    log.write_text('200 https://example.test/one\n')
    first = work.log_update()
    assert first['text'] == log.read_text()
    with log.open('a') as stream:
        stream.write('200 https://example.test/two\n')
    second = work.log_update(first['offset'], first['site'])
    assert second['text'] == '200 https://example.test/two\n'
    assert work.log_update(second['offset'], second['site'])['text'] == ''
    work.active_site = 'second'
    folder = tmp_path / 'data/crawls/single-second'
    folder.mkdir()
    (folder / 'crawl.log').write_text('new crawl\n')
    assert work.log_update(second['offset'], second['site'])['text'] == 'new crawl\n'


def test_public_workbench_uses_read_only_snapshot(tmp_path):
    import pytest
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/sites.csv').write_text(Path('data/sites.csv').read_text())
    db = sqlite3.connect(tmp_path / 'data/search.sqlite3')
    db.execute('CREATE TABLE example(value)')
    db.close()
    work = Workbench(tmp_path, public=True)
    work.sync()
    with pytest.raises(sqlite3.OperationalError, match='readonly'):
        work.db.execute('INSERT INTO example VALUES(1)')
    work.db.close()


def test_public_routes_hide_local_controls(monkeypatch, tmp_path):
    import io
    import hypertext_search.web as web
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/sites.csv').write_text(Path('data/sites.csv').read_text())
    local = Workbench(tmp_path)
    local.db.commit()
    local.db.close()
    monkeypatch.chdir(tmp_path)
    responses = []

    class Server:
        def __init__(self, address, handler):
            assert address == ('0.0.0.0', 8080)
            self.handler = handler

        def serve_forever(self):
            for method, path in [('GET', '/'), ('GET', '/stats'), ('GET', '/crawl'), ('GET', '/crawl/log'), ('POST', '/start')]:
                request = self.handler.__new__(self.handler)
                request.path = path
                request.wfile = io.BytesIO()
                codes = []
                request.send_response = codes.append
                request.send_header = lambda *args: None
                request.end_headers = lambda: None
                getattr(request, 'do_' + method)()
                responses.append((path, codes[0], request.wfile.getvalue()))

        def server_close(self):
            pass

    monkeypatch.setattr(web, 'HTTPServer', Server)
    web.serve(8080, public=True)
    assert responses[0][1] == 200
    assert b'href="/crawl"' not in responses[0][2]
    assert b'href="/stats"' not in responses[0][2]
    assert all(code == 404 for _, code, _ in responses[1:])
