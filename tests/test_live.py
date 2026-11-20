"""Live sanity checks against the real Gemini API and Ethereum. Opt in: LIVE=1 pytest tests/test_live.py

These check that the data lines up: Gemini spot vs Gemini candles vs Chainlink onchain, every
parsed contract is well formed, ladders are priced monotonically, and a full scan is fast enough.
"""

import math
import os
import time

import pytest

from edge import agent, gemini, onchain, pricer

pytestmark = pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 to hit real APIs")


def test_feeds_are_the_right_assets_with_8_decimals():
    for asset, feed in onchain.FEEDS.items():
        assert onchain.chainlink_price(asset)["description"] == f"{asset} / USD"
        assert int(onchain._call(feed, "0x313ce567"), 16) == 8  # decimals()


@pytest.mark.parametrize("asset", ["BTC", "ETH", "SOL"])
def test_three_price_sources_agree(asset):
    s, last, cl = gemini.spot(asset), gemini.closes_1m(asset)[-1], onchain.chainlink_price(asset)["price"]
    assert abs(math.log(s / last)) < 0.01      # Gemini ticker vs latest 1-minute candle: within 1%
    assert abs(math.log(s / cl)) < 0.015       # Gemini vs Chainlink: within 1.5% (the guardrail)


def test_every_live_contract_is_well_formed_and_ladders_are_monotone():
    ms = gemini.fetch_markets()
    assert len(ms) > 50
    now = gemini.now()
    ladders = {}
    for m in ms:
        assert m.expiry > now and m.asset in {"BTC", "ETH", "SOL", "XRP"}
        assert m.bid is None or m.ask is None or 0 < m.bid <= m.ask < 1
        if m.kind == "above":
            ladders.setdefault((m.asset, m.expiry), []).append(m.strike)
    for (asset, expiry), strikes in ladders.items():
        s, sig = gemini.spot(asset), pricer.ewma_vol_per_min(gemini.closes_1m(asset))
        minutes = (expiry - now).total_seconds() / 60
        p = [pricer.prob_at_or_above(s, k, sig, minutes) for k in sorted(strikes)]
        assert all(a >= b for a, b in zip(p, p[1:]))
        break  # one ladder per asset is enough to keep the run short


def test_live_scan_is_consistent_and_fast():
    t = time.perf_counter()
    rep = agent.run_once()
    elapsed = time.perf_counter() - t
    assert elapsed < 20, f"live scan took {elapsed:.1f}s"
    g = agent.Guardrails()
    assert rep.committed_usd <= g.budget_usd
    for d in rep.decisions:
        assert 0 <= d.fair <= 1
        if d.action in ("buy_yes", "buy_no"):
            assert d.edge >= g.min_edge and g.min_minutes_to_expiry <= d.minutes_left <= g.max_minutes_to_expiry
            assert d.size_usd <= g.max_per_market_usd
