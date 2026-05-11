"""
audit_regime.py — per-regime audit of all 5 engines.

For each engine:
  1. Run the full portfolio backtest (90d, ETH blacklisted, MAX_OPEN=4,
     audit-derived per-coin/direction filters applied)
  2. Compute regime label at each trade's fire_ts using local classifier
     (matches KIROSHI's logic — trend_up / trend_down / range / chop)
  3. Split trade list by regime
  4. Compute stats per regime
  5. Output: which regime(s) each engine should fire in

The output drives a hard rule for each engine:
    BLOCKED_REGIMES = "chop,trend_down"   # only fire in trend_up + range, say

Note: regime is per-COIN, not market-wide. Each coin gets its own classifier.
"""
from __future__ import annotations
import sys, os, time, subprocess, shutil, json
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, '/tmp/multica-fresh')

GH_TOKEN = "<GH_TOKEN>"

# ─── Regime classifier ─────────────────────────────────────────────────
def compute_adx(df, period=14):
    h, l, c = df['high'].values, df['low'].values, df['close'].values
    if len(h) < 2: return np.full(len(h), np.nan)
    tr = np.maximum.reduce([h[1:]-l[1:], np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])])
    plus_dm = np.where((h[1:]-h[:-1]) > (l[:-1]-l[1:]), np.maximum(h[1:]-h[:-1], 0), 0)
    minus_dm = np.where((l[:-1]-l[1:]) > (h[1:]-h[:-1]), np.maximum(l[:-1]-l[1:], 0), 0)
    atr = pd.Series(tr).ewm(span=period).mean()
    plus_di = 100 * pd.Series(plus_dm).ewm(span=period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm).ewm(span=period).mean() / atr
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)
    adx = dx.ewm(span=period).mean()
    return np.concatenate(([np.nan], adx.values))


def classify_regime(df):
    """Per-bar regime: trend_up / trend_down / range / chop"""
    closes = df['close'].values
    if len(closes) < 200:
        return pd.Series([None] * len(closes), index=df.index)
    sma200 = pd.Series(closes).rolling(200).mean().values
    slope_20 = pd.Series(closes).pct_change(20).values
    adx = compute_adx(df, 14)
    labels = []
    for i in range(len(closes)):
        if i < 200 or pd.isna(sma200[i]) or pd.isna(adx[i]) or pd.isna(slope_20[i]):
            labels.append(None); continue
        above = closes[i] > sma200[i]
        slope = slope_20[i]
        trending = adx[i] > 20
        if trending and above and slope > 0.01: labels.append('trend_up')
        elif trending and (not above) and slope < -0.01: labels.append('trend_down')
        elif adx[i] < 15: labels.append('chop')
        else: labels.append('range')
    return pd.Series(labels, index=df.index)


# ─── Engine specs (use the audit-derived winners) ──────────────────────
ENGINES = [
    {
        "name": "wyckoff-v1",
        "env": {"STRATEGY_TIMEFRAME": "4h",
                 "STRATEGY_RANGE_LOOKBACK": "18",
                 "STRATEGY_SPRING_VOL_MULT": "1.3",
                 "STRATEGY_BREACH_MAX_PCT": "0.015",
                 "STRATEGY_RANGE_MIN_WIDTH_PCT": "0.01",
                 "STRATEGY_RANGE_MAX_WIDTH_PCT": "0.10",
                 "STRATEGY_RANGE_MIN_BARS_IN_BAND": "16"},
        "interval": "4h",
        # Audit winner: longs only + kill BNB/SOL + ETH
        "blacklist_coins": ("ETH", "BNB", "SOL"),
        "blacklist_shorts": ("BTC","LINK","AVAX","DOGE","XRP","HYPE","SUI","TON"),
    },
    {
        "name": "funding-div-v1",
        "env": {"STRATEGY_FUNDING_THRESHOLD_HI": "0.000015",
                 "STRATEGY_FUNDING_THRESHOLD_LO": "-0.000015"},
        "interval": "1h",
        "needs_funding": True,
        "blacklist_coins": ("ETH", "SOL"),
        "blacklist_shorts": ("BTC","LINK","AVAX","DOGE","BNB","XRP","HYPE","SUI","TON"),
    },
    {
        "name": "liq-heatmap-v1",
        "env": {"STRATEGY_VOL_SPIKE_MULT": "1.4",
                 "STRATEGY_MIN_CLUSTER_PIVOTS": "2"},
        "interval": "1h",
        "blacklist_coins": ("ETH", "DOGE", "BTC"),
    },
    {
        "name": "tod-reversion-v1",
        "env": {"STRATEGY_VWAP_DEV_THRESHOLD_PCT": "0.005"},
        "interval": "1h",
        "blacklist_coins": ("ETH",),
    },
    {
        "name": "avwap-mesh-v1",
        "env": {},
        "interval": "1h",
        "blacklist_coins": ("ETH",),
    },
]


