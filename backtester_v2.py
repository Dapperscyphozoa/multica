"""
backtester_v2.py — concurrency-aware walk-forward backtester.

Differences from backtester.py:
  - run_backtest_realistic() simulates a PORTFOLIO of N coins, not isolated coins
  - Respects MAX_OPEN_POSITIONS globally (only N open at once)
  - 1 open trade per coin enforced (matches trader.py runtime semantics)
  - Returns per-trade detail (entry_ts, coin, R, exit_reason) for proper audit
  - Supports per-coin blacklist for shorts / longs separately

Use this for any honest backtest. The original backtester.py was per-coin
isolated — which is what production trader.py is NOT. The audit numbers
were optimistic because of that.
"""
from __future__ import annotations
import json
import time
import urllib.request
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Callable, Optional, List, Dict

from backtester import fetch_hl_candles   # reuse the HL fetch


@dataclass
class Trade:
    coin: str
    fire_ts: pd.Timestamp
    entry_px: float
    sl_px: float
    tp_px: float
    is_long: bool
    max_hold_bars: int
    fire_reason: str = ""
    confidence: float = 1.0
    exit_ts: Optional[pd.Timestamp] = None
    exit_px: float = 0.0
    close_reason: str = ""
    sl_pct: float = 0.0
    pnl_pct: float = 0.0
    pnl_r: float = 0.0
    hold_bars_actual: int = 0


def _resolve_trade(trade: Trade, bars_ahead: pd.DataFrame) -> Trade:
    if len(bars_ahead) == 0:
        trade.close_reason = "no_data"
        trade.exit_px = trade.entry_px
        return trade
    n = min(trade.max_hold_bars, len(bars_ahead))
    for i in range(n):
        bar = bars_ahead.iloc[i]
        h, l = float(bar["high"]), float(bar["low"])
        if trade.is_long:
            if l <= trade.sl_px:
                trade.exit_px = trade.sl_px; trade.close_reason = "SL"
                trade.exit_ts = bars_ahead.index[i]
                trade.hold_bars_actual = i + 1
                break
            if h >= trade.tp_px:
                trade.exit_px = trade.tp_px; trade.close_reason = "TP"
                trade.exit_ts = bars_ahead.index[i]
                trade.hold_bars_actual = i + 1
                break
        else:
            if h >= trade.sl_px:
                trade.exit_px = trade.sl_px; trade.close_reason = "SL"
                trade.exit_ts = bars_ahead.index[i]
                trade.hold_bars_actual = i + 1
                break
            if l <= trade.tp_px:
                trade.exit_px = trade.tp_px; trade.close_reason = "TP"
                trade.exit_ts = bars_ahead.index[i]
                trade.hold_bars_actual = i + 1
                break
    else:
        i = n - 1
        trade.exit_px = float(bars_ahead.iloc[i]["close"])
        trade.close_reason = "TIME"
        trade.exit_ts = bars_ahead.index[i]
        trade.hold_bars_actual = n

    if trade.is_long:
        trade.pnl_pct = (trade.exit_px - trade.entry_px) / trade.entry_px
    else:
        trade.pnl_pct = (trade.entry_px - trade.exit_px) / trade.entry_px
    trade.pnl_r = (trade.pnl_pct / trade.sl_pct) if trade.sl_pct > 0 else 0.0
    return trade


