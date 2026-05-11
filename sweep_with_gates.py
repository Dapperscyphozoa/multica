"""
sweep_with_gates.py — find params that pass real audit gates across all 7 engines.

For each engine:
  1. Build wide grid (10-30 combos)
  2. For each combo: test NORMAL direction + INVERTED direction
  3. Apply audit gates → return passing combos
  4. Winner = best (sumR × sharpe) among passers, else best PF among non-passers

Uses cached candles (deep_search.build_cache must have run).
"""
import os, sys, time, json, subprocess
sys.path.insert(0, '/tmp/multica-fresh')
from deep_search import load_candles, load_funding, run_combo

GH_TOKEN = "${GH_TOKEN}"


# ─── GRIDS — wide search ─────────────────────────────────────────────
def grid_liq_heatmap():
    """3 axes: vol_spike × min_pivots × sweep_threshold"""
    combos = []
    for vol in [1.2, 1.4, 1.6, 1.8]:
        for pivots in [2, 3]:
            for sweep in [0.001, 0.002, 0.003]:
                combos.append({
                    "STRATEGY_VOL_SPIKE_MULT": vol,
                    "STRATEGY_MIN_CLUSTER_PIVOTS": pivots,
                    "STRATEGY_SWEEP_THRESHOLD_PCT": sweep,
                })
    return combos


def grid_funding_div():
    """asymmetric thresholds — perps tend positive"""
    combos = []
    for hi in [0.5e-5, 1.0e-5, 1.5e-5, 2.0e-5, 3.0e-5]:
        for lo_mult in [0.7, 1.0, 1.5]:  # asymmetric: how much wider on short side
            combos.append({
                "STRATEGY_FUNDING_THRESHOLD_HI": hi,
                "STRATEGY_FUNDING_THRESHOLD_LO": -hi * lo_mult,
            })
    return combos


def grid_vpin():
    combos = []
    for thresh in [0.10, 0.20, 0.30, 0.40, 0.55]:
        for prox in [0.005, 0.015, 0.030, 0.050]:
            combos.append({
                "STRATEGY_VPIN_THRESHOLD": thresh,
                "STRATEGY_EXTREME_PROXIMITY_PCT": prox,
            })
    return combos


def grid_venue_lag():
    return [{"STRATEGY_MIN_VENUE_DIVERGENCE_PCT": v}
            for v in [0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005]]


def grid_tod_reversion():
    return [{"STRATEGY_VWAP_DEV_THRESHOLD_PCT": v}
            for v in [0.002, 0.003, 0.004, 0.005, 0.006, 0.008, 0.010]]


def grid_wyckoff():
    """4h timeframe, vary range detection params"""
    combos = []
    for lookback in [18, 24, 30]:
        for vol_mult in [1.0, 1.3, 1.5]:
            for breach in [0.010, 0.015, 0.020]:
                combos.append({
                    "STRATEGY_TIMEFRAME": "4h",
                    "STRATEGY_RANGE_LOOKBACK": lookback,
                    "STRATEGY_SPRING_VOL_MULT": vol_mult,
                    "STRATEGY_BREACH_MAX_PCT": breach,
                    "STRATEGY_RANGE_MIN_WIDTH_PCT": 0.01,
                    "STRATEGY_RANGE_MAX_WIDTH_PCT": 0.10,
                    "STRATEGY_RANGE_MIN_BARS_IN_BAND": 16,
                })
    return combos


def grid_avwap_mesh():
    combos = []
    for band in [0.003, 0.005, 0.008]:
        for approach in [0.005, 0.010, 0.015]:
            for anchors in [2, 3]:
                combos.append({
                    "STRATEGY_MESH_BAND_PCT": band,
                    "STRATEGY_APPROACH_MAX_PCT": approach,
                    "STRATEGY_MESH_MIN_ANCHORS": anchors,
                })
    return combos


ENGINES = {
    "liq-heatmap-v1":   {"grid": grid_liq_heatmap(),  "interval": "1h", "funding": False},
    "funding-div-v1":   {"grid": grid_funding_div(),  "interval": "1h", "funding": True},
    "vpin-v1":          {"grid": grid_vpin(),          "interval": "1h", "funding": False},
    "venue-lag-v1":     {"grid": grid_venue_lag(),    "interval": "1h", "funding": False},
    "tod-reversion-v1": {"grid": grid_tod_reversion(),"interval": "1h", "funding": False},
    "wyckoff-v1":       {"grid": grid_wyckoff(),       "interval": "4h", "funding": False},
    "avwap-mesh-v1":    {"grid": grid_avwap_mesh(),   "interval": "1h", "funding": False},
}


def fmt_audit(a):
    if not a: return "n/a"
    issues = ",".join(a.get('issues', []))[:60]
    pass_str = "PASS" if a.get('pass') else f"FAIL[{issues}]"
    return (f"n={a['n']:<4} WR={a['wr']*100:5.1f}% PF={a['pf']:5.2f} "
            f"sumR={a['sumR']:+7.2f} Sh={a['sharpe']:.3f} dd={a['max_dd']:.1f} "
            f"rec={a['recovery']:.2f} mxD={a['max_per_day']} "
            f"oosPF={a.get('oos_pf') or 0:.2f} {pass_str}")


