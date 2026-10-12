"""Commands for crawling, searching, and managing local results."""
import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .report import export
from .urls import normalize, read_seeds


def select_site(value, seeds):
    url = normalize(value if '://' in value else f'https://{value}')
    if not url:
        raise ValueError('Enter a valid site URL or hostname.')
    parsed = urlsplit(url)
    matches = [seed for seed in seeds
               if seed.hostname.removeprefix('www.') == parsed.hostname.removeprefix('www.')
               and (seed.scope_path == '/' or parsed.path.rstrip('/') == seed.scope_path.rstrip('/')
                    or parsed.path.startswith(seed.scope_path.rstrip('/') + '/'))]
    if not matches:
        raise ValueError(f'{value} is not in data/sites.csv. Add the site there first.')
    longest = max(len(seed.scope_path) for seed in matches)
    matches = [seed for seed in matches if len(seed.scope_path) == longest]
    if len(matches) != 1:
        raise ValueError('Multiple listed sites match this URL; use the publication’s full URL.')
    return matches[0]


def reset(root):
    crawls = root / 'data/crawls'
    if crawls.exists():
        shutil.rmtree(crawls)
    for name in ('search.sqlite3', 'search.sqlite3-wal', 'search.sqlite3-shm', 'search.sqlite3-journal'):
        (root / 'data' / name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Crawl one publication and search its saved text locally.')
    commands = parser.add_subparsers(dest='command', required=True)
    crawl = commands.add_parser('crawl', help='Crawl or resume one listed site by URL or hostname')
    crawl.add_argument('site', help='Site URL or hostname, such as https://100r.co')
    crawl.add_argument('--jobdir', type=Path, help='Override the site’s saved crawl directory')
    commands.add_parser('reset', help='Delete all saved crawls and the search index, after confirmation')
    server = commands.add_parser('serve', help='Start the local search interface')
    server.add_argument('--port', type=int, default=8000)
    server.add_argument('--public', action='store_true', help='Serve a read-only search snapshot on all network interfaces')
    report = commands.add_parser('report', help='Export a saved discovery report')
    report.add_argument('--jobdir', type=Path, default=Path('data/crawls/text-1'))
    report.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'crawl':
        try:
            site = select_site(args.site, read_seeds('data/sites.csv'))
        except ValueError as exc:
            parser.error(str(exc))
        job = args.jobdir or Path('data/crawls') / f'single-{site.site_id}'
        print(f'Crawling {site.name} ({site.homepage_url}). Saved progress: {job}', flush=True)
        os.execv(sys.executable, [sys.executable, '-m', 'scrapy', 'crawl', 'discovery',
                                 '-a', f'site={site.site_id}', '-s', f'JOBDIR={job}'])
    elif args.command == 'reset':
        print('This permanently deletes all saved crawl results, queues, logs, and the search index.\nStop any running server and crawls first. The site list will be preserved.')
        try:
            answer = input('Type yes to delete: ')
        except (EOFError, KeyboardInterrupt):
            answer = ''
        if answer.strip().lower() != 'yes':
            print('Reset cancelled.')
            return
        reset(Path.cwd())
        print('Saved crawl data and search index cleared.')
    elif args.command == 'serve':
        from .web import serve
        serve(args.port, public=args.public)
    else:
        export(args.jobdir, args.output or args.jobdir / 'report')


if __name__ == '__main__':
    main()
