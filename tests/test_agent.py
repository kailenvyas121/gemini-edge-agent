"""Offline tests: pricing math and guardrail logic (no network)."""

from datetime import datetime, timedelta, timezone

import pytest

from edge import agent, pricer
from edge.gemini import Market


def test_fair_value_is_half_at_the_money_and_monotone():
    assert pricer.prob_at_or_above(100, 100, 0.001, 10) == pytest.approx(0.5, abs=1e-6)
    p = [pricer.prob_at_or_above(100, k, 0.001, 10) for k in (99, 99.5, 100, 100.5, 101)]
    assert all(a > b for a, b in zip(p, p[1:]))


def test_expired_contract_is_zero_or_one_and_unstarted_window_is_half():
    assert pricer.prob_at_or_above(101, 100, 0.001, 0) == 1.0
    assert pricer.prob_at_or_above(99, 100, 0.001, 0) == 0.0
    assert pricer.fair_value("up", 100, None, 0.001, 10) == 0.5


def test_quarter_kelly_is_zero_without_edge():
    assert agent._kelly_size(0.5, 0.6, 100) == 0
    assert agent._kelly_size(0.7, 0.5, 100) == pytest.approx(0.25 * 0.4 * 100)


def _market(bid, ask, minutes=30, strike=100.0):
    now = datetime.now(timezone.utc)
    return Market("S", "BTC test", "$100 or above", "BTC", "above", strike, None, now + timedelta(minutes=minutes),
                  bid, ask, None, "IDX", 0)


@pytest.fixture
def offline(monkeypatch):
    def install(markets, spot=110.0, chainlink=110.0):
        monkeypatch.setattr(agent.gemini, "fetch_markets", lambda: markets)
        monkeypatch.setattr(agent.gemini, "spot", lambda a: spot)
        monkeypatch.setattr(agent.gemini, "closes_1m", lambda a: [100 * (1 + 0.001 * (i % 2)) for i in range(60)])
        monkeypatch.setattr(agent.onchain, "chainlink_price",
                            lambda a: {"price": chainlink, "updated_at": datetime.now(timezone.utc).timestamp(),
                                       "round_id": 1, "feed": "0x0", "description": "BTC / USD"})
        monkeypatch.setattr(agent, "LEDGER", agent.Path("/tmp/gea_test_ledger.jsonl"))
    return install


def test_agent_buys_cheap_yes_within_caps(offline):
    offline([_market(0.40, 0.45)])  # spot 110 vs strike 100: fair ≈ 1
    rep = agent.run_once()
    d = rep.decisions[0]
    assert d.action == "buy_yes" and 0 < d.size_usd <= agent.Guardrails().max_per_market_usd


def test_oracle_divergence_blocks_trading(offline):
    offline([_market(0.40, 0.45)], spot=110.0, chainlink=100.0)  # ~950 bp apart
    rep = agent.run_once()
    assert "BTC" in rep.blocked and rep.decisions[0].action == "hold" and rep.halted


def test_wide_books_and_last_minutes_are_skipped(offline):
    offline([_market(0.10, 0.60), _market(0.40, 0.45, minutes=1)])
    rep = agent.run_once()
    assert [d.action for d in rep.decisions] == ["hold", "hold"]


def test_large_orders_need_human_approval(offline):
    offline([_market(0.40, 0.45)])
    g = agent.Guardrails(max_per_market_usd=50, approval_above_usd=5)
    assert agent.run_once(g, approve=lambda d: False).decisions[0].reason == "needs human approval"
    assert agent.run_once(g, approve=lambda d: True).decisions[0].action == "buy_yes"
