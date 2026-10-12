import hashlib
import json
from hypertext_search.urls import normalize


class ObservationPipeline:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        return instance

    def process_item(self, item):
        self.crawler.discovery_audit.record(item)
        if item["event"] == "document":
            item["url"] = normalize(item["url"])
            db = self.crawler.discovery_audit.db
            digest = hashlib.sha256((item["body"] + item.get("body_html", "")).encode()).hexdigest()
            score = {"full": 4, "public_page": 3, "unknown": 2, "partial": 1}[item["completeness"]]
            db.execute("CREATE TABLE IF NOT EXISTS documents(site TEXT, url TEXT, hash TEXT, score INTEGER, data TEXT, PRIMARY KEY(site,url))")
            db.execute("INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(site,url) DO UPDATE SET hash=excluded.hash,score=excluded.score,data=excluded.data WHERE excluded.score>=documents.score",
                       (item["site"], item["url"], digest, score, json.dumps(item)))
            db.commit()
        return item
