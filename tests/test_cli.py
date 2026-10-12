from pathlib import Path

import pytest

from hypertext_search.__main__ import main, reset, select_site
from hypertext_search.urls import read_seeds


def test_site_urls_and_hostnames():
    seeds = read_seeds('data/sites.csv')
    for url in ('100r.co', 'https://100r.co/', 'https://www.100r.co/about'):
        assert select_site(url, seeds).site_id == 'site_1e4242025ce8'
    with pytest.raises(ValueError, match='not in'):
        select_site('unlisted.example', seeds)


def test_crawl_launches_selected_site(monkeypatch):
    monkeypatch.setattr('sys.argv', ['hypertext_search', 'crawl', '100r.co'])
    launched = []
    monkeypatch.setattr('os.execv', lambda executable, args: launched.append(args))
    main()
    assert launched[0][-4:] == ['-a', 'site=site_1e4242025ce8', '-s',
                               'JOBDIR=data/crawls/single-site_1e4242025ce8']


def test_reset_preserves_site_list(tmp_path):
    data = tmp_path / 'data'
    (data / 'crawls' / 'single-test').mkdir(parents=True)
    (data / 'crawls' / 'single-test' / 'results.sqlite3').write_text('results')
    for name in ('sites.csv', 'search.sqlite3', 'search.sqlite3-wal'):
        (data / name).write_text(name)
    reset(tmp_path)
    assert (data / 'sites.csv').exists()
    assert not (data / 'crawls').exists()
    assert not (data / 'search.sqlite3').exists()
    assert not (data / 'search.sqlite3-wal').exists()


def test_reset_cancellation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['hypertext_search', 'reset'])
    monkeypatch.setattr('builtins.input', lambda prompt: 'no')
    (tmp_path / 'data/crawls').mkdir(parents=True)
    main()
    assert (tmp_path / 'data/crawls').exists()
