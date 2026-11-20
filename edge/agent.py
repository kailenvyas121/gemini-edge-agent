"""The agent's decision loop: observe → verify onchain → price → decide → guard → act (paper) → log.

The agent never trades without passing every guardrail, and by default it only paper-trades:
it writes the orders it would send to a ledger. Live order routing would use Gemini's
authenticated trading endpoints and is deliberately not enabled here.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from edge import gemini, onchain, pricer

LEDGER = Path("output/ledger.jsonl")


@dataclass
class Guardrails:
    budget_usd: float = 100.0            # total premium the agent may commit per run
    max_per_market_usd: float = 10.0     # cap on any one contract
    min_edge: float = 0.04               # fair − price must beat this (covers fees and model error)
    max_spread: float = 0.15             # skip books wider than 15¢
    min_minutes_to_expiry: float = 2.0   # no trading in the final minutes (pick-off risk)
    max_minutes_to_expiry: float = 1440  # short-term volatility model is only trusted out to a day
    # Chainlink updates on a ~0.5–1% move or a heartbeat, so tens of bp of gap is normal.
    max_oracle_divergence_bp: float = 150  # Gemini spot vs Chainlink onchain; above this, that asset is blocked
    max_oracle_age_s: float = 86_400       # onchain price older than this → that asset is blocked
    approval_above_usd: float = 25.0     # orders above this need a human "yes"
    allowed_assets: tuple = ("BTC", "ETH", "SOL", "XRP")
    dry_run: bool = True                 # paper trading only


@dataclass
class Decision:
    symbol: str
    title: str
    label: str
    asset: str
    minutes_left: float
    strike: float | None
    spot: float
    fair: float
    bid: float | None
    ask: float | None
    action: str = "hold"     # buy_yes, buy_no, hold
    edge: float = 0.0
    size_usd: float = 0.0
    reason: str = ""


@dataclass
class RunReport:
    started_at: str
    halted: bool = False                 # every asset blocked
    blocked: dict = field(default_factory=dict)  # asset → reason
    oracle: dict = field(default_factory=dict)
    vol_annualized: dict = field(default_factory=dict)
    decisions: list = field(default_factory=list)
    committed_usd: float = 0.0


def _kelly_size(fair: float, price: float, bankroll: float) -> float:
    """Quarter-Kelly stake for a binary bought at `price` with win probability `fair`."""
    if price <= 0 or price >= 1:
        return 0.0
    f = (fair - price) / (1 - price)
    return max(0.0, 0.25 * f * bankroll)


def run_once(g: Guardrails | None = None, approve=None) -> RunReport:
    """One pass of the loop. `approve(decision) -> bool` is asked for orders above the approval limit."""
    g = g or Guardrails()
    t0 = gemini.now()
    rep = RunReport(started_at=t0.isoformat())
    markets = [m for m in gemini.fetch_markets() if m.asset in g.allowed_assets]

    # 1. Observe spot + volatility, and 2. verify each asset against Chainlink onchain
    state = {}
    for asset in sorted({m.asset for m in markets}):
        s = gemini.spot(asset)
        sig = pricer.ewma_vol_per_min(gemini.closes_1m(asset))
        rep.vol_annualized[asset] = round(sig * math.sqrt(pricer.MIN_PER_YEAR), 3)
        cl = onchain.chainlink_price(asset)
        if cl is not None:
            div_bp = 1e4 * abs(math.log(s / cl["price"]))
            age = t0.timestamp() - cl["updated_at"]
            rep.oracle[asset] = {"gemini_spot": s, "chainlink": cl["price"], "divergence_bp": round(div_bp, 1),
                                 "age_s": round(age), "feed": cl["feed"], "description": cl["description"]}
            if not cl["description"].startswith(asset):
                rep.blocked[asset] = f"onchain feed describes itself as {cl['description']!r}, not {asset}"
            elif cl["price"] <= 0:
                rep.blocked[asset] = "onchain price is not positive"
            elif div_bp > g.max_oracle_divergence_bp or age > g.max_oracle_age_s:
                rep.blocked[asset] = f"Gemini vs Chainlink {div_bp:.0f} bp apart, onchain price {age / 60:.0f} min old"
        else:
            rep.oracle[asset] = {"gemini_spot": s, "chainlink": None, "note": "no Chainlink mainnet feed; trades capped"}
        state[asset] = (s, sig, cl is not None)

    # 3. Price every contract, 4. decide, 5. guard
    budget = g.budget_usd
    for m in sorted(markets, key=lambda m: m.expiry):
        s, sig, verified = state[m.asset]
        minutes = (m.expiry - t0).total_seconds() / 60
        strike = m.strike if not (m.kind == "up" and m.start and m.start > t0) else None
        fair = pricer.fair_value(m.kind, s, strike, sig, minutes)
        d = Decision(m.symbol, m.title, m.label, m.asset, round(minutes, 1), strike, s, round(fair, 4), m.bid, m.ask)
        if m.asset in rep.blocked:
            d.reason = f"blocked: {rep.blocked[m.asset]}"
        elif minutes < g.min_minutes_to_expiry:
            d.reason = "too close to expiry"
        elif minutes > g.max_minutes_to_expiry:
            d.reason = "beyond model horizon (24h)"
        elif m.bid is None or m.ask is None or m.ask - m.bid > g.max_spread:
            d.reason = "book too wide or one-sided"
        else:
            buy_yes, buy_no = fair - m.ask, m.bid - fair  # buying NO at (1 − bid)
            side, edge, price = ("buy_yes", buy_yes, m.ask) if buy_yes >= buy_no else ("buy_no", buy_no, 1 - m.bid)
            win = fair if side == "buy_yes" else 1 - fair
            if edge < g.min_edge:
                d.reason = f"edge {edge:+.3f} below minimum"
            else:
                cap = g.max_per_market_usd if verified else g.max_per_market_usd / 2
                size = min(_kelly_size(win, price, g.budget_usd), cap, budget)
                if size < 1:
                    d.reason = "budget exhausted or stake too small"
                elif size > g.approval_above_usd and not (approve and approve(d)):
                    d.reason = "needs human approval"
                else:
                    d.action, d.edge, d.size_usd = side, round(edge, 4), round(size, 2)
                    d.reason = "paper order" if g.dry_run else "live order"
                    budget -= size
        rep.decisions.append(d)

    rep.halted = bool(state) and all(a in rep.blocked for a in state)
    rep.committed_usd = round(g.budget_usd - budget, 2)
    _log(rep, g)
    return rep


def _log(rep: RunReport, g: Guardrails) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        for d in rep.decisions:
            if d.action != "hold":
                f.write(json.dumps({"ts": rep.started_at, **asdict(d), "dry_run": g.dry_run}) + "\n")
