"""
Strategy files for the multica build.

Each entry is a complete, drop-in `signal_detector.py` for one engine fork.
Compact, functional first-pass strategies. All return the trader-compatible
payload contract. Forks land in PAPER mode (LIVE_TRADING=0) so no live trades
fire — strategies iterate via backtest after deploy.
"""

# ═══════════════════════════════════════════════════════════════════════
# 1. liq-heatmap-v1 — Stop-hunt cluster fader
# ═══════════════════════════════════════════════════════════════════════
LIQ_HEATMAP = '''"""
liq-heatmap-v1 — Stop-hunt cluster fader.
Fades sweep wicks into clusters of equal highs/lows (where retail SLs cluster).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional, List, Tuple
from .config import STRATEGY_PARAMS, TRADE_PARAMS


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _pivots_high(a, lb):
    return [i for i in range(lb, len(a)-lb)
            if a[i] >= a[i-lb:i].max() and a[i] >= a[i+1:i+1+lb].max()]


def _pivots_low(a, lb):
    return [i for i in range(lb, len(a)-lb)
            if a[i] <= a[i-lb:i].min() and a[i] <= a[i+1:i+1+lb].min()]


def _cluster(prices: List[float], band: float, min_n: int) -> List[Tuple[float, int]]:
    if not prices: return []
    s = sorted(prices); clusters: List[List[float]] = [[s[0]]]
    for p in s[1:]:
        m = sum(clusters[-1]) / len(clusters[-1])
        if abs(p - m) / m <= band: clusters[-1].append(p)
        else: clusters.append([p])
    out = [(sum(c)/len(c), len(c)) for c in clusters if len(c) >= min_n]
    return sorted(out, key=lambda x: -x[1])


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    LB = STRATEGY_PARAMS.get("cluster_lookback", 120)
    PIV = STRATEGY_PARAMS.get("pivot_lookback", 5)
    BAND = STRATEGY_PARAMS.get("cluster_band_pct", 0.003)
    MIN = STRATEGY_PARAMS.get("min_cluster_pivots", 3)
    SWEEP = STRATEGY_PARAMS.get("sweep_threshold_pct", 0.002)
    VSPIKE = STRATEGY_PARAMS.get("vol_spike_mult", 1.8)
    PROX = STRATEGY_PARAMS.get("max_cluster_proximity_pct", 0.020)

    if df is None or len(df) < LB + 20: return None
    r = df.iloc[-LB:]
    highs = r["high"].values; lows = r["low"].values; closes = r["close"].values
    opens = r["open"].values
    vols = r["volume"].values if "volume" in r.columns else np.ones(len(r))

    sh_px = [float(highs[i]) for i in _pivots_high(highs, PIV)]
    sl_px = [float(lows[i])  for i in _pivots_low(lows, PIV)]
    bsl = _cluster(sh_px, BAND, MIN)
    ssl = _cluster(sl_px, BAND, MIN)
    if not bsl and not ssl: return None

    last_c = float(closes[-1]); last_h = float(highs[-1]); last_l = float(lows[-1])
    last_o = float(opens[-1]);  last_v = float(vols[-1])
    rng = last_h - last_l
    if rng <= 0: return None

    atr = calc_atr(highs, lows, closes, TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None

    avg_v = float(np.mean(vols[-21:-1])) if len(vols) >= 21 else float(np.mean(vols[:-1]))
    vspike = last_v / avg_v if avg_v > 0 else 0
    if vspike < VSPIKE: return None
    body_ratio = abs(last_c - last_o) / rng
    if body_ratio > 0.35: return None

    is_long = None; fired_pool = None; swept_lo = swept_hi = None
    for px, n in bsl:
        if abs(last_c - px) / px > PROX: continue
        bhi = px * (1 + BAND); blo = px * (1 - BAND)
        if last_h >= bhi and last_c < bhi:
            if (last_h - bhi) / px >= SWEEP * 0.5:
                is_long, fired_pool = False, (px, n, "BSL")
                swept_lo, swept_hi = blo, bhi
                break

    if is_long is None:
        for px, n in ssl:
            if abs(last_c - px) / px > PROX: continue
            bhi = px * (1 + BAND); blo = px * (1 - BAND)
            if last_l <= blo and last_c > blo:
                if (blo - last_l) / px >= SWEEP * 0.5:
                    is_long, fired_pool = True, (px, n, "SSL")
                    swept_lo, swept_hi = blo, bhi
                    break

    if is_long is None: return None
    pool_px, n_mem, pool_type = fired_pool

    if is_long:
        sl_p = swept_lo * (1 - 0.003)
        sl_d = last_c - sl_p
        opp = [p for p, _ in bsl if p > last_c * 1.005]
        tp_p = min(opp) if opp else last_c + 2 * sl_d
        if (tp_p - last_c) < 2 * sl_d: tp_p = last_c + 2 * sl_d
    else:
        sl_p = swept_hi * (1 + 0.003)
        sl_d = sl_p - last_c
        opp = [p for p, _ in ssl if p < last_c * 0.995]
        tp_p = max(opp) if opp else last_c - 2 * sl_d
        if (last_c - tp_p) < 2 * sl_d: tp_p = last_c - 2 * sl_d

    sl_pct = abs(last_c - sl_p) / last_c
    if sl_pct < 0.002 or sl_pct > 0.05: return None

    return {
        "fire_ts": df.index[-1], "ref_price": last_c, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS["max_hold_bars"],
        "fire_reason": f"sweep_{pool_type}_n={n_mem}",
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "pool_price": float(pool_px), "pool_type": pool_type,
        "pool_members": int(n_mem), "vol_spike": float(vspike),
        "body_ratio": float(body_ratio),
    }
'''