def per_regime_stats(trades, regime_by_coin):
    """Group trade list by regime at fire-time, compute stats per group."""
    import statistics
    bucketed = {}
    unknown = []
    for t in trades:
        coin = t.coin
        ts = t.fire_ts
        rmap = regime_by_coin.get(coin)
        if rmap is None:
            unknown.append(t); continue
        # asof: latest regime label at-or-before fire_ts
        try:
            label = rmap.asof(ts)
        except Exception:
            label = None
        if pd.isna(label) or label is None:
            unknown.append(t); continue
        bucketed.setdefault(label, []).append(t)

    out = {}
    for label, ts in bucketed.items():
        rs = [t.pnl_r for t in ts]
        if not rs: continue
        n = len(rs)
        wins = [r for r in rs if r > 0]
        gw = sum(wins); gl = abs(sum(r for r in rs if r <= 0))
        pf = (gw / gl) if gl > 0 else float('inf')
        avg = statistics.mean(rs); std = statistics.stdev(rs) if n > 1 else 1.0
        sharpe = (avg / std) if std > 0 else 0
        eq = 0; peak = 0; dd = 0
        for r in rs:
            eq += r
            if eq > peak: peak = eq
            if peak - eq > dd: dd = peak - eq
        out[label] = {
            "n": n, "wr": round(len(wins) / n * 100, 1),
            "pf": round(pf, 2) if pf != float('inf') else 'inf',
            "sum_r": round(sum(rs), 2), "sharpe": round(sharpe, 3),
            "dd": round(dd, 2), "rec": round(sum(rs)/dd, 2) if dd > 0 else 'inf',
        }
    out["_unknown"] = {"n": len(unknown)}
    return out


