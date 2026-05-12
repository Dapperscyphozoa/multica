"""
walk_forward.py — 3-window walk-forward validation.

Single-window backtests overfit the regime they cover. A real edge has to
hold across regimes. This runs each engine across 3 non-overlapping windows:

  Window A: 2024-Q4 (bull run / mostly trend_up)
  Window B: 2025-Q2 (chop / range-heavy)
  Window C: 2026-Q1 (recent — what's happening now)

For each engine in each window:
  - portfolio-aware backtest with v2 (audit-derived filters applied)
  - fee-adjusted (maker mode) Sharpe + PF
  - PASS = positive Sharpe in all 3 windows
  - ROBUST PASS = Sharpe > 0.05 in all 3 windows

Engines that pass walk-forward are real. The rest are overfit to one regime.
"""
from __future__ import annotations
import os, sys, time, json, pickle, subprocess, shutil
import urllib.request, urllib.error
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta

GH = "os.environ.get("GH_TOKEN", "")"

sys.path.insert(0, '/tmp/multica-fresh')

# Three non-overlapping 75-day windows
NOW_MS = int(time.time() * 1000)
DAY = 86400_000
WINDOWS = {
    "2024Q4": (NOW_MS - 540*DAY, NOW_MS - 465*DAY),   # ~540-465d ago
    "2025Q2": (NOW_MS - 330*DAY, NOW_MS - 255*DAY),   # ~330-255d ago
    "2026Q1": (NOW_MS -  90*DAY, NOW_MS),              # last 90d
}

UNIVERSE = ["BTC","SOL","LINK","AVAX","DOGE","BNB","XRP","HYPE"]