# ═══════════════════════════════════════════════════════════════════════
# 2. funding-div-v1 — Funding rate divergence
# ═══════════════════════════════════════════════════════════════════════
FUNDING_DIV = '''"""
funding-div-v1 — Funding-rate divergence engine.
Shorts crowded-long perps (high funding, no new high). Longs the inverse.
Reads funding via HL metaAndAssetCtxs in scheduler (cached); detector reads
last value via the bar's `funding` column injected by data layer.
For now we approximate: detector checks 4H momentum + 1H structure + uses
config-driven funding thresholds. The scheduler will inject funding into
extras via the universe-level fetch — strategy reads df.attrs.get("funding").
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import json, urllib.request, time
from typing import Optional
from .config import STRATEGY_PARAMS, TRADE_PARAMS


_funding_cache = {"ts": 0, "data": {}}
_FUNDING_TTL = 600  # 10 min


def _fetch_funding_all() -> dict:
    """Cached fetch of all coins\' funding (hr rate)."""
    now = time.time()
    if now - _funding_cache["ts"] < _FUNDING_TTL and _funding_cache["data"]:
        return _funding_cache["data"]
    try:
        req = urllib.request.Request("https://api.hyperliquid.xyz/info",
            data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            mc = json.loads(r.read())
        out = {}
        for u, c in zip(mc[0]["universe"], mc[1]):
            try: out[u["name"]] = float(c.get("funding", 0))
            except: pass
        _funding_cache["ts"] = now
        _funding_cache["data"] = out
        return out
    except Exception:
        return _funding_cache["data"]


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    F_HI = STRATEGY_PARAMS.get("funding_threshold_hi", 0.0003)   # 0.03% hourly
    F_LO = STRATEGY_PARAMS.get("funding_threshold_lo", -0.0002)
    coin = df.attrs.get("coin", "")
    if not coin: return None
    if df is None or len(df) < 30: return None

    fund = _fetch_funding_all().get(coin, 0.0)
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    last_c = float(closes[-1])

    is_long = None
    fire_reason = None
    # SHORT: funding too high → longs over-paying → fade
    if fund > F_HI:
        # No new 8-bar high
        if last_c < float(np.max(highs[-9:-1])):
            # Bearish bar
            if last_c < float(closes[-2]):
                is_long = False
                fire_reason = f"funding_hot_{fund*100:.4f}pct"
    elif fund < F_LO:
        if last_c > float(np.min(lows[-9:-1])):
            if last_c > float(closes[-2]):
                is_long = True
                fire_reason = f"funding_cold_{fund*100:.4f}pct"

    if is_long is None: return None

    atr = calc_atr(highs, lows, closes, TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None
    sl_mult = TRADE_PARAMS["sl_atr_mult"]
    tp_mult = TRADE_PARAMS["tp_atr_mult"]
    if is_long:
        sl_p = last_c - sl_mult * atr; tp_p = last_c + tp_mult * atr
    else:
        sl_p = last_c + sl_mult * atr; tp_p = last_c - tp_mult * atr

    return {
        "fire_ts": df.index[-1], "ref_price": last_c, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS["max_hold_bars"],
        "fire_reason": fire_reason,
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "funding_rate": float(fund),
    }
'''

