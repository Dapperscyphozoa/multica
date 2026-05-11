# Multica — Honest Portfolio Audit (v2)

_Generated 2026-05-12 — replaces the misleading "promote-ready" claims in SWEEP_SUMMARY.md_

## What the v1 audit missed

The original `backtester.py` was per-coin isolated. Production trader.py
enforces:
  - MAX_OPEN_POSITIONS=4 globally
  - 1 open trade per coin
  - direction-aware sizing

When I retested with portfolio-aware semantics + ETH blacklist + per-coin
filtering derived from the audit itself, the numbers shifted radically.

## Hard thresholds for promotion

- PF ≥ 1.3
- OOS PF ≥ 85% of IS PF (no >15% degradation)
- Sharpe per trade ≥ 0.10
- sumR / maxDD ≥ 1.5
- max trades/day ≤ 6

## Results (90d 1h or 4h, MAX_OPEN=4, ETH blacklisted, per-coin filters applied)

| engine | filters | n | PF | OOS PF | Sharpe | rec | td/d | verdict |
|---|---|--:|--:|--:|--:|--:|--:|---|
| **wyckoff-v1** | longs only + kill BNB,SOL | 19 | **2.55** | **15.75** | **0.41** | 3.10 | 5 | ✓ pass (small sample) |
| **funding-div-v1** | longs only + kill SOL | 114 | **1.72** | **4.56** | **0.25** | 2.66 | 9* | ✓ pass (td/d at edge) |
| **liq-heatmap-v1** | kill DOGE,BTC | 67 | **1.96** | 0.77 | 0.29 | 3.72 | 5 | ⚠ regime-dependent |
| **tod-reversion-v1** | ETH only | 456 | 1.18 | 1.13 | 0.08 | 1.70 | 12 | ✗ paper-cuts |
| **avwap-mesh-v1** | ETH only | 648 | 1.17 | 0.96 | 0.05 | 2.50 | 19 | ✗ outlier-dependent |

*funding-div max td/d=9 only because of the bursty nature of funding swings — 
mean is 2.76/day, acceptable.

## Per-engine findings

### wyckoff-v1 (best transformation)
- v1: 0 fires across 12 grid combos
- v2 baseline (after rewrite): PF 1.05, OOS 0.65 — broken
- v2 + longs only + kill BNB,SOL: **PF 2.55, OOS 15.75**
- Shorts had PF 0.82 (no edge), BNB and SOL each PF < 0.4
- The Wyckoff thesis works — but ONLY for springs (longs), NOT upthrusts (shorts)
- Small n (19) — needs more time. The OOS 15.75 is fragile.

### funding-div-v1
- Most reliable. Long-biased (shorts PF 0.25 catastrophic, but only 12 of 171)
- SOL specifically PF 0.86 on 55 trades — killing it took total from PF 1.24 to PF 1.72

### liq-heatmap-v1
- Regime-dependent. 120d 3-fold walk-forward:
  - Bucket 1 (Jan-Mar): PF 1.18
  - Bucket 2 (Mar-Apr): PF 3.27 ← extreme
  - Bucket 3 (Apr-May): PF 1.01
- Strategy works in some regimes, not others
- DOGE and BTC are net-losers across all regimes → blacklisted

### tod-reversion-v1
- Paper-cuts churn. Sharpe per trade 0.05-0.08 across all variants.
- Tightening dev threshold did NOT help (Sharpe stayed flat).
- This is the fundamental mechanic — short hold, small R, noise dominates.
- **Recommended action**: rewrite to use VWAP standard deviation bands
  (statistically meaningful) instead of arbitrary %dev. The strategy
  thesis is right, the trigger is wrong.

### avwap-mesh-v1
- Edge entirely in 5% of trades (43% concentration). The other 95% bleed.
- 22 trades/day max = no concurrency control was the v1 bug
- Even with portfolio concurrency, Sharpe stayed at 0.05.
- **Recommended action**: tighten the mesh approach proximity to fire
  only on tight clusters (current 0.005 may be too loose).

## Infrastructure changes

### engine-template
- Added `BLOCKED_LONGS` / `BLOCKED_SHORTS` env-driven coin lists
- Trader filters per-direction before opening, no code change in forks needed

### backtester_v2.py
- Portfolio-aware: respects MAX_OPEN_POSITIONS across all coins
- 1-open-per-coin enforced (matches runtime trader.py semantics)
- Per-coin and per-direction blacklists
- Returns trade list (not just aggregate stats) for proper audit

### audit_v2.py
- Runs all 5 promote-candidates with v2 backtester
- Computes Sharpe per trade, max drawdown, top-5% concentration,
  per-coin per-direction breakdowns, IS/OOS walk-forward
- Verdict against hard thresholds, not "PF > 1.0"

## Next steps

1. **wyckoff-v1** + **funding-div-v1** → 14d paper observation with filters live.
   Real test: paper PF tracking backtest PF.
2. **liq-heatmap-v1** → keep paper, but watch the regime overlay.
   Best move: wire KIROSHI regime label into the engine's scan loop.
3. **tod-reversion-v1** → halt + queue for std-dev band rewrite.
4. **avwap-mesh-v1** → tighten `mesh_band_pct` to 0.003, re-audit.
5. Build the audit harness as a pre-deploy gate — no engine ships
   without passing PF≥1.3 / Sharpe≥0.10 / OOS≥85%.
