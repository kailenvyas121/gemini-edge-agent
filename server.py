"""Dashboard server (standard library only).

    python server.py            # then open http://localhost:8000

GET /           the dashboard
GET /api/scan   runs one pass of the agent and returns its report as JSON (cached for 20 s)
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from edge import agent

ROOT = Path(__file__).parent
CACHE_S = 20
_lock = threading.Lock()
_cache: dict = {"t": 0.0, "body": b""}


def scan_json() -> bytes:
    with _lock:
        if time.time() - _cache["t"] > CACHE_S or not _cache["body"]:
            g = agent.Guardrails()
            try:
                rep = agent.run_once(g)
                body = {"ok": True, "guardrails": asdict(g), **asdict(rep)}
            except Exception as e:  # network hiccups show up in the UI instead of crashing it
                body = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            _cache.update(t=time.time(), body=json.dumps(body, default=str).encode())
        return _cache["body"]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/scan"):
            self._send(200, scan_json(), "application/json")
        elif self.path in ("/", "/index.html"):
            self._send(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print("Gemini Edge Agent dashboard → http://localhost:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