# ═══════════════════════════════════════════════════════════════════════
# 3. vpin-v1 — Volume-synchronized PIN
# ═══════════════════════════════════════════════════════════════════════
VPIN = '''"""
vpin-v1 — Volume-synchronized PIN (toxicity).
Approximates VPIN from 1m candle aggressor flow inferred from close vs mid.
Fires reversal at swing extremes when VPIN > threshold AND direction
opposes price trend.

Note: true tick VPIN needs trade-by-trade data. This bar-aggregate proxy
trades off precision for free data. Tune VPIN_THRESHOLD aggressively.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional
from .config import STRATEGY_PARAMS, TRADE_PARAMS


def _aggressor_vol(o, h, l, c, v):
    """Estimate buy/sell aggressor split from candle anatomy."""
    rng = h - l
    if rng <= 0: return v * 0.5, v * 0.5
    buy_frac = (c - l) / rng
    sell_frac = (h - c) / rng
    s = buy_frac + sell_frac
    if s == 0: return v * 0.5, v * 0.5
    return v * (buy_frac / s), v * (sell_frac / s)


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    WIN = STRATEGY_PARAMS.get("vpin_window", 50)
    THRESH = STRATEGY_PARAMS.get("vpin_threshold", 0.55)
    SWING_LB = STRATEGY_PARAMS.get("swing_lookback", 24)
    EXT_PCT = STRATEGY_PARAMS.get("extreme_proximity_pct", 0.005)
    if df is None or len(df) < WIN + 5: return None

    opens = df["open"].values; highs = df["high"].values
    lows = df["low"].values; closes = df["close"].values
    vols = df["volume"].values if "volume" in df.columns else np.ones(len(df))

    buy_v = np.zeros(len(df)); sell_v = np.zeros(len(df))
    for i in range(len(df)):
        b, s = _aggressor_vol(opens[i], highs[i], lows[i], closes[i], vols[i])
        buy_v[i] = b; sell_v[i] = s

    win_buy = float(buy_v[-WIN:].sum()); win_sell = float(sell_v[-WIN:].sum())
    win_tot = win_buy + win_sell
    if win_tot <= 0: return None
    vpin = abs(win_buy - win_sell) / win_tot
    if vpin < THRESH: return None
    flow_dir = 1 if win_buy > win_sell else -1   # net buyer = +1

    last_c = float(closes[-1])
    sw_hi = float(np.max(highs[-SWING_LB:-1]))
    sw_lo = float(np.min(lows[-SWING_LB:-1]))

    is_long = None
    if last_c >= sw_hi * (1 - EXT_PCT) and flow_dir == -1:
        # at high, net selling = SHORT
        is_long = False
    elif last_c <= sw_lo * (1 + EXT_PCT) and flow_dir == 1:
        is_long = True
    else:
        return None

    atr = calc_atr(highs, lows, closes, TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None
    sl_m = TRADE_PARAMS["sl_atr_mult"]; tp_m = TRADE_PARAMS["tp_atr_mult"]
    if is_long: sl_p = last_c - sl_m * atr; tp_p = last_c + tp_m * atr
    else:       sl_p = last_c + sl_m * atr; tp_p = last_c - tp_m * atr

    return {
        "fire_ts": df.index[-1], "ref_price": last_c, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS["max_hold_bars"],
        "fire_reason": f"vpin_{vpin:.2f}_flow_{flow_dir}",
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "vpin": float(vpin), "flow_dir": int(flow_dir),
    }
'''

