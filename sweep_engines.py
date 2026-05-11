"""
sweep_engines.py — parameter sweep across all 7 multica engines.

Per engine: small focused grid (5-8 combos) on the 1-2 most impactful params.
Reuses fetched HL candles across grid combos via in-memory cache.
Runs all engines in parallel via ThreadPoolExecutor.
"""
from __future__ import annotations
import os
import sys
import json
import shutil
import subprocess
import urllib.request
import time
import statistics
import importlib
import importlib.util
import concurrent.futures
from pathlib import Path

GH_TOKEN = os.environ.get("GH_TOKEN", "")
WORK_ROOT = "/tmp/multica/sweep"

# ─── Parameter grids per engine ────────────────────────────────────────
# Each entry: {strategy_params_override_dict_for_this_run}
GRIDS = {
    "liq-heatmap-v1": [
        {"vol_spike_mult": 1.4, "min_cluster_pivots": 2},
        {"vol_spike_mult": 1.4, "min_cluster_pivots": 3},
        {"vol_spike_mult": 1.6, "min_cluster_pivots": 2},
        {"vol_spike_mult": 1.6, "min_cluster_pivots": 3},
        {"vol_spike_mult": 1.8, "min_cluster_pivots": 3},  # current default
        {"vol_spike_mult": 2.0, "min_cluster_pivots": 3},
    ],
    "funding-div-v1": [
        {"funding_threshold_hi":  0.00005, "funding_threshold_lo": -0.00005},
        {"funding_threshold_hi":  0.00010, "funding_threshold_lo": -0.00010},
        {"funding_threshold_hi":  0.00015, "funding_threshold_lo": -0.00010},
        {"funding_threshold_hi":  0.00020, "funding_threshold_lo": -0.00015},
        {"funding_threshold_hi":  0.00030, "funding_threshold_lo": -0.00020},  # current
    ],
    "vpin-v1": [
        {"vpin_threshold": 0.40, "extreme_proximity_pct": 0.010},
        {"vpin_threshold": 0.45, "extreme_proximity_pct": 0.010},
        {"vpin_threshold": 0.45, "extreme_proximity_pct": 0.015},
        {"vpin_threshold": 0.50, "extreme_proximity_pct": 0.010},
        {"vpin_threshold": 0.55, "extreme_proximity_pct": 0.005},  # current
        {"vpin_threshold": 0.55, "extreme_proximity_pct": 0.010},
    ],
    "venue-lag-v1": [
        {"min_venue_divergence_pct": 0.0010},
        {"min_venue_divergence_pct": 0.0015},
        {"min_venue_divergence_pct": 0.0020},
        {"min_venue_divergence_pct": 0.0025},  # current
        {"min_venue_divergence_pct": 0.0035},
        {"min_venue_divergence_pct": 0.0050},
    ],
    "tod-reversion-v1": [
        {"vwap_dev_threshold_pct": 0.002},
        {"vwap_dev_threshold_pct": 0.003},
        {"vwap_dev_threshold_pct": 0.004},  # current
        {"vwap_dev_threshold_pct": 0.005},
        {"vwap_dev_threshold_pct": 0.006},
    ],
    "wyckoff-v1": [
        {"range_lookback": 18, "spring_vol_mult": 1.2, "breach_max_pct": 0.005},
        {"range_lookback": 18, "spring_vol_mult": 1.3, "breach_max_pct": 0.008},
        {"range_lookback": 24, "spring_vol_mult": 1.2, "breach_max_pct": 0.005},
        {"range_lookback": 24, "spring_vol_mult": 1.3, "breach_max_pct": 0.008},
        {"range_lookback": 24, "spring_vol_mult": 1.5, "breach_max_pct": 0.005},  # current
        {"range_lookback": 30, "spring_vol_mult": 1.3, "breach_max_pct": 0.008},
    ],
    "avwap-mesh-v1": [
        {"mesh_band_pct": 0.003, "approach_max_pct": 0.005, "mesh_min_anchors": 2},
        {"mesh_band_pct": 0.005, "approach_max_pct": 0.005, "mesh_min_anchors": 2},
        {"mesh_band_pct": 0.005, "approach_max_pct": 0.010, "mesh_min_anchors": 2},  # current
        {"mesh_band_pct": 0.005, "approach_max_pct": 0.010, "mesh_min_anchors": 3},
        {"mesh_band_pct": 0.008, "approach_max_pct": 0.010, "mesh_min_anchors": 2},
        {"mesh_band_pct": 0.008, "approach_max_pct": 0.015, "mesh_min_anchors": 3},
    ],
}


