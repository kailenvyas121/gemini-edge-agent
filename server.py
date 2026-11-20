"""Dashboard server (standard library only).

    python server.py            # then open http://localhost:8000

GET  /                       the dashboard
GET  /api/scan?budget=&min_edge=&cap=   runs one pass of the agent with these limits (cached 10 s)
GET  /api/history?symbol=    Gemini mid vs agent fair value for one contract, one point per scan
POST /api/approve?symbol=    a human approves an order that is above the approval limit
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from edge import agent

ROOT = Path(__file__).parent
CACHE_S = 10
_lock = threading.Lock()
_cache: dict = {"t": 0.0, "key": None, "body": b""}
_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=240))
_approved: set[str] = set()


def _guardrails(q: dict) -> agent.Guardrails:
    g = agent.Guardrails()

    def num(name, lo, hi):
        try:
            return min(hi, max(lo, float(q[name][0])))
        except (KeyError, ValueError, IndexError):
            return None

    if (v := num("budget", 10, 5000)) is not None:
        g.budget_usd = v
    if (v := num("min_edge", 0.01, 0.30)) is not None:
        g.min_edge = v
    if (v := num("cap", 1, 500)) is not None:
        g.max_per_market_usd = v
    return g


def scan_json(q: dict | None = None) -> bytes:
    q = q or {}
    g = _guardrails(q)
    key = (g.budget_usd, g.min_edge, g.max_per_market_usd, frozenset(_approved))
    with _lock:
        if time.time() - _cache["t"] > CACHE_S or _cache["key"] != key or not _cache["body"]:
            try:
                rep = agent.run_once(g, approve=lambda d: d.symbol in _approved)
                for d in rep.decisions:
                    if d.bid is not None and d.ask is not None:
                        _history[d.symbol].append({"t": rep.started_at, "mid": (d.bid + d.ask) / 2, "fair": d.fair,
                                                   "bid": d.bid, "ask": d.ask})
                body = {"ok": True, "guardrails": asdict(g), "approved": sorted(_approved), **asdict(rep)}
            except Exception as e:  # network hiccups show up in the UI instead of crashing it
                body = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            _cache.update(t=time.time(), key=key, body=json.dumps(body, default=str).encode())
        return _cache["body"]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/scan":
            self._send(200, scan_json(q), "application/json")
        elif u.path == "/api/history":
            sym = q.get("symbol", [""])[0]
            self._send(200, json.dumps({"symbol": sym, "points": list(_history.get(sym, []))}).encode(), "application/json")
        elif u.path in ("/", "/index.html"):
            self._send(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/api/approve":
            sym = parse_qs(u.query).get("symbol", [""])[0]
            if sym:
                _approved.add(sym)
            self._send(200, json.dumps({"approved": sorted(_approved)}).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print("Gemini Edge Agent dashboard → http://localhost:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