# ═══════════════════════════════════════════════════════════════════════
# 4. venue-lag-v1 — Cross-venue price discovery lag
# ═══════════════════════════════════════════════════════════════════════
VENUE_LAG = '''"""
venue-lag-v1 — Cross-venue price discovery lag.
When Binance OR Bybit moves > MOVE_PCT in 60s and HL hasn\'t followed,
the lag is the trade. Trade direction matches the leader.

Bar-mode adaptation: pulls latest mid from Binance + Bybit on every tick
(cached 30s), compares to HL last close. Fires on divergence.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import json, urllib.request, time
from typing import Optional, Dict
from .config import STRATEGY_PARAMS, TRADE_PARAMS


_ext_cache = {"ts": 0, "binance": {}, "bybit": {}}
_EXT_TTL = 30


def _binance_prices() -> Dict[str, float]:
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
        return {d["symbol"].replace("USDT",""): float(d["markPrice"])
                for d in data if d["symbol"].endswith("USDT")}
    except Exception:
        return {}


def _bybit_prices() -> Dict[str, float]:
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
        out = {}
        for d in data.get("result", {}).get("list", []):
            sym = d.get("symbol", "")
            if sym.endswith("USDT"):
                try: out[sym.replace("USDT","")] = float(d["lastPrice"])
                except: pass
        return out
    except Exception:
        return {}


def _ext_fetch():
    now = time.time()
    if now - _ext_cache["ts"] < _EXT_TTL: return
    _ext_cache["binance"] = _binance_prices()
    _ext_cache["bybit"]   = _bybit_prices()
    _ext_cache["ts"] = now


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    DIVPCT = STRATEGY_PARAMS.get("min_venue_divergence_pct", 0.0025)
    coin = df.attrs.get("coin", "")
    if not coin: return None
    if df is None or len(df) < 30: return None

    _ext_fetch()
    bin_p = _ext_cache["binance"].get(coin)
    byb_p = _ext_cache["bybit"].get(coin)
    if bin_p is None and byb_p is None: return None

    hl_p = float(df["close"].iloc[-1])
    # Use whichever venue has data (avg if both)
    ext_pxs = [p for p in [bin_p, byb_p] if p is not None and p > 0]
    if not ext_pxs: return None
    ext_avg = sum(ext_pxs) / len(ext_pxs)
    div = (ext_avg - hl_p) / hl_p

    if abs(div) < DIVPCT: return None

    is_long = div > 0   # external > HL → HL needs to catch up → LONG
    atr = calc_atr(df["high"].values, df["low"].values, df["close"].values,
                    TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None
    sl_m = TRADE_PARAMS["sl_atr_mult"]; tp_m = TRADE_PARAMS["tp_atr_mult"]
    if is_long: sl_p = hl_p - sl_m * atr; tp_p = hl_p + tp_m * atr
    else:       sl_p = hl_p + sl_m * atr; tp_p = hl_p - tp_m * atr

    return {
        "fire_ts": df.index[-1], "ref_price": hl_p, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS["max_hold_bars"],
        "fire_reason": f"lag_div_{div*100:.3f}pct",
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "binance_px": float(bin_p) if bin_p else None,
        "bybit_px":   float(byb_p) if byb_p else None,
        "hl_px":      float(hl_p),
        "divergence_pct": float(div),
    }
'''