def run_backtest_realistic(
    candles_per_coin: Dict[str, pd.DataFrame],
    detector: Callable[[pd.DataFrame], Optional[dict]],
    *,
    max_open_positions: int = 4,
    warmup_bars: int = 200,
    blacklist_longs: tuple = (),
    blacklist_shorts: tuple = (),
    blacklist_coins: tuple = (),
) -> List[Trade]:
    """
    Portfolio-aware walk-forward simulator.

    Iterates chronologically across the merged timeline of all coin candles.
    At each timestamp, walks every coin's candle at that index, asks the
    detector if it would fire, and only opens a trade if:
      - That coin has no open trade currently
      - Global open count < max_open_positions
      - Coin not in blacklist_coins
      - Direction not in blacklist for that coin

    Returns chronological list of resolved Trade objects.
    """
    # Align all coins to a shared timeline. We assume each df has the same
    # bar interval. Build a sorted superset of timestamps.
    if not candles_per_coin:
        return []
    universe = sorted(candles_per_coin.keys())
    # Use the first coin's index as the reference (they should all be the same hourly grid)
    ref_idx = sorted(set().union(*[df.index for df in candles_per_coin.values()]))

    open_trades: List[Trade] = []   # active
    closed_trades: List[Trade] = []
    # For each open trade, track which bar to start resolving from
    open_resolve_start: Dict[str, int] = {}   # coin → next_bar_idx

    n_bars = len(ref_idx)
    if n_bars < warmup_bars + 5:
        return []

    for i in range(warmup_bars, n_bars - 1):
        ts = ref_idx[i]

        # 1) Resolve any open trades whose conditions hit on bar i
        still_open: List[Trade] = []
        for t in open_trades:
            df = candles_per_coin.get(t.coin)
            if df is None or ts not in df.index:
                still_open.append(t); continue
            # Get this single bar
            try:
                bar = df.loc[ts]
            except KeyError:
                still_open.append(t); continue
            h, l = float(bar["high"]), float(bar["low"])
            hit = None
            if t.is_long:
                if l <= t.sl_px: t.exit_px, t.close_reason = t.sl_px, "SL"; hit = ts
                elif h >= t.tp_px: t.exit_px, t.close_reason = t.tp_px, "TP"; hit = ts
            else:
                if h >= t.sl_px: t.exit_px, t.close_reason = t.sl_px, "SL"; hit = ts
                elif l <= t.tp_px: t.exit_px, t.close_reason = t.tp_px, "TP"; hit = ts
            if hit is not None:
                t.exit_ts = hit
                # Compute holding bars
                df_idx = df.index.get_indexer([hit])[0]
                fire_idx = df.index.get_indexer([t.fire_ts])[0]
                t.hold_bars_actual = max(1, df_idx - fire_idx)
                if t.is_long:
                    t.pnl_pct = (t.exit_px - t.entry_px) / t.entry_px
                else:
                    t.pnl_pct = (t.entry_px - t.exit_px) / t.entry_px
                t.pnl_r = (t.pnl_pct / t.sl_pct) if t.sl_pct > 0 else 0.0
                closed_trades.append(t)
                continue
            # Time-stop check
            df_idx = df.index.get_indexer([ts])[0]
            fire_idx = df.index.get_indexer([t.fire_ts])[0]
            if df_idx - fire_idx >= t.max_hold_bars:
                t.exit_px = float(bar["close"]); t.close_reason = "TIME"
                t.exit_ts = ts
                t.hold_bars_actual = df_idx - fire_idx
                if t.is_long:
                    t.pnl_pct = (t.exit_px - t.entry_px) / t.entry_px
                else:
                    t.pnl_pct = (t.entry_px - t.exit_px) / t.entry_px
                t.pnl_r = (t.pnl_pct / t.sl_pct) if t.sl_pct > 0 else 0.0
                closed_trades.append(t)
                continue
            still_open.append(t)
        open_trades = still_open

        # 2) Look for new signals on each coin
        if len(open_trades) >= max_open_positions:
            continue
        open_coins = {t.coin for t in open_trades}
        for coin in universe:
            if coin in open_coins: continue
            if coin in blacklist_coins: continue
            df = candles_per_coin.get(coin)
            if df is None or ts not in df.index: continue
            try:
                bar_idx = df.index.get_indexer([ts])[0]
            except Exception:
                continue
            if bar_idx < warmup_bars: continue
            slice_df = df.iloc[:bar_idx + 1].copy()
            slice_df.attrs["coin"] = coin
            try:
                sig = detector(slice_df)
            except Exception:
                sig = None
            if sig is None: continue
            required = {"ref_price", "sl_px", "tp_px", "is_long", "max_hold_bars"}
            if not required.issubset(sig.keys()): continue
            is_long = bool(sig["is_long"])
            if is_long and coin in blacklist_longs: continue
            if (not is_long) and coin in blacklist_shorts: continue

            entry_px = float(sig["ref_price"])
            sl_px = float(sig["sl_px"])
            tp_px = float(sig["tp_px"])
            sl_pct = abs(entry_px - sl_px) / entry_px
            if sl_pct < 0.001 or sl_pct > 0.10: continue

            t = Trade(
                coin=coin, fire_ts=ts,
                entry_px=entry_px, sl_px=sl_px, tp_px=tp_px,
                is_long=is_long,
                max_hold_bars=int(sig["max_hold_bars"]),
                fire_reason=str(sig.get("fire_reason", "")),
                confidence=float(sig.get("confidence", 1.0)),
                sl_pct=sl_pct,
            )
            open_trades.append(t)
            open_coins.add(coin)
            if len(open_trades) >= max_open_positions: break

    # Force-close anything still open at end of data
    for t in open_trades:
        df = candles_per_coin.get(t.coin)
        if df is None: continue
        last = df.iloc[-1]
        t.exit_px = float(last["close"]); t.close_reason = "EOD"
        t.exit_ts = df.index[-1]
        if t.is_long:
            t.pnl_pct = (t.exit_px - t.entry_px) / t.entry_px
        else:
            t.pnl_pct = (t.entry_px - t.exit_px) / t.entry_px
        t.pnl_r = (t.pnl_pct / t.sl_pct) if t.sl_pct > 0 else 0.0
        closed_trades.append(t)

    closed_trades.sort(key=lambda t: t.fire_ts)
    return closed_trades


