"""
audit_v2.py — proper portfolio-aware audit of all 5 engines.

Uses backtester_v2 to enforce:
  - MAX_OPEN_POSITIONS=4 globally
  - 1 open trade per coin
  - ETH blacklist (per user directive: ETH doesn't respect charts)

For each engine:
  1. Clone its repo fresh
  2. Apply winning sweep params via env vars
  3. Fetch 90d of candles for full universe
  4. Run portfolio-aware backtest
  5. Compute full audit stats
  6. IS/OOS split (67/33)
  7. Per-coin breakdown — flag any coin with negative sum_r as candidate for blacklist
  8. Write AUDIT_v2.md to each repo and commit

Verdict thresholds:
  - PF >= 1.3
  - OOS PF >= 0.85 * IS PF (no >15% degradation)
  - Sharpe/trade >= 0.10
  - sumR / maxDD >= 1.5
  - max_trades_per_day <= 6
"""
from __future__ import annotations
import os
import sys
import time
import subprocess
import shutil
import json
from pathlib import Path

sys.path.insert(0, "/tmp/multica-fresh")
GH_TOKEN = os.environ.get("GH_TOKEN", "")

ENGINES = [
    {
        "name": "tod-reversion-v1",
        "env": {"STRATEGY_VWAP_DEV_THRESHOLD_PCT": "0.005"},
        "interval": "1h",
        "max_hold": None,   # use engine's own
    },
    {
        "name": "avwap-mesh-v1",
        "env": {},
        "interval": "1h",
        "max_hold": None,
    },
    {
        "name": "liq-heatmap-v1",
        "env": {"STRATEGY_VOL_SPIKE_MULT": "1.4",
                 "STRATEGY_MIN_CLUSTER_PIVOTS": "2"},
        "interval": "1h",
        "max_hold": None,
    },
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
        "max_hold": None,
    },
    {
        "name": "funding-div-v1",
        "env": {"STRATEGY_FUNDING_THRESHOLD_HI": "0.000015",
                 "STRATEGY_FUNDING_THRESHOLD_LO": "-0.000015"},
        "interval": "1h",
        "max_hold": None,
        "needs_funding": True,
    },
]

# User directive: ETH doesn't respect charts. Blacklist universally.
GLOBAL_BLACKLIST = ("ETH",)


