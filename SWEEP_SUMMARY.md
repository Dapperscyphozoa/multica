# Multica — Master Status Summary

_Updated: 2026-05-11 (post-v2 deployments)_

## Headline

| engine | status | trades | WR | sumR | PF | notes |
|---|---|--:|--:|--:|--:|---|
| `tod-reversion-v1` | ✅ **promote-ready** | 510 | 60.8% | +41.6  | 1.10 | best WR; robust across grid |
| `avwap-mesh-v1`    | ✅ **promote-ready** | 549 | 23.3% | +89.98 | 1.15 | asymmetric R distribution |
| `liq-heatmap-v1`   | ✅ **promote-ready** |  80 | 33.8% | +26.96 | 1.42 | tuned `vol_spike=1.4, min_pivots=2` |
| `wyckoff-v1`       | ✅ **promote-ready** v2 |  46 | 34.8% | +12.10 | 1.79 | rewritten: 4h, restricted universe |
| `funding-div-v1`   | ✅ **promote-ready** v2 | 119 | 42.9% | +11.11 | 1.13 | historicised via HL fundingHistory |
| `venue-lag-v1`     | ⏸ live-only | – | – | – | – | needs historical cross-venue archive |
| `vpin-v1`          | 🛑 **deprecated** | – | – | – | – | suspended; needs websocket trade-tape |

## What changed this pass

### Promoted (from "needs rewrite" / "not backtestable"):

**`wyckoff-v1` v2** — Strategy rewrite. From **0 fires** (v1) to PF 1.79.
- Timeframe 1h → 4h (cleaner range structure on crypto)
- Percentile-based range detection with in-band tolerance
- Dropped ATR-contraction filter (the silent killer)
- Restricted universe: BTC/ETH/SOL/LINK/AVAX only
- Defaults rebuilt: `range_lookback=18, spring_vol_mult=1.3, breach_max_pct=0.015`
- 90d backtest: 46 trades, 34.8% WR, sumR +12.10, medPF 1.79
  - ETH +5.02R / AVAX +5.68R / LINK +4.75R led; SOL -4.41R worst

**`funding-div-v1` v2** — Strategy now backtestable.
- Added `_funding_for_bar()` — reads `df['funding']` in backtest mode,
  falls back to live HL `metaAndAssetCtxs` in production
- Built `backtester_with_funding.py` — paginates HL `fundingHistory`
  (500-sample window) and asof-joins to candle bars
- Fixed unit bug — funding is decimal, prior defaults were ~20x typical max
- New default thresholds: `funding_threshold_hi=1.5e-5, lo=-1.5e-5`
- 60d backtest: 119 trades, 42.9% WR, sumR +11.11, medPF 1.13
  - DOGE +9.60R / BTC +3.89R / XRP +3.28R led; ETH -5.43R worst

### Deprecated:

**`vpin-v1`** — Render service suspended.
- Bar-aggregate VPIN proxy is fundamentally too crude
- HL `recentTrades` only returns last 10 trades (not historicisable)
- A real VPIN engine needs websocket trade-tape collector (~2-day build)
- PM registry updated: `lifecycle_stage: demoted, capital_fraction: 0.0, deprecated: True`
- Repo retained with `DEPRECATED.md` for future revisit

### Remaining live-only:

**`venue-lag-v1`** — same backtest limitation as funding-div had.
- Reads live Binance + Bybit prices inside detector
- Could be historicised via Binance's free historical price API
- Lower priority than the 5 now-promotable engines — judge on paper for now

## Promotion path (next 14 days)

For the 5 promote-ready engines:

1. Today–Day 7: paper-mode forward walk. Compare per-day paper PF to backtest PF.
2. Day 7: review paper performance. If paper PF within 20% of backtest, queue for canary.
3. Day 14: promote to canary stage (`lifecycle_stage="canary"`, `PM_CHECK_ENABLED=1`).
   PM will allocate 5% capital fraction to canary engines.

Engines to monitor first: `tod-reversion-v1` (highest WR), `avwap-mesh-v1`
(already has 2 paper fills).
