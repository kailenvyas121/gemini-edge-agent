"""End-to-end: browser → HTTP API → agent logic → rendered page, with the network mocked.

Starts the real server, calls the real API endpoint, and renders the real page in headless
Chrome (skipped if Chrome isn't installed), then checks the rendered DOM shows the agent's output.
"""

import json
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server
from edge import agent
from tests.test_integration import _market

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.fixture
def live_server(offline, monkeypatch):
    offline([_market(0.40, 0.45), _market(0.10, 0.60), _market(0.40, 0.45, minutes=1)])
    server._cache.update(t=0.0, body=b"")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_api_returns_full_report(live_server):
    d = json.loads(urllib.request.urlopen(f"{live_server}/api/scan").read())
    assert d["ok"] and len(d["decisions"]) == 3
    assert {x["reason"] for x in d["decisions"]} >= {"paper order", "book too wide or one-sided", "too close to expiry"}
    assert d["oracle"]["BTC"]["description"] == "BTC / USD" and d["guardrails"]["dry_run"] is True


def test_page_and_404(live_server):
    html = urllib.request.urlopen(f"{live_server}/").read().decode()
    assert "Gemini <span>Edge</span> Agent" in html and "/api/scan" in html
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{live_server}/nope")


def test_api_reports_errors_instead_of_crashing(live_server, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("network down")
    monkeypatch.setattr(agent, "run_once", boom)
    server._cache.update(t=0.0, body=b"")
    d = json.loads(urllib.request.urlopen(f"{live_server}/api/scan").read())
    assert d["ok"] is False and "network down" in d["error"]


@pytest.mark.skipif(not shutil.os.path.exists(CHROME), reason="Chrome not installed")
def test_browser_renders_agent_output(live_server):
    dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=8000",
                          "--dump-dom", f"{live_server}/"], capture_output=True, text=True, timeout=60).stdout
    assert "Markets priced" in dom and "buy yes" in dom          # KPI cards and the order table rendered
    assert "verified onchain" in dom                              # onchain check card rendered
    assert "book too wide or one-sided" in dom                    # skipped markets explained in the table
    assert "Budget per run" in dom                                # guardrails panel rendered
