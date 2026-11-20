"""Unit tests: one function at a time, real API payloads as fixtures (no network)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from edge import gemini, onchain, pricer

FIX = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- pricer

def test_closed_form_matches_monte_carlo():
    rng = np.random.default_rng(0)
    nu, sig, minutes, spot, strike = pricer.NU, 0.0008, 30, 100.0, 100.6
    z = rng.standard_t(nu, 1_000_000) * math.sqrt((nu - 2) / nu)  # unit-variance Student-t
    mc = np.mean(spot * np.exp(sig * math.sqrt(minutes) * z) >= strike)
    assert pricer.prob_at_or_above(spot, strike, sig, minutes) == pytest.approx(mc, abs=0.002)


def test_at_the_money_is_half_and_ladder_is_monotone():
    assert pricer.prob_at_or_above(100, 100, 0.001, 10) == pytest.approx(0.5, abs=1e-9)
    p = [pricer.prob_at_or_above(100, k, 0.001, 10) for k in np.linspace(98, 102, 41)]
    assert all(a > b for a, b in zip(p, p[1:]))


def test_expiry_limits_and_unstarted_window():
    assert pricer.prob_at_or_above(101, 100, 0.001, 0) == 1.0
    assert pricer.prob_at_or_above(99, 100, 0.001, 0) == 0.0
    assert pricer.fair_value("up", 100, None, 0.001, 10) == 0.5


def test_more_volatility_pulls_far_strikes_toward_half():
    lo, hi = (pricer.prob_at_or_above(100, 102, s, 30) for s in (0.0005, 0.002))
    assert lo < hi < 0.5


def test_vol_uses_five_minute_returns_which_damp_bid_ask_bounce():
    # Prices bouncing ±0.05% every minute with no trend: a 1-minute estimator reads the bounce as
    # volatility; sampling every 5 minutes cuts the per-minute estimate by √5.
    bounce = [100 * (1 + 0.0005 * (-1) ** i) for i in range(61)]
    five = pricer.ewma_vol_per_min(bounce, halflife=1e9)
    one = pricer.ewma_vol_per_min(bounce, halflife=1e9, step=1)
    assert five / one == pytest.approx(1 / math.sqrt(5), rel=0.02)
    with pytest.raises(ValueError):
        pricer.ewma_vol_per_min([100.0] * 20)


def test_vol_recovers_known_volatility():
    rng = np.random.default_rng(1)
    sig = 0.001
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, sig, 1440))))
    assert pricer.ewma_vol_per_min(closes, halflife=1e9) == pytest.approx(sig, rel=0.1)


# ---------------------------------------------------------------- gemini parsing (real payload)

def test_parse_real_events_page():
    events = json.loads((FIX / "events_page.json").read_text())["data"]
    ms = gemini.parse_events(events)
    assert ms, "fixture should contain open crypto contracts"
    for m in ms:
        assert m.asset in {"BTC", "ETH", "SOL", "XRP"}
        assert m.kind in {"up", "above"}
        assert m.expiry is not None and m.symbol.startswith("GEMI-")
        for px in (m.bid, m.ask):
            assert px is None or 0 < px < 1
        if m.bid is not None and m.ask is not None:
            assert m.bid <= m.ask
        if m.kind == "above":
            assert m.strike and m.strike > 0
    # the label and the strike agree on every ladder contract ("$59,000 or above" ↔ 59000)
    for m in ms:
        if m.kind == "above":
            assert float(m.label.split(" ")[0].replace("$", "").replace(",", "")) == pytest.approx(m.strike)


def test_buy_no_price_is_one_minus_best_bid_in_real_payload():
    events = json.loads((FIX / "events_page.json").read_text())["data"]
    n = 0
    for e in events:
        for c in e["contracts"]:
            p = c["prices"]
            if p.get("bestBid") and p.get("buy", {}).get("no"):
                n += 1
                assert float(p["buy"]["no"]) == pytest.approx(1 - float(p["bestBid"]), abs=0.005)
    assert n > 0


def test_asset_from_series_and_unknown_assets_skipped():
    assert gemini._asset({"series": "BTC05M"}) == "BTC"
    assert gemini._asset({"series": "DOGE05M"}) is None
    assert gemini.parse_events([{"series": "BTC05M", "status": "settled", "contracts": [{}]}]) == []


# ---------------------------------------------------------------- onchain decoding (real eth_call results)

def test_decode_real_chainlink_round():
    res = json.loads((FIX / "btc_latest_round.json").read_text())["result"]
    d = onchain.decode_latest_round(res)
    assert 1_000 < d["price"] < 1_000_000 and d["updated_at"] > 1_700_000_000 and d["round_id"] > 0


def test_decode_real_description():
    res = json.loads((FIX / "btc_description.json").read_text())["result"]
    assert onchain._decode_string(res) == "BTC / USD"


def test_no_feed_returns_none():
    assert onchain.chainlink_price("XRP") is None
