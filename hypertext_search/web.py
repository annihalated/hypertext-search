"""Loopback-only search and single-site crawl workbench, using the standard library."""
import html
import json
import re
import signal
import sqlite3
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from .urls import read_seeds
from .formatting import excerpt, safe_html


class Workbench:
    def __init__(self, root=Path('.'), public=False):
        self.root = root.resolve()
        self.public = public
        self.seeds = read_seeds(self.root / 'data/sites.csv')
        self.process = None
        self.active_site = None
        self.log = None
        self.last_sync = 0
        self.stamps = {}
        if public:
            database = self.root / 'data/search.sqlite3'
            self.db = sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)
            return
        self.db = sqlite3.connect(self.root / 'data/search.sqlite3')
        self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(site UNINDEXED,url UNINDEXED,title,body,date UNINDEXED,completeness UNINDEXED)')
        self.db.execute('CREATE TABLE IF NOT EXISTS formatting(site TEXT,url TEXT,html TEXT,PRIMARY KEY(site,url))')
        self.db.execute('CREATE TABLE IF NOT EXISTS versions(site TEXT,url TEXT,hash TEXT,PRIMARY KEY(site,url))')

    def sync(self):
        if self.public:
            return
        if time.monotonic() - self.last_sync < 5:
            return
        for path in sorted((self.root / 'data/crawls').glob('*/results.sqlite3')):
            stamp = path.stat().st_mtime_ns
            if self.stamps.get(path) == stamp:
                continue
            source = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=1)
            try:
                if not source.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone():
                    continue
                for site, url, digest, raw in source.execute('SELECT site,url,hash,data FROM documents'):
                    old = self.db.execute('SELECT hash FROM versions WHERE site=? AND url=?', (site,url)).fetchone()
                    if old and old[0] == digest:
                        continue
                    doc = json.loads(raw)
                    if old:
                        self.db.execute('DELETE FROM search WHERE site=? AND url=?', (site,url))
                    self.db.execute('INSERT INTO search VALUES(?,?,?,?,?,?)', (site,url,doc.get('title',''),doc['body'],doc.get('date',''),doc.get('completeness','')))
                    self.db.execute('INSERT OR REPLACE INTO formatting VALUES(?,?,?)', (site,url,doc.get('body_html','')))
                    self.db.execute('INSERT OR REPLACE INTO versions VALUES(?,?,?)', (site,url,digest))
                self.db.commit()
                self.stamps[path] = stamp
            finally:
                source.close()
        self.last_sync = time.monotonic()

    def start(self, site):
        if site not in {s.site_id for s in self.seeds}:
            raise ValueError('Select a listed site.')
        if self.process and self.process.poll() is None:
            raise ValueError('A crawl is already running. Stop it before starting another.')
        job = self.root / 'data/crawls' / f'single-{site}'
        job.mkdir(parents=True, exist_ok=True)
        if self.log:
            self.log.close()
        self.log = (job / 'crawl.log').open('a')
        self.process = subprocess.Popen([sys.executable,'-m','scrapy','crawl','discovery','-a',f'site={site}','-s',f'JOBDIR={job}'], cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT)
        self.active_site = site

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)

    def log_update(self, offset=0, site=''):
        active = self.active_site or ''
        if site != active:
            offset = 0
        text = ''
        if active:
            path = self.root / 'data/crawls' / f'single-{active}' / 'crawl.log'
            if path.exists():
                with path.open('rb') as stream:
                    size = path.stat().st_size
                    if offset > size:
                        offset = 0
                    stream.seek(max(offset, size - 65536))
                    text = stream.read(65536).decode(errors='replace')
                    offset = stream.tell()
        return dict(site=active, offset=offset, text=text, status=self.status())

    def status(self):
        if not self.process:
            return 'No crawl started in this session.'
        code = self.process.poll()
        return f'{self.active_site}: ' + ('running (stop waits for a clean shutdown)' if code is None else f'finished, exit code {code}')