def audit_one(spec):
    name = spec["name"]
    print(f"\n{'='*78}\nREGIME AUDIT: {name}\n{'='*78}")

    work = f"/tmp/regaud-{name}"
    if not Path(work).exists():
        subprocess.run(f"git clone -q https://{GH_TOKEN}@github.com/Dapperscyphozoa/{name}.git {work}",
                       shell=True, check=True)
        shutil.copy("/tmp/multica-fresh/backtester.py", f"{work}/")
        shutil.copy("/tmp/multica-fresh/backtester_v2.py", f"{work}/")
        if spec.get("needs_funding"):
            shutil.copy("/tmp/multica-fresh/backtester_with_funding.py", f"{work}/")

    for k, v in spec["env"].items(): os.environ[k] = v
    os.environ["ENGINE_NAME"] = f"regaud-{name}"
    os.environ["STATE_DIR"] = f"/tmp/regaud-state-{name}"
    os.environ["MAX_OPEN_POSITIONS"] = "4"

    for k in list(sys.modules):
        if k.startswith("engine") or k in ("backtester","backtester_v2","backtester_with_funding"):
            del sys.modules[k]
    sys.path = [p for p in sys.path if not p.startswith("/tmp/regaud") and not p.startswith("/tmp/audit")]
    sys.path.insert(0, work)

    from engine.config import ACTIVE_UNIVERSE
    from engine.signal_detector import evaluate_latest_bar
    from backtester_v2 import run_backtest_realistic, compute_stats

    if spec.get("needs_funding"):
        from backtester_with_funding import fetch_hl_candles_with_funding
        fetcher = lambda c: fetch_hl_candles_with_funding(c, days=120, interval=spec["interval"])
    else:
        from backtester import fetch_hl_candles
        fetcher = lambda c: fetch_hl_candles(c, days=120, interval=spec["interval"])

    # Fetch candles + classify regime per coin
    candles = {}; regime_by_coin = {}
    for c in ACTIVE_UNIVERSE:
        if c in spec["blacklist_coins"]: continue
        try:
            df = fetcher(c)
        except Exception as e:
            print(f"  {c}: fetch fail {e}"); continue
        if len(df) >= 250:
            candles[c] = df
            # Compute regime on ALL OHLCV — even if engine uses 4h, regime classifier wants 1h-style
            # If engine is 4h, regime is 4h-bar level (matches). Good.
            regime_by_coin[c] = classify_regime(df)
            print(f"  {c}: {len(df)} bars, regimes={dict(regime_by_coin[c].value_counts())}")
        time.sleep(0.3)

    warmup = 200 if spec["interval"] == "1h" else 60
    trades = run_backtest_realistic(
        candles, evaluate_latest_bar,
        max_open_positions=4, warmup_bars=warmup,
        blacklist_coins=spec.get("blacklist_coins", ()),
        blacklist_longs=spec.get("blacklist_longs", ()),
        blacklist_shorts=spec.get("blacklist_shorts", ()),
    )
    if not trades:
        print(f"  no trades"); return {"name": name, "status": "no_trades"}

    full = compute_stats(trades)
    print(f"\n  OVERALL: n={full['n']} PF={full['pf']} Sharpe={full['sharpe_per_trade']} sumR={full['sum_r']}")

    per_reg = per_regime_stats(trades, regime_by_coin)
    print(f"\n  PER-REGIME breakdown:")
    print(f"    {'regime':<14}{'n':>5}{'WR%':>7}{'PF':>7}{'Sharpe':>9}{'sumR':>8}{'DD':>7}{'rec':>6}")
    for label, st in sorted(per_reg.items(), key=lambda x: -x[1].get('n', 0)):
        if label == "_unknown":
            print(f"    {'(no regime)':<14}{st['n']:>5}")
            continue
        print(f"    {label:<14}{st['n']:>5}{st['wr']:>7}{st['pf']:>7}{st['sharpe']:>9}{st['sum_r']:>8}{st['dd']:>7}{st['rec']:>6}")

    # Verdict: which regimes pass PF >= 1.5
    good_regimes = []; bad_regimes = []
    for label, st in per_reg.items():
        if label == "_unknown" or st.get("n", 0) < 5: continue
        pf = st.get("pf")
        if isinstance(pf, (int, float)) and pf >= 1.5: good_regimes.append((label, pf, st["n"]))
        elif isinstance(pf, (int, float)) and pf < 1.0: bad_regimes.append((label, pf, st["n"]))
    print(f"\n  ✓ GOOD regimes (PF>=1.5, n>=5): {good_regimes}")
    print(f"  ✗ BAD regimes (PF<1.0, n>=5):   {bad_regimes}")

    return {"name": name, "overall_pf": full['pf'], "per_regime": per_reg,
             "good": good_regimes, "bad": bad_regimes}


def main():
    out = []
    for spec in ENGINES:
        try:
            r = audit_one(spec)
            out.append(r)
        except Exception as e:
            import traceback; traceback.print_exc()
            out.append({"name": spec["name"], "err": str(e)})

    print(f"\n\n{'='*78}\nREGIME-GATED VERDICTS\n{'='*78}")
    for r in out:
        if r.get("err"):
            print(f"  {r['name']:<22} FAILED: {r['err'][:60]}"); continue
        good = ",".join(f"{g[0]}({g[1]})" for g in r.get("good", [])) or "none"
        bad = ",".join(f"{b[0]}({b[1]})" for b in r.get("bad", [])) or "none"
        print(f"  {r['name']:<22} overall PF={r['overall_pf']} | GOOD: {good} | BAD: {bad}")

    Path("/tmp/multica/regime_audit.json").write_text(
        json.dumps(out, default=str, indent=2))


if __name__ == "__main__":
    main()