def sh(cmd: str, cwd=None, check=True, timeout=120):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                        text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"sh fail: {r.stderr[:500]}")
    return r.stdout, r.stderr, r.returncode


def sweep_one(engine_name: str) -> dict:
    """Clone engine, sweep its grid, commit SWEEP_RESULTS.md."""
    print(f"[{engine_name}] start", flush=True)
    repo_path = f"{WORK_ROOT}/{engine_name}"
    if Path(repo_path).exists():
        shutil.rmtree(repo_path)
    sh(f"git clone -q https://{GH_TOKEN}@github.com/Dapperscyphozoa/{engine_name}.git {repo_path}",
       timeout=120)

    # Drop sweep_local.py into the repo to run the inner loop
    inner = '''
import sys, json, os, time
sys.path.insert(0, ".")
os.environ.setdefault("ENGINE_NAME", "sweep")
os.environ.setdefault("STATE_DIR", "/tmp/sweep-state")

from backtester import fetch_hl_candles, run_backtest
from engine.config import ACTIVE_UNIVERSE, TRADE_PARAMS, STRATEGY_PARAMS
from engine.signal_detector import evaluate_latest_bar

# Read grid from stdin
import sys
grid = json.loads(sys.argv[1])

# Cache candles per coin
print("== fetching candles ==", flush=True)
candle_cache = {}
for coin in ACTIVE_UNIVERSE:
    df = fetch_hl_candles(coin, days=60, interval="1h")
    candle_cache[coin] = df
    print(f"  {coin}: {len(df)} bars", flush=True)
    time.sleep(0.3)

# Sweep
print("== sweep ==", flush=True)
results = []
for combo_idx, combo in enumerate(grid):
    # Update STRATEGY_PARAMS in place
    STRATEGY_PARAMS.update(combo)
    coin_results = []
    for coin in ACTIVE_UNIVERSE:
        bars = candle_cache[coin]
        if len(bars) < 250: continue
        try:
            r = run_backtest(coin, bars, evaluate_latest_bar, TRADE_PARAMS)
            coin_results.append(r)
        except Exception as e:
            coin_results.append({"coin": coin, "n_trades": 0, "err": str(e)[:120]})
    # Aggregate
    total_trades = sum(r.get("n_trades", 0) for r in coin_results)
    valid = [r for r in coin_results if r.get("n_trades", 0) > 0]
    if total_trades > 0:
        agg_wr = round(100 * sum(r["n_trades"] * r["wr_pct"] / 100 for r in valid) / total_trades, 1)
        sum_r  = round(sum(r.get("sum_r", 0) for r in valid), 2)
        pfs    = [r["pf"] for r in valid if r.get("pf") not in (None, "inf")]
        med_pf = round(sorted(pfs)[len(pfs)//2], 2) if pfs else None
    else:
        agg_wr = None; sum_r = 0.0; med_pf = None
    coins_fired = sum(1 for r in valid)
    results.append({"combo": combo, "trades": total_trades, "wr": agg_wr,
                     "sum_r": sum_r, "pf": med_pf, "coins_fired": coins_fired})
    print(f"  combo {combo_idx+1}/{len(grid)}: n={total_trades} wr={agg_wr} sumR={sum_r} pf={med_pf}", flush=True)

print("== DONE ==", flush=True)
print("RESULTS_JSON:" + json.dumps(results))
'''
    Path(f"{repo_path}/_sweep_inner.py").write_text(inner)

    # Run the inner sweep
    grid_json = json.dumps(GRIDS[engine_name])
    print(f"[{engine_name}] running {len(GRIDS[engine_name])} grid combos...", flush=True)
    out, err, rc = sh(
        f"cd {repo_path} && python3 _sweep_inner.py '{grid_json}'",
        check=False, timeout=900,
    )
    if rc != 0:
        return {"engine": engine_name, "ok": False, "err": err[-1500:],
                "stdout_tail": out[-1500:]}

    # Parse RESULTS_JSON line
    results_line = next((l for l in out.split("\n")
                          if l.startswith("RESULTS_JSON:")), None)
    if not results_line:
        return {"engine": engine_name, "ok": False, "err": "no_results_line",
                "stdout_tail": out[-1500:]}
    results = json.loads(results_line[len("RESULTS_JSON:"):])

    # Pick winner — highest sum_r among combos with trades >= 20
    qualifying = [r for r in results if r.get("trades", 0) >= 20]
    if qualifying:
        winner = max(qualifying, key=lambda r: r.get("sum_r", 0))
    elif any(r.get("trades", 0) > 0 for r in results):
        firing = [r for r in results if r.get("trades", 0) > 0]
        winner = max(firing, key=lambda r: r.get("sum_r", 0))
    else:
        winner = None

    # Write SWEEP_RESULTS.md
    lines = [
        f"# {engine_name} — Parameter Sweep",
        "",
        f"_Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}_",
        "",
        "## Grid results",
        "",
        "| # | params | trades | WR% | sumR | medPF | coins |",
        "|---|---|--:|--:|--:|--:|--:|",
    ]
    for i, r in enumerate(results, 1):
        marker = " ★" if winner and r == winner else ""
        params_str = ", ".join(f"{k}={v}" for k, v in r["combo"].items())
        lines.append(
            f"| {i}{marker} | {params_str} | {r['trades']} | "
            f"{r['wr'] if r['wr'] is not None else '–'} | "
            f"{r['sum_r']} | {r['pf'] if r['pf'] is not None else '–'} | "
            f"{r['coins_fired']} |"
        )
    if winner:
        lines += ["", "## Winner", "",
                  f"```json",
                  json.dumps(winner["combo"], indent=2),
                  "```",
                  "",
                  f"**trades={winner['trades']} WR={winner['wr']}% sumR={winner['sum_r']} medPF={winner['pf']}**"]
    else:
        lines += ["", "## No-fire", "", "Every grid combo produced zero trades. Loosen thresholds further or revisit strategy."]
    body = "\n".join(lines) + "\n"
    Path(f"{repo_path}/SWEEP_RESULTS.md").write_text(body)

    # Cleanup helper file
    Path(f"{repo_path}/_sweep_inner.py").unlink()

    # Commit + push
    sh("git -c user.email=mca@cp.local -c user.name=multica add SWEEP_RESULTS.md",
       cwd=repo_path)
    sh('git -c user.email=mca@cp.local -c user.name=multica commit -q -m "sweep: parameter grid results"',
       cwd=repo_path, check=False)
    sh("git push -q origin main", cwd=repo_path, timeout=120, check=False)

    print(f"[{engine_name}] DONE winner={winner['combo'] if winner else 'none'}", flush=True)
    return {"engine": engine_name, "ok": True, "winner": winner, "all": results}


