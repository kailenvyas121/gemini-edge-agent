"""Fair value of a crypto prediction contract.

A contract that pays $1 if the asset finishes at or above a strike is a digital option. With zero
drift over minutes, its fair value is P(S_T ≥ K) = 1 − F(ln(K/S) / (σ√τ)), where σ is a short-term
volatility forecast and F is a fat-tailed Student-t scaled to unit variance (crypto returns are far
from normal). For an up/down window that has not started, the strike is the price at the start,
so fair value is about 50%.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

NU = 4.5  # Student-t degrees of freedom; crypto 5–15 minute returns have excess kurtosis near 10
MIN_PER_YEAR = 525_600


def ewma_vol_per_min(closes: list[float], halflife: float = 30.0) -> float:
    """Exponentially weighted volatility of 1-minute log returns (per minute)."""
    r = np.diff(np.log(np.asarray(closes)))
    if len(r) < 10:
        raise ValueError("need at least 10 one-minute returns")
    w = 0.5 ** (np.arange(len(r))[::-1] / halflife)
    return float(math.sqrt(np.sum(w * r**2) / np.sum(w)))


def prob_at_or_above(spot: float, strike: float, sigma_min: float, minutes: float, nu: float = NU) -> float:
    if minutes <= 0:
        return 1.0 if spot >= strike else 0.0
    sd = sigma_min * math.sqrt(minutes)
    z = math.log(strike / spot) / sd
    return float(stats.t.sf(z / math.sqrt((nu - 2) / nu), nu))


def fair_value(kind: str, spot: float, strike: float | None, sigma_min: float, minutes: float) -> float:
    if kind == "up" and strike is None:
        return 0.5  # window not started: strike will be the price at the start
    return prob_at_or_above(spot, strike, sigma_min, minutes)