# ═══════════════════════════════════════════════════════════════════════
# 5. tod-reversion-v1 — Time-of-day mean reversion
# ═══════════════════════════════════════════════════════════════════════
TOD_REVERSION = '''"""
tod-reversion-v1 — Time-of-day mean reversion engine.
Fires MR fades during statistically-profitable hours per asset.
v1 uses a SIMPLE whitelist (overridable via env) — hours during which
mean reversion has shown edge across crypto in general:
  Asian wee hours (UTC 02:00-05:00): thin liquidity sweeps
  London close to NY mid-session (UTC 15:00-18:00): MR setups
Calibration is left to a subsequent commit; v1 ships the framework.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional
from .config import STRATEGY_PARAMS, TRADE_PARAMS


def calc_vwap(highs, lows, closes, vols):
    typical = (highs + lows + closes) / 3.0
    cum_pv = (typical * vols).cumsum()
    cum_v = vols.cumsum()
    return cum_pv / np.where(cum_v == 0, 1, cum_v)


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    # Whitelist hours-of-day UTC (CSV via STRATEGY_PARAMS for override)
    whitelist_str = STRATEGY_PARAMS.get("hour_whitelist",
                                         "2,3,4,15,16,17,18")
    whitelist = set(int(x) for x in whitelist_str.split(","))
    DEV_PCT = STRATEGY_PARAMS.get("vwap_dev_threshold_pct", 0.004)
    if df is None or len(df) < 24: return None

    # Get hour in UTC from index
    last_ts = df.index[-1]
    try: hour_utc = int(last_ts.hour)
    except: return None
    if hour_utc not in whitelist: return None

    # Session VWAP (since hour start). Resample to 1m if df is finer;
    # for 1h frame, use rolling 24-bar VWAP as proxy.
    highs = df["high"].values; lows = df["low"].values
    closes = df["close"].values
    vols = df["volume"].values if "volume" in df.columns else np.ones(len(df))

    # Anchored VWAP over the last 24 bars (rough daily anchor for hourly)
    anchor = max(0, len(df) - 24)
    h24 = highs[anchor:]; l24 = lows[anchor:]; c24 = closes[anchor:]; v24 = vols[anchor:]
    vwap_arr = calc_vwap(h24, l24, c24, v24)
    cur_vwap = float(vwap_arr[-1])
    last_c = float(closes[-1])
    dev = (last_c - cur_vwap) / cur_vwap

    if abs(dev) < DEV_PCT: return None

    is_long = dev < 0   # price below vwap → mean revert UP

    atr = calc_atr(highs, lows, closes, TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None
    sl_m = TRADE_PARAMS["sl_atr_mult"]; tp_m = TRADE_PARAMS["tp_atr_mult"]
    if is_long: sl_p = last_c - sl_m * atr; tp_p = cur_vwap  # revert to VWAP
    else:       sl_p = last_c + sl_m * atr; tp_p = cur_vwap

    # Sanity: tp must be different from entry by >= 0.5×atr
    if abs(tp_p - last_c) < 0.5 * atr:
        if is_long: tp_p = last_c + tp_m * atr
        else:       tp_p = last_c - tp_m * atr

    return {
        "fire_ts": df.index[-1], "ref_price": last_c, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS.get("max_hold_bars", 8),
        "fire_reason": f"tod_h{hour_utc}_dev{dev*100:+.2f}pct",
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "hour_utc": int(hour_utc),
        "vwap": float(cur_vwap), "deviation_pct": float(dev),
    }
'''

