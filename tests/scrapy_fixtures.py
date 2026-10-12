"""Synthetic HTTP downloader for real-engine offline integration tests."""
import base64
import json
import socket
from pathlib import Path
from scrapy.http import HtmlResponse, Response, TextResponse, XmlResponse


class FixtureDownloadHandler:
    async def close(self):
        pass

    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.fixtures = json.loads(Path(crawler.settings["FIXTURE_PATH"]).read_text())
        instance.calls = Path(crawler.settings["FIXTURE_CALLS"])
        instance.counts = {}
        def forbidden(*args, **kwargs):
            raise AssertionError("Offline test attempted real network access")
        socket.getaddrinfo = socket.gethostbyname = socket.create_connection = forbidden
        return instance

    async def download_request(self, request):
        with self.calls.open("a") as stream:
            stream.write(request.url + "\n")
        entry = self.fixtures.get(request.url, {"status": 404, "body": ""})
        if isinstance(entry, list):
            index = self.counts.get(request.url, 0)
            self.counts[request.url] = index + 1
            entry = entry[min(index, len(entry) - 1)]
        if entry.get("error"):
            raise OSError(entry["error"])
        headers = entry.get("headers", {"Content-Type": "text/html"})
        typ = headers.get("Content-Type", "")
        cls = XmlResponse if "xml" in typ else HtmlResponse if "html" in typ else TextResponse if "text/" in typ else Response
        body = base64.b64decode(entry["base64"]) if "base64" in entry else entry.get("body", "").encode()
        return cls(request.url, status=entry.get("status", 200), headers=headers, body=body, request=request)
