"""Explicit discovery commands; reports never initiate network requests."""
import argparse
import asyncio
import fcntl
from pathlib import Path

from .discovery import Config, Engine, Store, export, read_seeds


def main():
    parser = argparse.ArgumentParser(description="Discovery-only search seed tooling")
    parser.add_argument("--db", type=Path, default=Path("data/discovery.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Create a snapshot and discover URLs (network)")
    start.add_argument("--seeds", type=Path, default=Path("data/sites.csv"))
    start.add_argument("--contact", required=True, help="Operator email or contact URL for the user agent")
    start.add_argument("--concurrency", type=int, default=4)
    start.add_argument("--interval", type=float, default=1.0)
    resume = commands.add_parser("resume", help="Resume the original run snapshot (network)")
    resume.add_argument("run", type=int)
    report = commands.add_parser("report", help="Export local observations without network access")
    report.add_argument("run", type=int)
    report.add_argument("--output", type=Path, default=Path("data/discovery-report"))
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(args.db) + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another command is using this discovery database")
        store = Store(args.db)
        try:
            if args.command == "report":
                if not store.db.execute("SELECT 1 FROM runs WHERE id=?", (args.run,)).fetchone():
                    parser.error("Unknown run")
                export(store, args.run, args.output)
                print(f"Report exported to {args.output.resolve()}")
            else:
                run = (store.start(read_seeds(args.seeds), Config(args.contact, args.concurrency, args.interval))
                       if args.command == "start" else args.run)
                print(f"Discovery run {run}; database: {args.db.resolve()}", flush=True)
                asyncio.run(Engine(store, run).run_queue())
                print(f"Known queue exhausted for run {run}; export and inspect warnings before assessing coverage.")
        except KeyboardInterrupt:
            print("Interrupted. Resume this run to recover unfinished queue jobs.")
        finally:
            store.db.close()


if __name__ == "__main__":
    main()