def main():
    if not GH_TOKEN:
        print("ERROR: GH_TOKEN unset", file=sys.stderr); sys.exit(1)
    Path(WORK_ROOT).mkdir(parents=True, exist_ok=True)

    out_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(sweep_one, name): name for name in GRIDS}
        for f in concurrent.futures.as_completed(futures):
            name = futures[f]
            try:
                out_results[name] = f.result()
            except Exception as e:
                out_results[name] = {"engine": name, "ok": False,
                                      "exception": str(e)[:300]}

    # Print master summary
    print("\n" + "=" * 78)
    print("SWEEP MASTER SUMMARY")
    print("=" * 78)
    for name in GRIDS:
        r = out_results.get(name, {})
        if not r.get("ok"):
            print(f"  {name:<22} FAIL: {(r.get('err') or r.get('exception') or '?')[:80]}")
            continue
        w = r.get("winner")
        if w:
            params_str = " ".join(f"{k}={v}" for k, v in w["combo"].items())
            print(f"  {name:<22} n={w['trades']:<4} WR={str(w['wr'])+'%':<7} "
                  f"sumR={str(w['sum_r']):<8} PF={str(w['pf']):<6} {params_str}")
        else:
            print(f"  {name:<22} NO FIRES across all combos")
    return out_results


if __name__ == "__main__":
    main()