def fetch_hl_window(coin, start_ms, end_ms, interval='1h'):
    """Fetch a specific window of HL candles. Retries on 503."""
    body = json.dumps({"type":"candleSnapshot",
                        "req":{"coin":coin,"interval":interval,
                                "startTime":start_ms,"endTime":end_ms}}).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                data=body, headers={"Content-Type":"application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=20).read())
            df = pd.DataFrame(r)
            if len(df) == 0:
                return None
            df['t'] = pd.to_datetime(df['t'], unit='ms', utc=True)
            df = df.set_index('t').rename(columns={'o':'open','h':'high','l':'low','c':'close','v':'volume'})
            df = df[['open','high','low','close','volume']]
            for c in df.columns: df[c] = df[c].astype(float)
            return df
        except Exception as e:
            if attempt < 3:
                time.sleep((attempt+1)*3)
                continue
            return None
    return None


def fetch_universe(window_name, start_ms, end_ms, blacklist):
    cache = f"/tmp/multica/wf_cache_{window_name}.pkl"
    if Path(cache).exists():
        return pickle.load(open(cache, "rb"))
    candles = {}
    for coin in UNIVERSE:
        if coin in blacklist: continue
        print(f"    {coin}: fetching {window_name}", flush=True)
        df = fetch_hl_window(coin, start_ms, end_ms, "1h")
        if df is not None and len(df) >= 250:
            candles[coin] = df
        time.sleep(0.4)
    if candles:
        pickle.dump(candles, open(cache, "wb"))
    return candles


ENGINES = [
    {"name":"wyckoff-v1", "interval":"4h", "env":{
        "STRATEGY_TIMEFRAME":"4h","STRATEGY_RANGE_LOOKBACK":"18",
        "STRATEGY_SPRING_VOL_MULT":"1.3","STRATEGY_BREACH_MAX_PCT":"0.015",
        "STRATEGY_RANGE_MIN_WIDTH_PCT":"0.01","STRATEGY_RANGE_MAX_WIDTH_PCT":"0.10",
        "STRATEGY_RANGE_MIN_BARS_IN_BAND":"16","MAX_HOLD_BARS":"48",
    }, "blacklist_coins":("ETH","BNB","SOL"),
       "blacklist_shorts":("BTC","LINK","AVAX","DOGE","XRP","HYPE")},
    {"name":"funding-div-v1","interval":"1h","needs_funding":True,"env":{
        "STRATEGY_FUNDING_THRESHOLD_HI":"0.000015","STRATEGY_FUNDING_THRESHOLD_LO":"-0.000015",
        "MAX_HOLD_BARS":"48","SL_ATR_MULT":"2.5","TP_ATR_MULT":"5.0",
    },"blacklist_coins":("ETH","SOL"),
      "blacklist_shorts":("BTC","LINK","AVAX","DOGE","BNB","XRP","HYPE")},
    {"name":"liq-heatmap-v1","interval":"1h","env":{
        "STRATEGY_VOL_SPIKE_MULT":"1.4","STRATEGY_MIN_CLUSTER_PIVOTS":"2","MAX_HOLD_BARS":"24",
    },"blacklist_coins":("ETH","DOGE","BTC")},
    {"name":"avwap-mesh-v1","interval":"1h","env":{},"blacklist_coins":("ETH",)},
]


def setup_engine_for_bt(name, env):
    work = f"/tmp/wf-{name}"
    if not Path(work).exists():
        subprocess.run(f"git clone -q https://{GH}@github.com/Dapperscyphozoa/{name}.git {work}",
                       shell=True, check=True)
        shutil.copy("/tmp/multica-fresh/backtester.py", f"{work}/")
        shutil.copy("/tmp/multica-fresh/backtester_v2.py", f"{work}/")
    for k, v in env.items(): os.environ[k] = v
    os.environ.update({
        "ENGINE_NAME": f"wf-{name}",
        "STATE_DIR": f"/tmp/wf-state-{name}",
        "MAX_OPEN_POSITIONS": "4",
    })
    for k in list(sys.modules):
        if k.startswith("engine") or k.startswith("backtester"): del sys.modules[k]
    sys.path = [p for p in sys.path if not p.startswith("/tmp/wf-")]
    sys.path.insert(0, work)
    sys.path.insert(0, '/tmp/multica-fresh')
    return work


def fee_adjusted_stats(trades, mode="maker"):
    """Compute PF + Sharpe with fee model applied."""
    if not trades: return None
    if mode == "maker":
        entry_bps, tp_bps, sl_bps = -0.05, -0.05, 4.5
    else:
        entry_bps, tp_bps, sl_bps = 4.5, 4.5, 4.5
    adj = []
    for t in trades:
        cr = (t.close_reason or "").upper()
        fee_R = ((entry_bps + (tp_bps if "TP" in cr else sl_bps)) * 0.0067)
        adj.append(t.pnl_r - fee_R)
    n = len(adj)
    wins = [r for r in adj if r > 0]
    gw = sum(wins); gl = abs(sum(r for r in adj if r <= 0))
    pf = (gw / gl) if gl else float('inf')
    import statistics
    avg = statistics.mean(adj)
    std = statistics.stdev(adj) if n > 1 else 1.0
    sharpe = (avg / std) if std > 0 else 0
    return {"n": n, "pf": round(pf, 3) if pf != float('inf') else 'inf',
             "sumR": round(sum(adj), 2),
             "sharpe": round(sharpe, 4),
             "wr_pct": round(len(wins)/n*100, 1)}


def run_one(spec, window_name, candles):
    work = setup_engine_for_bt(spec["name"], spec["env"])
    from engine.signal_detector import evaluate_latest_bar
    from backtester_v2 import run_backtest_realistic
    warmup = 200 if spec["interval"] == "1h" else 60
    trades = run_backtest_realistic(
        candles, evaluate_latest_bar, max_open_positions=4,
        warmup_bars=warmup,
        blacklist_coins=spec.get("blacklist_coins", ()),
        blacklist_shorts=spec.get("blacklist_shorts", ()),
    )
    return fee_adjusted_stats(trades, mode="maker"), len(trades)


def main():
    results = {}
    for window_name, (start, end) in WINDOWS.items():
        print(f"\n{'='*78}\nWindow: {window_name}  ({datetime.fromtimestamp(start/1000,timezone.utc).date()} → "
               f"{datetime.fromtimestamp(end/1000,timezone.utc).date()})\n{'='*78}")
        # Single universe fetch per window
        candles = fetch_universe(window_name, start, end, set(["ETH"]))
        if not candles:
            print(f"  no candles for {window_name}"); continue
        print(f"  fetched {len(candles)} coins, {sum(len(c) for c in candles.values())} bars total")

        for spec in ENGINES:
            try:
                # Filter candles by engine blacklist
                filtered = {c: df for c, df in candles.items() if c not in spec["blacklist_coins"]}
                # If engine uses 4h, resample
                if spec["interval"] == "4h":
                    resampled = {}
                    for c, df in filtered.items():
                        agg = df.resample("4h").agg({
                            "open":"first","high":"max","low":"min",
                            "close":"last","volume":"sum"}).dropna()
                        if len(agg) >= 60:
                            resampled[c] = agg
                    filtered = resampled
                stats, n = run_one(spec, window_name, filtered)
                if stats is None:
                    print(f"  {spec['name']:<20}: 0 trades"); continue
                key = f"{spec['name']}::{window_name}"
                results[key] = stats
                print(f"  {spec['name']:<20}: n={stats['n']:<4} PF={stats['pf']:<6} "
                      f"Sharpe={stats['sharpe']:<7} sumR={stats['sumR']}")
            except Exception as e:
                import traceback; traceback.print_exc()

    # Verdict per engine
    print(f"\n\n{'='*78}\nWALK-FORWARD VERDICTS\n{'='*78}")
    for spec in ENGINES:
        name = spec["name"]
        windows_results = {w: results.get(f"{name}::{w}") for w in WINDOWS}
        valid = [v for v in windows_results.values() if v]
        if len(valid) < 3:
            print(f"\n{name}: INCOMPLETE ({len(valid)}/3 windows)")
            continue
        sharpes = [v["sharpe"] for v in valid]
        pfs = [v["pf"] for v in valid if isinstance(v["pf"], (int, float))]
        all_pos = all(s > 0 for s in sharpes)
        all_robust = all(s > 0.05 for s in sharpes)
        verdict = "ROBUST PASS" if all_robust else ("PASS" if all_pos else "FAIL")
        print(f"\n{name}: {verdict}")
        for w in WINDOWS:
            r = windows_results.get(w)
            if r: print(f"  {w}: PF={r['pf']:<6} Sharpe={r['sharpe']:<7} sumR={r['sumR']:<7} n={r['n']}")
            else: print(f"  {w}: (no data)")

    Path("/tmp/multica/walk_forward.json").write_text(json.dumps(results, default=str, indent=2))
    print(f"\nresults → /tmp/multica/walk_forward.json")


if __name__ == "__main__":
    main()