def sweep_engine(name: str, spec: dict, candles, funding):
    print(f"\n{'='*88}")
    print(f"SWEEP: {name} — {len(spec['grid'])} combos × 2 directions = {len(spec['grid'])*2} runs")
    print(f"{'='*88}")
    # Clone engine
    work = f"/tmp/sweep-deep-{name}"
    subprocess.run(f"rm -rf {work} && git clone -q https://{GH_TOKEN}@github.com/Dapperscyphozoa/{name}.git {work}",
                   shell=True, check=True)
    fund_arg = funding if spec['funding'] else None
    
    rows = []
    for idx, params in enumerate(spec['grid'], 1):
        # NORMAL
        try:
            a_norm = run_combo(work, params, candles, funding=fund_arg,
                                interval=spec['interval'], invert=False)
        except Exception as e:
            print(f"  combo {idx} NORMAL: ERR {str(e)[:80]}"); continue
        # INVERTED
        try:
            a_inv = run_combo(work, params, candles, funding=fund_arg,
                                interval=spec['interval'], invert=True)
        except Exception as e:
            print(f"  combo {idx} INVERT: ERR {str(e)[:80]}"); a_inv = None
        rows.append({'params': params, 'normal': a_norm, 'inverted': a_inv})
        # Print compact
        n_str = fmt_audit(a_norm) if a_norm else "no trades"
        i_str = fmt_audit(a_inv) if a_inv else "no trades"
        params_str = ",".join(f"{k.replace('STRATEGY_','').lower()}={v}" for k,v in params.items())[:60]
        print(f"  [{idx:>2}] {params_str}")
        print(f"        N: {n_str}")
        print(f"        I: {i_str}")
    
    # Find best PASSING combo (consider both directions)
    candidates = []
    for r in rows:
        if r['normal'] and r['normal'].get('pass'):
            candidates.append({'params': r['params'], 'direction': 'normal', 'audit': r['normal']})
        if r['inverted'] and r['inverted'].get('pass'):
            candidates.append({'params': r['params'], 'direction': 'inverted', 'audit': r['inverted']})
    
    print(f"\n  PASSING combos: {len(candidates)}/{len(rows)*2}")
    
    if candidates:
        # Score by sumR × sharpe (reward consistency)
        winner = max(candidates, key=lambda c: c['audit']['sumR'] * max(c['audit']['sharpe'], 0))
        print(f"  WINNER ({winner['direction']}): {winner['params']}")
        print(f"          {fmt_audit(winner['audit'])}")
        return {'engine': name, 'winner': winner, 'all_passing': candidates, 'n_combos': len(rows)}
    
    # No passers — show best-failing for diagnosis
    best_n = max([r for r in rows if r['normal']],
                  key=lambda r: r['normal']['pf'], default=None)
    best_i = max([r for r in rows if r['inverted']],
                  key=lambda r: r['inverted']['pf'], default=None)
    print(f"  NO COMBOS PASSED")
    if best_n: print(f"  Best NORMAL:   {fmt_audit(best_n['normal'])}  {best_n['params']}")
    if best_i: print(f"  Best INVERTED: {fmt_audit(best_i['inverted'])}  {best_i['params']}")
    return {'engine': name, 'winner': None, 'best_normal': best_n, 'best_inverted': best_i, 'n_combos': len(rows)}


def main(only=None):
    print("=== loading candle cache ===")
    candles_1h = load_candles("1h", 90)
    candles_4h = load_candles("4h", 90)
    funding = load_funding(90)
    print(f"  1h: {len(candles_1h)} coins  4h: {len(candles_4h)} coins  funding: {len(funding)} coins")
    
    results = {}
    for name, spec in ENGINES.items():
        if only and name not in only: continue
        try:
            c = candles_4h if spec['interval'] == '4h' else candles_1h
            results[name] = sweep_engine(name, spec, c, funding)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"  {name}: SWEEP FAILED — {e}")
    
    print(f"\n\n{'='*88}")
    print("MASTER SUMMARY")
    print(f"{'='*88}")
    for name, r in results.items():
        w = r.get('winner')
        if w:
            a = w['audit']
            params_s = ",".join(f"{k.replace('STRATEGY_','')}={v}" for k,v in w['params'].items())[:80]
            print(f"  ✓ {name:<22} [{w['direction']:>8}] PF={a['pf']:.2f} Sh={a['sharpe']:.3f} sumR={a['sumR']:+.1f} rec={a['recovery']:.1f}")
            print(f"      {params_s}")
        else:
            print(f"  ✗ {name:<22} no combos passed all gates")
    
    return results


if __name__ == "__main__":
    import sys
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    main(only=only)
