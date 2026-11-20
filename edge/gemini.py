"""Gemini public APIs: prediction-market events and spot candles. No API key needed for market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

BASE = "https://api.gemini.com"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "gemini-edge-agent/0.1"


def _get(path: str, params: dict | None = None):
    r = SESSION.get(f"{BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@dataclass
class Market:
    """One tradable YES contract on Gemini, normalized for pricing."""

    symbol: str          # e.g. GEMI-BTC05M2611201710-UP
    title: str           # event title, e.g. "BTC price today at 12:10pm EST"
    label: str           # contract label, e.g. "Up" or "$59,000 or above"
    asset: str           # BTC, ETH, SOL, XRP
    kind: str            # "up" (≥ reference at window start) or "above" (≥ fixed strike)
    strike: float | None  # None for an up/down window that hasn't started (reference not set yet)
    start: datetime | None
    expiry: datetime
    bid: float | None
    ask: float | None
    last: float | None
    source: str          # settlement index named in the contract
    volume: float


def _asset(event: dict) -> str | None:
    series = (event.get("series") or event.get("ticker") or "")[:3].upper()
    return series if series in {"BTC", "ETH", "SOL", "XRP"} else None


def parse_events(events: list[dict]) -> list[Market]:
    """Normalize Gemini events into tradable contracts with a parsable strike rule."""
    out = []
    for e in events:
        asset = _asset(e)
        if asset is None or e.get("status") != "active":
            continue
        for c in e.get("contracts", []):
            if c.get("marketState") != "open":
                continue
            strike = c.get("strike") or {}
            if strike.get("type") == "reference":
                kind = "up"
            elif strike.get("type") == "above":
                kind = "above"
            else:
                continue  # ranges and other rules are skipped rather than mispriced
            p = c.get("prices") or {}
            out.append(Market(
                symbol=c.get("instrumentSymbol", ""), title=e.get("title", ""), label=c.get("label", ""),
                asset=asset, kind=kind, strike=_f(strike.get("value")),
                start=_ts(c.get("effectiveDate") if kind == "up" else None),
                expiry=_ts(c.get("expiryDate") or e.get("expiryDate")),
                bid=_f(p.get("bestBid")), ask=_f(p.get("bestAsk")), last=_f(p.get("lastTradePrice")),
                source=c.get("source", ""), volume=_f(e.get("volume")) or 0.0))
    return out


def fetch_markets(category: str = "Crypto") -> list[Market]:
    """Every active crypto contract with a parsable strike rule (up/down windows and price ladders)."""
    out, offset = [], 0
    while True:
        d = _get("/v1/prediction-markets/events", {"category": category, "limit": 100, "offset": offset})
        events = d.get("data", [])
        out += parse_events(events)
        offset += len(events)
        if not events or offset >= d.get("pagination", {}).get("total", 0):
            break
    return out


def spot(asset: str) -> float:
    t = _get(f"/v1/pubticker/{asset.lower()}usd")
    return (float(t["bid"]) + float(t["ask"])) / 2


def closes_1m(asset: str) -> list[float]:
    """Recent 1-minute closes, oldest first (Gemini returns newest first)."""
    rows = _get(f"/v2/candles/{asset.lower()}usd/1m")
    return [float(r[4]) for r in reversed(rows)]


def now() -> datetime:
    return datetime.now(timezone.utc)
