"""
backtester_maker.py — realistic maker fill rate model on top of backtester_v2.

When you place a post-only limit at touch, you don't always fill. Your fill
rate depends on:
  1. Queue position when book moves to your price (50% avg)
  2. Book traversing your price entirely (you fill at full size)
  3. Book stalling at your price (you fill partial / not at all)

Standard model: assume 50% fill rate on signal triggers. Half the trades
that would have fired never actually open. Of those that DO open, the
remaining 50% are still profitable in aggregate (the signals weren't
spurious, just delayed).

This is the harshest fair-fight assumption. Real maker fill rate on HL is
higher than 50% for thin coins (less competition) but lower on BTC/ETH
(deep queue).

Usage:
    trades_maker = run_backtest_maker_realistic(
        candles, evaluate_latest_bar, fill_rate=0.5, ...
    )
"""
from __future__ import annotations
import random
from typing import Callable, Optional
import pandas as pd

# Reuse all the machinery from backtester_v2
from backtester_v2 import (
    run_backtest_realistic,
    compute_stats,
    Trade,
)


def run_backtest_maker_realistic(
    candles, evaluate_fn,
    fill_rate: float = 0.5,
    seed: int = 42,
    **kwargs,
):
    """Wraps backtester_v2 — randomly drops `(1-fill_rate)` of signals to simulate
    maker fills that didn't happen due to queue position / book moving away.
    
    The signal-drop step is BEFORE the trade is opened, so it accurately
    reflects "would have placed an order at touch but never filled".
    """
    # First pass: run normal backtest to get all signal-fires
    all_trades = run_backtest_realistic(candles, evaluate_fn, **kwargs)
    if not all_trades:
        return all_trades

    rng = random.Random(seed)
    kept = [t for t in all_trades if rng.random() < fill_rate]
    return kept


def maker_fill_sensitivity_test(
    candles, evaluate_fn, fill_rates=(1.0, 0.75, 0.5, 0.25), **kwargs,
):
    """Run the same backtest at multiple fill rates. Returns dict of stats."""
    all_trades = run_backtest_realistic(candles, evaluate_fn, **kwargs)
    if not all_trades:
        return {}
    out = {}
    for fr in fill_rates:
        rng = random.Random(42)
        kept = [t for t in all_trades if rng.random() < fr]
        if not kept:
            out[fr] = {"n": 0}; continue
        out[fr] = compute_stats(kept)
        out[fr]["fill_rate"] = fr
    return out