def audit_engine(spec: dict) -> dict:
    name = spec["name"]
    print(f"\n{'='*72}\nAUDIT v2: {name}\n{'='*72}")

    work = f"/tmp/audit-v2-{name}"
    if Path(work).exists():
        shutil.rmtree(work)
    subprocess.run(
        f"git clone -q https://{GH_TOKEN}@github.com/Dapperscyphozoa/{name}.git {work}",
        shell=True, check=True,
    )
    # Drop backtester files
    shutil.copy("/tmp/multica-fresh/backtester.py", f"{work}/backtester.py")
    shutil.copy("/tmp/multica-fresh/backtester_v2.py", f"{work}/backtester_v2.py")
    if spec.get("needs_funding"):
        shutil.copy("/tmp/multica-fresh/backtester_with_funding.py",
                     f"{work}/backtester_with_funding.py")

    # Apply env overrides
    for k, v in spec["env"].items():
        os.environ[k] = v
    os.environ["ENGINE_NAME"] = f"audit-v2-{name}"
    os.environ["STATE_DIR"] = f"/tmp/audit-v2-state-{name}"
    os.environ["MAX_OPEN_POSITIONS"] = "4"

    # Reload engine module
    for k in list(sys.modules):
        if k.startswith("engine") or k in ("backtester", "backtester_v2",
                                              "backtester_with_funding"):
            del sys.modules[k]
    # Path manipulation
    sys.path = [p for p in sys.path if not p.startswith("/tmp/audit")]
    sys.path.insert(0, work)

    from engine.config import ACTIVE_UNIVERSE, MAX_OPEN_POSITIONS
    from engine.signal_detector import evaluate_latest_bar
    from backtester_v2 import (run_backtest_realistic, compute_stats,
                                walk_forward_split)

    # Decide fetcher
    if spec.get("needs_funding"):
        from backtester_with_funding import fetch_hl_candles_with_funding
        def fetcher(coin, days, interval):
            return fetch_hl_candles_with_funding(coin, days=days, interval=interval)
    else:
        from backtester import fetch_hl_candles
        def fetcher(coin, days, interval):
            return fetch_hl_candles(coin, days=days, interval=interval)

    interval = spec["interval"]
    print(f"  universe={ACTIVE_UNIVERSE} interval={interval} "
          f"MAX_OPEN={MAX_OPEN_POSITIONS} blacklist={GLOBAL_BLACKLIST}")

    # Fetch candles for full universe
    candles = {}
    for c in ACTIVE_UNIVERSE:
        if c in GLOBAL_BLACKLIST: continue
        try:
            df = fetcher(c, 90, interval)
        except Exception as e:
            print(f"    {c}: fetch failed {e}"); continue
        if len(df) >= 100:
            candles[c] = df
            print(f"    {c}: {len(df)} bars")
        time.sleep(0.4)

    if not candles:
        print("  NO DATA"); return {"name": name, "status": "no_data"}

    # Portfolio backtest
    warmup = 200 if interval == "1h" else 60
    trades = run_backtest_realistic(
        candles, evaluate_latest_bar,
        max_open_positions=MAX_OPEN_POSITIONS,
        warmup_bars=warmup,
        blacklist_coins=GLOBAL_BLACKLIST,
    )
    if not trades:
        print("  NO TRADES"); return {"name": name, "status": "no_trades",
                                       "n_candles": len(candles)}

    full_stats = compute_stats(trades)
    is_trades, oos_trades = walk_forward_split(trades, 0.67)
    is_stats = compute_stats(is_trades) if is_trades else {}
    oos_stats = compute_stats(oos_trades) if oos_trades else {}

    # Verdict
    issues = []
    if isinstance(full_stats["pf"], (int, float)) and full_stats["pf"] < 1.3:
        issues.append(f"PF {full_stats['pf']} < 1.3 (marginal)")
    if isinstance(oos_stats.get("pf"), (int, float)) and \
       isinstance(is_stats.get("pf"), (int, float)) and is_stats["pf"] > 0:
        if oos_stats["pf"] < 0.85 * is_stats["pf"]:
            issues.append(f"OOS PF {oos_stats['pf']} < 85% of IS {is_stats['pf']} (overfit)")
    if isinstance(oos_stats.get("pf"), (int, float)) and oos_stats["pf"] < 1.0:
        issues.append(f"OOS PF {oos_stats['pf']} < 1.0 (no OOS edge)")
    if full_stats["sharpe_per_trade"] < 0.10:
        issues.append(f"Sharpe/trade {full_stats['sharpe_per_trade']} < 0.10")
    if (isinstance(full_stats["recovery_ratio"], (int, float))
        and full_stats["recovery_ratio"] < 1.5):
        issues.append(f"sumR/maxDD {full_stats['recovery_ratio']} < 1.5 (high DD)")
    if full_stats["max_trades_per_day"] > 6:
        issues.append(f"{full_stats['max_trades_per_day']} trades/day max > 6")

    # Per-coin candidates for blacklist (those with PF < 0.7 and n >= 5)
    suspicious_coins = []
    for c, st in full_stats["by_coin"].items():
        if st["n"] >= 5 and isinstance(st["pf"], (int, float)) and st["pf"] < 0.7:
            suspicious_coins.append((c, st["n"], st["pf"], st["sum_r"]))

    # Print summary
    print(f"\n  FULL  n={full_stats['n']:<4} WR={full_stats['wr_pct']:<5} "
          f"PF={full_stats['pf']:<5} sumR={full_stats['sum_r']:<7} "
          f"Sharpe={full_stats['sharpe_per_trade']:<7} "
          f"DD={full_stats['max_dd_r']:<5} rec={full_stats['recovery_ratio']:<5}")
    print(f"  IS    n={is_stats.get('n','?'):<4} WR={is_stats.get('wr_pct','?'):<5} "
          f"PF={is_stats.get('pf','?'):<5} sumR={is_stats.get('sum_r','?')}")
    print(f"  OOS   n={oos_stats.get('n','?'):<4} WR={oos_stats.get('wr_pct','?'):<5} "
          f"PF={oos_stats.get('pf','?'):<5} sumR={oos_stats.get('sum_r','?')}")
    print(f"  Days active={full_stats['days_active']} "
          f"trades/day max={full_stats['max_trades_per_day']} "
          f"mean={full_stats['mean_trades_per_day']}")
    print(f"  Per-coin: {full_stats['by_coin']}")
    print(f"  Longs:  {full_stats['longs']}")
    print(f"  Shorts: {full_stats['shorts']}")
    if suspicious_coins:
        print(f"  ⚠ losing coins (PF<0.7, n>=5): {suspicious_coins}")
    if issues:
        print(f"  VERDICT: ✗ {len(issues)} issues")
        for iss in issues: print(f"    - {iss}")
    else:
        print(f"  VERDICT: ✓ passes all checks")

    return {
        "name": name,
        "status": "ok",
        "full": full_stats, "is": is_stats, "oos": oos_stats,
        "issues": issues, "suspicious_coins": suspicious_coins,
    }


def main():
    out = []
    for spec in ENGINES:
        try:
            out.append(audit_engine(spec))
        except Exception as e:
            import traceback; traceback.print_exc()
            out.append({"name": spec["name"], "status": "failed", "err": str(e)})

    print(f"\n\n{'='*72}\nAUDIT v2 MASTER VERDICT (with concurrency + ETH blacklist)\n{'='*72}")
    for r in out:
        if r.get("status") != "ok":
            print(f"  {r['name']:<22} {r.get('status'):<10} {r.get('err','')[:60]}")
            continue
        f = r["full"]; ois = r["oos"]
        verdict = "✓ PASS" if not r["issues"] else f"✗ {len(r['issues'])}"
        print(f"  {r['name']:<22} n={f['n']:<4} "
              f"PF={f['pf']:<5} OOS_PF={ois.get('pf','?'):<5} "
              f"Sharpe={f['sharpe_per_trade']:<7} "
              f"rec={f['recovery_ratio']:<5} {verdict}")

    # Persist results
    Path("/tmp/multica/audit_v2_results.json").write_text(
        json.dumps(out, default=str, indent=2))
    print(f"\n  Persisted to /tmp/multica/audit_v2_results.json")
    return out


if __name__ == "__main__":
    main()