# ═══════════════════════════════════════════════════════════════════════
# 6. wyckoff-v1 — Spring / Upthrust detector
# ═══════════════════════════════════════════════════════════════════════
WYCKOFF = '''"""
wyckoff-v1 — Spring / Upthrust phase detector.
Identifies trading ranges (low ATR + no BOS) and fires on Spring (LONG)
or Upthrust (SHORT) confirmation with volume validation.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional
from .config import STRATEGY_PARAMS, TRADE_PARAMS


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _is_trading_range(highs, lows, closes, lookback=24, atr_contract_pct=0.7):
    """Range = no clear trend AND ATR contracted."""
    win_hi = float(np.max(highs[-lookback:]))
    win_lo = float(np.min(lows[-lookback:]))
    rng_pct = (win_hi - win_lo) / win_lo if win_lo > 0 else 0
    if rng_pct < 0.015 or rng_pct > 0.12: return None  # too tight or too wide
    # ATR contraction
    atr_now = calc_atr(highs[-15:], lows[-15:], closes[-15:], 14)
    atr_pre = calc_atr(highs[-30:-15], lows[-30:-15], closes[-30:-15], 14)
    if not atr_now or not atr_pre or atr_pre <= 0: return None
    if atr_now / atr_pre > atr_contract_pct: return None
    return win_hi, win_lo


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    LB = STRATEGY_PARAMS.get("range_lookback", 24)
    VOL_MULT = STRATEGY_PARAMS.get("spring_vol_mult", 1.5)
    BREACH_MAX = STRATEGY_PARAMS.get("breach_max_pct", 0.005)
    if df is None or len(df) < LB + 5: return None

    highs = df["high"].values; lows = df["low"].values; closes = df["close"].values
    opens = df["open"].values
    vols = df["volume"].values if "volume" in df.columns else np.ones(len(df))

    r = _is_trading_range(highs, lows, closes, LB)
    if r is None: return None
    win_hi, win_lo = r

    last_h = float(highs[-1]); last_l = float(lows[-1])
    last_c = float(closes[-1]); last_o = float(opens[-1])
    last_v = float(vols[-1])
    avg_v = float(np.mean(vols[-LB:-1]))
    vmult = last_v / avg_v if avg_v > 0 else 0
    if vmult < VOL_MULT: return None

    is_long = None
    fire_reason = None
    # SPRING: wick below win_lo, close back above
    breach_pct = (win_lo - last_l) / win_lo if win_lo > 0 else 0
    if last_l < win_lo and last_c > win_lo and 0 < breach_pct < BREACH_MAX:
        if last_c > last_o:   # bullish close
            is_long = True
            fire_reason = f"spring_breach{breach_pct*100:.2f}pct_vol{vmult:.1f}x"
    if is_long is None:
        # UPTHRUST
        breach_pct = (last_h - win_hi) / win_hi if win_hi > 0 else 0
        if last_h > win_hi and last_c < win_hi and 0 < breach_pct < BREACH_MAX:
            if last_c < last_o:
                is_long = False
                fire_reason = f"upthrust_breach{breach_pct*100:.2f}pct_vol{vmult:.1f}x"
    if is_long is None: return None

    atr = calc_atr(highs, lows, closes, TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None
    # Wyckoff SL: 0.5% beyond spring/upthrust extreme
    if is_long:
        sl_p = last_l * (1 - 0.005)
        tp_p = win_hi   # range top is the target
        if (tp_p - last_c) < 3 * (last_c - sl_p):
            tp_p = last_c + 3 * (last_c - sl_p)
    else:
        sl_p = last_h * (1 + 0.005)
        tp_p = win_lo
        if (last_c - tp_p) < 3 * (sl_p - last_c):
            tp_p = last_c - 3 * (sl_p - last_c)

    sl_pct = abs(last_c - sl_p) / last_c
    if sl_pct < 0.003 or sl_pct > 0.06: return None

    return {
        "fire_ts": df.index[-1], "ref_price": last_c, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS["max_hold_bars"],
        "fire_reason": fire_reason,
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "range_high": float(win_hi), "range_low": float(win_lo),
        "vol_mult": float(vmult),
    }
'''