def compute_stats(trades: List[Trade]) -> dict:
    """Full audit-grade statistics on a trade list."""
    import statistics
    if not trades:
        return {"n": 0}
    n = len(trades)
    rs = [t.pnl_r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw = sum(wins); gl = abs(sum(losses))
    wr = len(wins) / n
    pf = (gw / gl) if gl > 0 else float("inf")
    avg_r = statistics.mean(rs)
    std_r = statistics.stdev(rs) if n > 1 else 1.0
    sharpe = (avg_r / std_r) if std_r > 0 else 0
    sum_r = sum(rs)

    # Equity curve + max drawdown
    eq = 0; peak = 0; max_dd = 0
    for r in rs:
        eq += r
        if eq > peak: peak = eq
        if peak - eq > max_dd: max_dd = peak - eq
    recovery = (sum_r / max_dd) if max_dd > 0 else float("inf")

    # Daily concurrency
    days = pd.Series([t.fire_ts.date() for t in trades])
    day_counts = days.value_counts()

    # Concentration: top 5% of trades vs gross wins
    rs_sorted = sorted(rs)
    top5_count = max(1, n // 20)
    top5_sum = sum(rs_sorted[-top5_count:])
    concentration = (top5_sum / gw) if gw > 0 else 0

    # Per-coin breakdown
    by_coin = {}
    for t in trades:
        by_coin.setdefault(t.coin, []).append(t.pnl_r)
    coin_stats = {}
    for c, r_list in by_coin.items():
        c_wins = [x for x in r_list if x > 0]
        c_gw = sum(c_wins); c_gl = abs(sum(x for x in r_list if x <= 0))
        coin_stats[c] = {
            "n": len(r_list),
            "wr": round(len(c_wins) / len(r_list) * 100, 1),
            "sum_r": round(sum(r_list), 2),
            "pf": round(c_gw / c_gl, 2) if c_gl > 0 else float("inf"),
        }

    # Per-direction breakdown
    longs = [t.pnl_r for t in trades if t.is_long]
    shorts = [t.pnl_r for t in trades if not t.is_long]
    def _direction_stats(rs):
        if not rs: return {"n": 0}
        gw = sum(r for r in rs if r > 0); gl = abs(sum(r for r in rs if r <= 0))
        return {"n": len(rs),
                 "wr": round(100 * len([r for r in rs if r > 0]) / len(rs), 1),
                 "sum_r": round(sum(rs), 2),
                 "pf": round(gw / gl, 2) if gl > 0 else float("inf")}

    return {
        "n": n,
        "wr_pct": round(wr * 100, 1),
        "pf": round(pf, 2) if pf != float("inf") else "inf",
        "sum_r": round(sum_r, 2),
        "avg_r": round(avg_r, 4),
        "sharpe_per_trade": round(sharpe, 4),
        "max_dd_r": round(max_dd, 2),
        "recovery_ratio": round(recovery, 2) if recovery != float("inf") else "inf",
        "days_active": int((days.max() - days.min()).days) if len(days) > 0 else 0,
        "max_trades_per_day": int(day_counts.max()) if len(day_counts) > 0 else 0,
        "mean_trades_per_day": round(float(day_counts.mean()), 2) if len(day_counts) > 0 else 0,
        "top5_concentration": round(concentration * 100, 1),
        "close_reasons": {r: sum(1 for t in trades if t.close_reason == r)
                           for r in ("SL", "TP", "TIME", "EOD")},
        "by_coin": coin_stats,
        "longs": _direction_stats(longs),
        "shorts": _direction_stats(shorts),
    }


def walk_forward_split(trades: List[Trade], train_frac: float = 0.67) -> tuple:
    """Split trades chronologically into IS and OOS sets."""
    if not trades: return [], []
    n = len(trades); split = int(n * train_frac)
    return trades[:split], trades[split:]
