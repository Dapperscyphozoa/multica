"""
deep_search.py — Find params that pass real audit gates.

Architecture:
  1. Cache HL candles to disk (60d 1h + 90d 4h, all coins) — fetch ONCE
  2. For each engine, run a focused grid:
       - Normal direction
       - Inverted direction
  3. Apply hard audit gates:
       - PF ≥ 1.3
       - OOS PF ≥ 1.0 (no curve-fit collapse)
       - Sharpe/trade ≥ 0.10
       - sumR/maxDD ≥ 1.2
       - Min 30 trades
  4. Winner = best sumR among combos passing all gates
  5. If inverted beats normal: flag for code-level direction flip
"""
from __future__ import annotations
import os, sys, time, json, statistics, subprocess, pickle
from pathlib import Path
import urllib.request

CACHE_DIR = Path("/tmp/multica/candle_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

COINS = ["BTC", "ETH", "SOL", "LINK", "AVAX", "DOGE", "BNB", "XRP", "HYPE"]


def hl_fetch(coin: str, days: int, interval: str) -> list:
    """Direct HL fetch, returns raw list of bars."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    body = json.dumps({"type": "candleSnapshot",
                       "req": {"coin": coin, "interval": interval,
                                "startTime": start_ms, "endTime": end_ms}}).encode()
    req = urllib.request.Request("https://api.hyperliquid.xyz/info",
        data=body, headers={"Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = (attempt + 1) * 8
                print(f"    429 — sleeping {wait}s")
                time.sleep(wait)
            else:
                raise
    return []


def hl_funding_full(coin: str, days: int) -> list:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    out = []
    cursor = start_ms
    while cursor < end_ms:
        body = json.dumps({"type": "fundingHistory", "coin": coin,
                            "startTime": cursor, "endTime": end_ms}).encode()
        req = urllib.request.Request("https://api.hyperliquid.xyz/info",
            data=body, headers={"Content-Type": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    batch = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep((attempt + 1) * 8)
                else:
                    return out
        else:
            return out
        if not batch: break
        out.extend(batch)
        last_t = int(batch[-1].get("time", 0))
        if last_t <= cursor: break
        cursor = last_t + 1
        time.sleep(0.5)
    return out


def build_cache():
    """Pre-fetch all candles + funding to disk. Run once."""
    targets = [
        ("1h", 90, "candles_1h_90d"),
        ("4h", 90, "candles_4h_90d"),
    ]
    for interval, days, name in targets:
        cache_file = CACHE_DIR / f"{name}.pkl"
        if cache_file.exists():
            data = pickle.load(open(cache_file, "rb"))
            if len(data) >= len(COINS) - 1:  # tolerate 1 missing
                print(f"  ✓ {name} cached ({len(data)} coins)")
                continue
        print(f"  fetching {name}...")
        data = {}
        for coin in COINS:
            print(f"    {coin}", end=" ", flush=True)
            bars = hl_fetch(coin, days=days, interval=interval)
            print(f"({len(bars)} bars)")
            data[coin] = bars
            time.sleep(1.5)
        pickle.dump(data, open(cache_file, "wb"))
        print(f"  saved {cache_file}")

    # Funding cache
    fund_file = CACHE_DIR / "funding_90d.pkl"
    if not fund_file.exists():
        print("  fetching funding history 90d...")
        fdata = {}
        for coin in COINS:
            print(f"    {coin}", end=" ", flush=True)
            f = hl_funding_full(coin, days=90)
            print(f"({len(f)} samples)")
            fdata[coin] = f
            time.sleep(1.5)
        pickle.dump(fdata, open(fund_file, "wb"))
    else:
        print(f"  ✓ funding cached")


def load_candles(interval: str = "1h", days: int = 90):
    name = f"candles_{interval}_{days}d"
    raw = pickle.load(open(CACHE_DIR / f"{name}.pkl", "rb"))
    import pandas as pd
    out = {}
    for coin, bars in raw.items():
        if not bars: continue
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.set_index("t").rename(
            columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
        for col in ("open","high","low","close","volume"):
            if col in df.columns: df[col] = df[col].astype(float)
        df.attrs["coin"] = coin
        out[coin] = df
    return out


def load_funding(days: int = 90):
    raw = pickle.load(open(CACHE_DIR / "funding_90d.pkl", "rb"))
    import pandas as pd
    out = {}
    for coin, samples in raw.items():
        if not samples: continue
        df = pd.DataFrame(samples)
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        df["fundingRate"] = df["fundingRate"].astype(float)
        out[coin] = df.set_index("time").sort_index()
    return out


# ─── Trade simulator (vectorised-ish) ────────────────────────────────
def sim_trades(coin: str, bars, detector, trade_params: dict, warmup: int,
                invert: bool = False) -> list:
    bars.attrs['coin'] = coin
    trades = []
    open_until = -1
    for i in range(warmup, len(bars) - 1):
        if i <= open_until: continue
        sl = bars.iloc[:i+1]; sl.attrs['coin'] = coin
        try: sig = detector(sl)
        except: sig = None
        if sig is None: continue
        if not {'ref_price','sl_px','tp_px','is_long','max_hold_bars'}.issubset(sig.keys()): continue
        entry = float(sig['ref_price'])
        sl_o = float(sig['sl_px']); tp_o = float(sig['tp_px'])
        is_long_o = bool(sig['is_long']); hold = int(sig['max_hold_bars'])
        is_long = (not is_long_o) if invert else is_long_o
        sl_d = abs(entry - sl_o); tp_d = abs(entry - tp_o)
        if is_long:
            sl_p = entry - sl_d; tp_p = entry + tp_d
        else:
            sl_p = entry + sl_d; tp_p = entry - tp_d
        sl_pct = abs(entry - sl_p) / entry
        if sl_pct < 0.001 or sl_pct > 0.10: continue
        bars_ahead = bars.iloc[i+1:i+1+hold]
        if len(bars_ahead) == 0: continue
        exit_px = float(bars_ahead.iloc[-1]['close']); offset = len(bars_ahead) - 1
        for j, (_, bar) in enumerate(bars_ahead.iterrows()):
            h, l = float(bar['high']), float(bar['low'])
            if is_long:
                if l <= sl_p: exit_px, offset = sl_p, j; break
                if h >= tp_p: exit_px, offset = tp_p, j; break
            else:
                if h >= sl_p: exit_px, offset = sl_p, j; break
                if l <= tp_p: exit_px, offset = tp_p, j; break
        pnl_pct = (exit_px - entry)/entry if is_long else (entry - exit_px)/entry
        trades.append({'ts': bars.index[i], 'pnl_r': pnl_pct/sl_pct, 'coin': coin})
        open_until = i + offset + 1
    return trades


# ─── Audit gates ─────────────────────────────────────────────────────
def audit(trades: list) -> dict:
    """Compute full stats + verdict."""
    if not trades or len(trades) < 10:
        return {'pass': False, 'n': len(trades), 'reason': 'too few trades'}
    trades = sorted(trades, key=lambda t: t['ts'])
    n = len(trades)
    rs = [t['pnl_r'] for t in trades]
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    wr = len(wins) / n
    gw = sum(wins); gl = abs(sum(losses))
    pf = (gw / gl) if gl > 0 else float('inf')
    avg_r = statistics.mean(rs); std_r = statistics.stdev(rs) if n > 1 else 1.0
    sharpe = avg_r / std_r if std_r > 0 else 0
    eq = 0; peak = 0; max_dd = 0
    for r in rs:
        eq += r
        if eq > peak: peak = eq
        if peak - eq > max_dd: max_dd = peak - eq
    recovery = (sum(rs) / max_dd) if max_dd > 0 else float('inf')
    split = int(n * 0.67)
    is_t = trades[:split]; oos_t = trades[split:]
    def quick(ts):
        if not ts: return None
        r = [t['pnl_r'] for t in ts]
        gw = sum(x for x in r if x > 0); gl = abs(sum(x for x in r if x <= 0))
        return {'n': len(r), 'wr': len([x for x in r if x>0])/len(r),
                 'pf': (gw/gl) if gl > 0 else float('inf'), 'sumR': sum(r)}
    is_s = quick(is_t); oos_s = quick(oos_t)
    # Daily concurrency
    import pandas as pd
    days = pd.Series([t['ts'].date() for t in trades]).value_counts()
    
    result = {'n': n, 'wr': wr, 'pf': pf, 'sumR': sum(rs),
              'avg_r': avg_r, 'sharpe': sharpe, 'max_dd': max_dd,
              'recovery': recovery, 'max_per_day': int(days.max()),
              'is_pf': is_s['pf'] if is_s else None,
              'oos_pf': oos_s['pf'] if oos_s else None,
              'is_n': is_s['n'] if is_s else 0, 'oos_n': oos_s['n'] if oos_s else 0}
    
    # GATES
    issues = []
    if n < 30: issues.append(f"n={n}<30")
    if pf < 1.3: issues.append(f"pf={pf:.2f}<1.3")
    if oos_s and oos_s['pf'] < 1.0: issues.append(f"oos_pf={oos_s['pf']:.2f}<1.0")
    if oos_s and is_s and oos_s['pf'] < 0.7 * is_s['pf']:
        issues.append(f"oos degraded {(1-oos_s['pf']/is_s['pf'])*100:.0f}%")
    if sharpe < 0.10: issues.append(f"sharpe={sharpe:.3f}<0.10")
    if recovery < 1.2: issues.append(f"rec={recovery:.2f}<1.2")
    if days.max() > 8: issues.append(f"max/day={days.max()}>8")
    result['issues'] = issues
    result['pass'] = (len(issues) == 0)
    return result


# ─── Combo runner ────────────────────────────────────────────────────
def run_combo(engine_dir: str, params: dict, candles, funding=None,
                interval: str = "1h", invert: bool = False) -> dict:
    """Run a single param combo across cached universe. Returns audit dict."""
    sys.path = [p for p in sys.path if not p.startswith('/tmp/inv-') and
                 not p.startswith('/tmp/audit-') and not p.startswith('/tmp/combo-')]
    sys.path.insert(0, engine_dir)
    # Clear cache
    for k in list(sys.modules):
        if k.startswith('engine'): del sys.modules[k]
    # Apply params
    os.environ['ENGINE_NAME'] = 'combo'
    os.environ['STATE_DIR'] = '/tmp/combo-state'
    for k, v in params.items():
        os.environ[k] = str(v)
    from engine.config import TRADE_PARAMS, STRATEGY_PARAMS, ACTIVE_UNIVERSE
    from engine.signal_detector import evaluate_latest_bar
    
    all_trades = []
    for coin in ACTIVE_UNIVERSE:
        if coin not in candles: continue
        bars = candles[coin].copy()
        if funding and coin in funding:
            f = funding[coin]
            bars['funding'] = f['fundingRate'].reindex(bars.index, method='ffill')
        warmup = max(50, int(len(bars) * 0.20))
        trades = sim_trades(coin, bars, evaluate_latest_bar, TRADE_PARAMS, warmup, invert=invert)
        all_trades.extend(trades)
    return audit(all_trades)


if __name__ == "__main__":
    print("=== building cache ===")
    build_cache()
    print("\n=== cache loaded ===")
    c1h = load_candles("1h", 90)
    c4h = load_candles("4h", 90)
    fund = load_funding(90)
    print(f"  1h: {len(c1h)} coins")
    print(f"  4h: {len(c4h)} coins")
    print(f"  funding: {len(fund)} coins")
