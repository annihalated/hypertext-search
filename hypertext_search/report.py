import csv
import json
import sqlite3
from urllib.parse import urlsplit


def export(jobdir, destination):
    snapshot = json.loads((jobdir / "discovery-config.json").read_text())
    with sqlite3.connect(f"{(jobdir / 'results.sqlite3').resolve().as_uri()}?mode=ro", uri=True) as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT data FROM observations ORDER BY id")]
        db.row_factory = sqlite3.Row
        requests = [dict(row) for row in db.execute("SELECT * FROM requests")]
        documents = ([json.loads(row[0]) for row in db.execute("SELECT data FROM documents ORDER BY site,url")]
                     if db.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone() else [])
    finish = json.loads((jobdir / "finish.json").read_text()) if (jobdir / "finish.json").exists() else {"reason": "running_or_interrupted"}
    destination.mkdir(parents=True, exist_ok=True)

    def csv_file(name, rows, fields):
        with (destination / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    provenance = [e for e in events if e["event"] == "candidate"]
    unique = {(e["site"], e["url"]): e for e in provenance}
    csv_file("candidates.csv", unique.values(), ["site", "url", "type"])
    csv_file("provenance.csv", provenance, ["site", "url", "source", "route", "text", "date", "observed"])
    csv_file("documents.csv", documents, ["site", "url", "title", "body", "author", "date", "source_url", "source_kind", "content_field", "completeness", "observed"])
    csv_file("attempts.csv", [e for e in events if e["event"] in {"fetch", "attempt_error"}], ["site", "url", "status", "error", "hash", "kind", "redirects"])
    csv_file("requests.csv", requests, ["site", "url", "kind", "state"])
    warnings = [e for e in events if e["event"] in {"warning", "failure", "alias"}]
    (destination / "warnings.json").write_text(json.dumps(warnings, indent=2) + "\n")
    lines = ["# Crawl assessment", "", "Candidate URLs are unverified until fetched. Types, gaps, and RSS completeness are inference. Saved text is the publicly available payload. Known routes exhausted does not prove complete historical coverage.",
             f"Scrapy close reason: {finish['reason']}", ""]
    summaries = []
    for seed in snapshot["seeds"]:
        site = seed["site_id"]
        host = seed["hostname"].removeprefix("www.")
        belongs = lambda e: e.get("site") == site or (not e.get("site") and (urlsplit(e["url"]).hostname or "").removeprefix("www.") == host)
        own = [e for e in events if belongs(e)]
        candidates = [e for (s, _), e in unique.items() if s == site]
        unfinished = len([r for r in requests if r["site"] == site and r["state"] == "pending"])
        failed = len([e for e in own if e["event"] == "failure"])
        blocked = len([e for e in own if e["event"] == "failure" and "Forbidden by robots.txt" in e.get("error", "")])
        saved = [d for d in documents if d["site"] == site]
        notes = [e for e in warnings if belongs(e)]
        home = [e["url"] for e in own if e["event"] == "fetch" and e.get("kind") == "html" and e.get("status") == 200 and e.get("source") == "seed"]
        outcome = "unfinished" if unfinished or finish["reason"] != "finished" else "routes_exhausted_with_gaps" if notes else "known_routes_exhausted"
        home_failure = [e for e in own if e["event"] == "failure" and e.get("source") == "seed" and e.get("kind") == "html"]
        if outcome != "unfinished" and home_failure:
            outcome = "blocked" if "Forbidden by robots.txt" in home_failure[-1].get("error", "") else "failed"
        summary = dict(site_id=site, name=seed["name"], sources=seed["sources"], source_ids=seed["source_ids"],
                       resolved=home[0] if home else "", outcome=outcome, candidates=len(candidates),
                       requests=len([e for e in own if e["event"] in {"fetch", "attempt_error"}]), failed=failed,
                       blocked=blocked, documents=len(saved), feed_documents=len([d for d in saved if d["source_kind"] == "feed"]),
                       unfinished=unfinished, routes=",".join(sorted({e["route"] for e in candidates})))
        summaries.append(summary)
        lines += [f"## {seed['name']} ({site})", "", f"Outcome: {outcome}; resolved address: {summary['resolved'] or 'unresolved'}",
                  f"Candidates: {len(candidates)}; requests: {summary['requests']}; failures: {failed}; blocked: {blocked}; unfinished: {unfinished}",
                  f"Saved text documents: {len(saved)}; from feeds: {summary['feed_documents']}",
                  f"Routes: {summary['routes'] or 'none'}", "", "Samples (types inferred):"]
        lines += [f"- {e['url']} — {e['type']}" for e in candidates[:5]]
        dates = sorted([e for e in provenance if e["site"] == site and e.get("date")], key=lambda e: e["date"])
        for e in ([dates[0], dates[-1]] if dates else []):
            lines.append(f"- Source date evidence: {e['date']} for {e['url']} from {e['source']} (unverified)")
        lines += ["", "Warnings and gaps:"]
        lines += [f"- {e.get('code', e['event'])}: {e['url']} — {e.get('detail', e.get('error', e.get('source', '')))}" for e in notes]
        lines += [""]
    csv_file("seeds.csv", summaries, list(summaries[0]) if summaries else ["site_id"])
    (destination / "report.md").write_text("\n".join(lines) + "\n")
    print(f"Report exported to {destination.resolve()}")