def serve(port=8000, public=False):
    work = Workbench(public=public)
    esc = lambda value: html.escape(str(value), quote=True)

    class Handler(BaseHTTPRequestHandler):
        def page(self, body, status=200):
            navigation = '' if public else '<a href="/crawl">Crawl a site</a><a href="/stats">Stats</a>'
            content = ('''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Hypertext Search</title><style>
body{font:16px system-ui;background:#f6f5f1;color:#242b29;max-width:1000px;margin:40px auto;padding:0 24px}nav{display:flex;gap:24px;margin-bottom:40px}a{color:#236657}h1{font:42px Georgia}input,select,button{font:inherit;padding:12px;border:1px solid #bbc5bf;border-radius:6px}input{width:45%}button{background:#236657;color:white;cursor:pointer}article{background:white;padding:22px;margin:18px 0;border:1px solid #ddd;border-radius:8px}mark{background:#fff2b3;color:inherit;border-radius:2px}p{line-height:1.6}.excerpt{line-height:1.6;overflow-wrap:anywhere}.excerpt h1,.excerpt h2,.excerpt h3{font:600 20px system-ui}.excerpt pre{font-size:14px}.excerpt blockquote{border-left:3px solid #bbc5bf;padding-left:16px;margin-left:0}small{color:#66736e}table{border-collapse:collapse;width:100%;background:white}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:16px}#crawl-log{font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;height:440px;overflow:auto}form{margin:20px 0}select{max-width:100%}</style></head><body><nav><b>Hypertext Search</b><a href="/">Search</a>''' + navigation + '</nav>' + body + '</body></html>').encode()
            self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def options(self, selected='', searchable=False):
            available = {row[0] for row in work.db.execute('SELECT DISTINCT site FROM search')} if searchable else None
            return ''.join(f'<option value="{esc(s.site_id)}" {"selected" if s.site_id == selected else ""}>{esc(s.name)}</option>' for s in work.seeds if available is None or s.site_id in available)

        def do_GET(self):
            path, _, query = self.path.partition('?')
            if public and path != '/':
                self.page('<h1>Page not found</h1>', 404)
                return
            args = parse_qs(query)
            q = args.get('q',[''])[0]
            site = args.get('site',[''])[0]
            if path == '/crawl/log':
                try:
                    offset = max(0, int(args.get('offset', ['0'])[0]))
                except ValueError:
                    offset = 0
                payload = json.dumps(work.log_update(offset, site)).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path == '/crawl':
                body = '<h1>Crawl one site</h1><p>Start or resume a dedicated crawl. Saved text becomes searchable as it arrives.</p>'
                body += f'<p id="crawl-status">{esc(work.status())}</p><form method="post" action="/start"><select name="site">{self.options(site or work.active_site)}</select> <button>Start / resume</button></form><form method="post" action="/stop"><button>Stop gracefully</button></form>'
                body += '''<h2>Live log</h2><small id="log-connection">Connecting…</small><pre id="crawl-log" aria-label="Live crawl log"></pre>
<script>
const log = document.getElementById('crawl-log');
const status = document.getElementById('crawl-status');
const connection = document.getElementById('log-connection');
let offset = 0, site = '';
async function updateLog() {
    try {
        const response = await fetch('/crawl/log?' + new URLSearchParams({offset, site}), {cache: 'no-store'});
        if (!response.ok) throw new Error('Log unavailable');
        const update = await response.json();
        const follow = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
        if (update.site !== site || update.offset < offset) log.textContent = '';
        site = update.site;
        offset = update.offset;
        if (update.text) log.textContent = (log.textContent + update.text).slice(-200000);
        if (follow) log.scrollTop = log.scrollHeight;
        status.textContent = update.status;
        connection.textContent = 'Live · updates every second';
    } catch (error) {
        connection.textContent = 'Connection interrupted · reconnecting…';
    } finally {
        setTimeout(updateLog, 1000);
    }
}
updateLog();
</script>'''
                self.page(body)
                return
            if path not in {'/','/stats'}:
                self.page('<h1>Page not found</h1>',404)
                return
            work.sync()
            if path == '/stats':
                counts = dict(work.db.execute('SELECT site,count(*) FROM search GROUP BY site'))
                total = sum(counts.values())
                body = f'<h1>Collection stats</h1><p><b>{total:,}</b> searchable documents across <b>{len(counts)}</b> sites.</p><p>{esc(work.status())}</p><p>Counts include existing saved data and new site crawls. Documents are deduplicated by site and URL. Search refreshes at most every five seconds.</p><table><tr><th>Site</th><th>Documents</th><th>Action</th></tr>'
                for seed in work.seeds:
                    body += f'<tr><td><a href="/?site={esc(seed.site_id)}">{esc(seed.name)}</a></td><td>{counts.get(seed.site_id,0):,}</td><td><a href="/crawl?site={esc(seed.site_id)}">Crawl</a></td></tr>'
                body += '</table><h2>Text completeness</h2>'
                for label,count in work.db.execute('SELECT completeness,count(*) FROM search GROUP BY completeness'):
                    body += f'<p>{esc(label)}: {count:,}</p>'
                self.page(body)
                return
            body = f'<h1>Search your reading web</h1><form><input name="q" placeholder="Search saved text" value="{esc(q)}"> <select name="site"><option value="">All sites</option>{self.options(site, searchable=True)}</select> <button>Search</button></form>'
            params = []
            clauses = []
            tokens = re.findall(r'\w+',q, re.UNICODE)
            if tokens:
                clauses.append('search MATCH ?')
                params.append(' AND '.join('"'+token+'"' for token in tokens))
            if site:
                clauses.append('site=?')
                params.append(site)
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            count = work.db.execute('SELECT count(*) FROM search'+where,params).fetchone()[0]
            rows = work.db.execute('SELECT site,url,title,body,date,completeness FROM search'+where+(' ORDER BY rank' if tokens else ' ORDER BY rowid DESC')+' LIMIT 50',params).fetchall()
            body += f'<p>{count:,} results · showing up to 50</p>'
            names = {s.site_id:s.name for s in work.seeds}
            for sid,url,title,text,date,complete in rows:
                start = 0
                if tokens:
                    found = text.lower().find(tokens[0].lower())
                    start = max(0,found-120)
                stored = work.db.execute('SELECT html FROM formatting WHERE site=? AND url=?', (sid,url)).fetchone()
                snippet = excerpt(stored[0], tokens[0] if tokens else '', url, matches=tokens) if stored and stored[0] else '<p>'+safe_html(esc(text[start:start+500]), matches=tokens)+'</p>'
                marked_title = safe_html(esc(title or url), matches=tokens)
                body += f'<article><small>{esc(names.get(sid,sid))} · {esc(date)} · {esc(complete)}</small><h2><a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{marked_title}</a></h2><div class="excerpt">{snippet}</div><small>{esc(url)}</small></article>'
            if not rows:
                body += '<p>No saved documents match. Try different words.</p>' if public else '<p>No saved documents match. Start a site crawl or try different words.</p>'
            self.page(body)

        def do_POST(self):
            if public:
                self.page('Not found', 404)
                return
            # Reject cross-origin browser requests to the local crawl controls.
            host = self.headers.get('Host','')
            origin = self.headers.get('Origin')
            if host != f'127.0.0.1:{port}' and host != f'localhost:{port}':
                self.page('Invalid host',403)
                return
            if origin and origin not in {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}:
                self.page('Invalid origin',403)
                return
            size = int(self.headers.get('Content-Length','0'))
            if size > 4096:
                self.page('Request too large',413)
                return
            args = parse_qs(self.rfile.read(size).decode())
            try:
                if self.path == '/start':
                    work.start(args.get('site',[''])[0])
                elif self.path == '/stop':
                    work.stop()
                else:
                    self.page('Not found',404)
                    return
            except ValueError as exc:
                self.page(f'<p>{esc(exc)}</p><a href="/crawl">Back to crawl controls</a>',409)
                return
            self.send_response(303)
            self.send_header('Location','/crawl')
            self.end_headers()

    server = HTTPServer(('0.0.0.0' if public else '127.0.0.1',port),Handler)
    print(f'Local search: http://127.0.0.1:{port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        work.stop()
        if work.process and work.process.poll() is None:
            work.process.wait()
        server.server_close()
        work.db.close()
        if work.log:
            work.log.close()
