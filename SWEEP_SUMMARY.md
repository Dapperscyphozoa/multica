# Multica — Master Parameter Sweep Summary

_Generated 2026-05-11, 60d walk-forward on HL 1h candles, full engine universe._

## Headline

| engine | status | trades | WR | sumR | PF | best params |
|---|---|--:|--:|--:|--:|---|
| `tod-reversion-v1` | ✅ **promote** | 510 | 60.8% | +41.6 | 1.10 | `vwap_dev=0.005` |
| `avwap-mesh-v1` | ✅ **promote** | 549 | 23.3% | +89.98 | 1.15 | defaults (asymmetric R) |
| `liq-heatmap-v1` | ✅ **promote** | 80 | 33.8% | +26.96 | 1.42 | `vol_spike=1.4, min_pivots=2` |
| `vpin-v1` | ⚠ marginal | 43 | 34.9% | -1.73 | 0.67 | `vpin=0.20, prox=0.03` |
| `funding-div-v1` | ⏸ live-only | – | – | – | – | not backtestable (fetches live funding) |
| `venue-lag-v1` | ⏸ live-only | – | – | – | – | not backtestable (fetches live Binance/Bybit) |
| `wyckoff-v1` | 🛑 needs rewrite | 0 | – | – | – | 0 fires across 12 grid combos |

## Per-engine detail

Each engine has a `SWEEP_RESULTS.md` committed to its repo with the full grid.

### Promotable (3)

- **`tod-reversion-v1`** — Time-of-day MR around session VWAP. Most reliable engine: 60.8% WR, near-monotonic across grid (every dev threshold 0.002–0.006 produces 475–578 trades, 58–61% WR, +27 to +42 sumR). Robust strategy.
- **`avwap-mesh-v1`** — AVWAP confluence fader. Big asymmetric R distribution: 23% WR but R-multiple wins large. Current defaults are optimal of the 6 combos tested. Already has live paper fills.
- **`liq-heatmap-v1`** — Stop-hunt cluster fader. Sweep transformed it from PF 0.0 (default) to PF 1.42 by loosening `vol_spike` (1.8→1.4) and `min_pivots` (3→2). The tighter defaults were starving the signal of valid setups.

### Marginal (1)

- **`vpin-v1`** — Bar-aggregate VPIN proxy. Required vpin_threshold=0.20 (far below typical 0.55) and proximity=0.03 (instead of 0.005) to fire 43 trades, but result is PF 0.67. The aggressor-flow estimator from candle anatomy is too crude. Strategy needs either (a) real tick-level VPIN, or (b) different mechanic entirely.

### Live-only (2)

These strategies fetch live external data inside `evaluate_latest_bar()`. Backtest cannot historicise that data, so every historical bar sees today's funding / today's Binance price — useless.

- **`funding-div-v1`** — Pulls HL `metaAndAssetCtxs` for current funding rate.
- **`venue-lag-v1`** — Pulls live Binance + Bybit prices.

To backtest properly, both need:
1. A historical funding-rate / cross-venue archive (Binance API has historical funding; Bybit doesn't free)
2. A backtest-mode flag on the strategy that reads from the archive instead of live

For now, these run live-only — paper performance will show whether the live signal works regardless.

### Strategy-rewrite (1)

- **`wyckoff-v1`** — Range-detection requires range-width 1.5%–12%, ATR contraction 30% across 30 bars, volume spike, AND wick breach. Even at vol_mult=1.0 and breach=2%, every grid combo: **0 fires**. The trading-range filter is the killer — crypto rarely produces clean Wyckoff ranges on 1h frames. Options:
  - Move to 4h or daily timeframe (more range-bound bars)
  - Drop the ATR-contraction requirement entirely (let any sideways action count)
  - Rewrite as "wide-channel breakout fade" instead

## Applied to live services

Winning params pushed to Render env vars on the 3 promote-candidates + vpin (for monitoring). All engines redeploying.

## Next steps

1. **Monitor 7d** — once redeploys complete, watch paper accumulation
2. **Promote** the 3 winners to `canary` (5% capital) after 14d agreement between backtest PF and paper PF (±20%)
3. **Rewrite** wyckoff's range detection (4h timeframe + drop ATR contraction)
4. **Historical-data refactor** for funding-div + venue-lag, OR accept them as live-only and judge purely on paper performance
5. **vpin** — strategy review: either ship tick-level VPIN or replace mechanic