# ═══════════════════════════════════════════════════════════════════════
# 7. avwap-mesh-v1 — Anchored VWAP confluence
# ═══════════════════════════════════════════════════════════════════════
AVWAP_MESH = '''"""
avwap-mesh-v1 — Anchored VWAP mesh engine.
Anchors AVWAPs to key events (weekly open, daily open, 24h-anchor) and
fires fade when 2+ AVWAPs cluster within a tight band that price approaches
and rejects.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional, List, Tuple
from .config import STRATEGY_PARAMS, TRADE_PARAMS


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _avwap_from(highs, lows, closes, vols, anchor_idx: int) -> float:
    if anchor_idx >= len(closes): return float(closes[-1])
    h = highs[anchor_idx:]; l = lows[anchor_idx:]
    c = closes[anchor_idx:]; v = vols[anchor_idx:]
    typical = (h + l + c) / 3.0
    pv = (typical * v).sum()
    vv = v.sum()
    if vv <= 0: return float(closes[-1])
    return float(pv / vv)


def _swing_high_idx(highs, lows, closes, lb=6) -> Optional[int]:
    """Last confirmed swing high index."""
    n = len(highs)
    for i in range(n - lb - 1, lb, -1):
        if highs[i] >= max(highs[i-lb:i]) and highs[i] >= max(highs[i+1:i+1+lb]):
            return i
    return None


def _swing_low_idx(highs, lows, closes, lb=6) -> Optional[int]:
    n = len(lows)
    for i in range(n - lb - 1, lb, -1):
        if lows[i] <= min(lows[i-lb:i]) and lows[i] <= min(lows[i+1:i+1+lb]):
            return i
    return None


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    MESH_MIN = STRATEGY_PARAMS.get("mesh_min_anchors", 2)
    MESH_BAND = STRATEGY_PARAMS.get("mesh_band_pct", 0.005)
    APPROACH_PCT = STRATEGY_PARAMS.get("approach_max_pct", 0.01)
    if df is None or len(df) < 60: return None

    highs = df["high"].values; lows = df["low"].values; closes = df["close"].values
    opens = df["open"].values
    vols = df["volume"].values if "volume" in df.columns else np.ones(len(df))

    # Anchors: 0=full history; len-168=1week-ago(hourly); len-24=24h-ago
    n = len(df)
    anchors = []
    if n >= 168: anchors.append(("weekly", n - 168))
    if n >= 24:  anchors.append(("daily",  n - 24))
    sh = _swing_high_idx(highs, lows, closes, 6)
    sl = _swing_low_idx(highs, lows, closes, 6)
    if sh is not None: anchors.append(("swing_high", sh))
    if sl is not None: anchors.append(("swing_low",  sl))
    if len(anchors) < MESH_MIN: return None

    avwap_pxs = []
    for name, idx in anchors:
        try:
            v = _avwap_from(highs, lows, closes, vols, idx)
            if v > 0: avwap_pxs.append((name, v))
        except Exception: pass
    if len(avwap_pxs) < MESH_MIN: return None

    # Find clusters: any group of >= MESH_MIN AVWAPs within MESH_BAND of each other
    pxs = sorted([(p, n) for n, p in avwap_pxs])
    last_c = float(closes[-1])

    fired_mesh = None
    for i in range(len(pxs)):
        cluster = [pxs[i]]
        for j in range(i+1, len(pxs)):
            band = cluster[0][0] * MESH_BAND
            if pxs[j][0] - cluster[0][0] <= band:
                cluster.append(pxs[j])
        if len(cluster) >= MESH_MIN:
            mesh_lo = cluster[0][0]
            mesh_hi = cluster[-1][0]
            mesh_mid = (mesh_lo + mesh_hi) / 2.0
            # Is last bar approaching this mesh?
            if abs(last_c - mesh_mid) / mesh_mid <= APPROACH_PCT:
                fired_mesh = (mesh_lo, mesh_hi, mesh_mid, [n for _, n in cluster])
                break

    if fired_mesh is None: return None
    mesh_lo, mesh_hi, mesh_mid, anchor_names = fired_mesh

    # Wick rejection check on last bar
    last_h = float(highs[-1]); last_l = float(lows[-1])
    last_o = float(opens[-1])
    rng = last_h - last_l
    if rng <= 0: return None

    is_long = None
    fire_reason = None
    # Wicked above mesh + closed back below = SHORT
    if last_h >= mesh_hi and last_c < mesh_hi and last_c < last_o:
        is_long = False
        fire_reason = f"mesh_reject_high_{len(anchor_names)}anchors"
    # Wicked below mesh + closed back above = LONG
    elif last_l <= mesh_lo and last_c > mesh_lo and last_c > last_o:
        is_long = True
        fire_reason = f"mesh_reject_low_{len(anchor_names)}anchors"

    if is_long is None: return None

    atr = calc_atr(highs, lows, closes, TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None
    buf = 0.003
    if is_long:
        sl_p = mesh_lo * (1 - buf)
        sl_d = last_c - sl_p
        tp_p = last_c + max(TRADE_PARAMS["tp_atr_mult"] * atr, 2.5 * sl_d)
    else:
        sl_p = mesh_hi * (1 + buf)
        sl_d = sl_p - last_c
        tp_p = last_c - max(TRADE_PARAMS["tp_atr_mult"] * atr, 2.5 * sl_d)

    sl_pct = abs(last_c - sl_p) / last_c
    if sl_pct < 0.002 or sl_pct > 0.05: return None

    return {
        "fire_ts": df.index[-1], "ref_price": last_c, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS["max_hold_bars"],
        "fire_reason": fire_reason,
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "mesh_lo": float(mesh_lo), "mesh_hi": float(mesh_hi),
        "mesh_mid": float(mesh_mid),
        "anchors": ",".join(anchor_names),
    }
'''


STRATEGIES = {
    "liq-heatmap-v1":   LIQ_HEATMAP,
    "funding-div-v1":   FUNDING_DIV,
    "vpin-v1":          VPIN,
    "venue-lag-v1":     VENUE_LAG,
    "tod-reversion-v1": TOD_REVERSION,
    "wyckoff-v1":       WYCKOFF,
    "avwap-mesh-v1":    AVWAP_MESH,
}
