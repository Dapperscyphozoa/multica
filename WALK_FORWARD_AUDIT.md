# Walk-Forward 3-Window Audit

Three non-overlapping 60-day windows within the 210-day HL hourly history limit:
- **early**:  180-120d ago
- **middle**: 120-60d ago
- **recent**:  60-0d ago (the slice our previous audit was tuned on)

All numbers fee-adjusted (maker mode: -0.05bps entry, -0.05bps TP, 4.5bps SL).

## Results (Sharpe per trade, fee-adjusted)

| Engine | Early | Middle | Recent | Verdict |
|---|--:|--:|--:|---|
| wyckoff-v1 | +0.35 (n=11) | **-0.06** (n=19) | +0.30 (n=15) | FAIL |
| liq-heatmap-v1 | +0.01 (n=11) | -0.01 (n=3) | +0.15 (n=14) | FAIL |
| avwap-mesh-v1 | **-0.07** (n=537) | +0.03 (n=533) | +0.03 (n=497) | FAIL |

## What this exposes

**None of the 3 tested engines pass.** All show non-trivial variance across
windows. The "edge" we measured in the 90-day audit was carried by the
recent window. Tuning to one regime produced params that don't generalize.

## Critical findings

1. **wyckoff-v1 middle-window failure (-0.06)**: thesis is range-specific
   and the middle window had a different range structure. Strategy works
   when ranges resolve via spring, fails when they break via continuation.
   Need a regime-fit gate — but the price-regime classifier already gives
   that and it still failed. Implies thesis itself is fragile.

2. **liq-heatmap-v1 sample too small**: only 3 trades in middle window.
   Can't conclude. Likely vol-spike events clustered in early + recent.

3. **avwap-mesh-v1 early-window catastrophe (-80R on 537 trades)**:
   the most diagnostic. Strategy fires constantly (~500 trades per
   60d window) but the early window must have been a strong trending
   regime where mesh-fade is structurally wrong. 4 cents of edge per
   trade in middle/recent isn't enough to overcome the early loss.

## What to do

- Either retune params with explicit walk-forward objective (max worst-window
  Sharpe, not average) — slow, requires param-grid + holdout
- Or accept that current engines have ~30-50d "decay time" before regime
  shifts kill them, and build a meta-monitor that auto-deprecates engines
  when their rolling Sharpe goes negative for 2 weeks
- Or add the missing dimension that explains why each window differs and
  gate on that

Most likely the correct answer is: each window had a different macro
regime that none of the current strategies have a sensor for. The
strategies are reading the price action of each coin individually but not
the macro state (BTC dominance, USDT supply, Fed policy, etc).

## Pivot

This is more impactful than continuing dimensional sweeps. Adding macro
sensors (DXY trend, BTC dominance, total crypto market cap regime) as
a confluence layer above the engines.
