"""Build a consistent search snapshot for deployment, leaving crawls local."""
import sqlite3
from pathlib import Path

from hypertext_search.web import Workbench

work = Workbench()
try:
    work.sync()
    Path('.deploy').mkdir(exist_ok=True)
    with sqlite3.connect('.deploy/search.sqlite3') as destination:
        work.db.backup(destination)
    print('Search snapshot ready in .deploy/search.sqlite3')
finally:
    work.db.close()
