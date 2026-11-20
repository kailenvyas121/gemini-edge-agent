"""Performance budgets (network mocked, so these measure our code, not Gemini's servers)."""

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import server
from edge import pricer
from tests.test_integration import _market


def test_pricing_300_contracts_under_50ms():
    t = time.perf_counter()
    for k in range(300):
        pricer.prob_at_or_above(100, 95 + k / 30, 0.001, 30)
    assert time.perf_counter() - t < 0.05


def test_full_decision_loop_under_200ms(offline):
    from edge import agent
    offline([_market(0.40, 0.45, strike=100 - i * 0.01) for i in range(300)])
    t = time.perf_counter()
    agent.run_once()
    assert time.perf_counter() - t < 0.2


def test_cached_api_response_under_50ms(offline):
    offline([_market(0.40, 0.45)])
    server._cache.update(t=0.0, body=b"")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/api/scan"
    urllib.request.urlopen(url).read()  # warm the cache
    t = time.perf_counter()
    json.loads(urllib.request.urlopen(url).read())
    assert time.perf_counter() - t < 0.05
    httpd.shutdown()
