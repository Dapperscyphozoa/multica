# Multica — Regime Overlay Audit

_Generated 2026-05-12 — 120d portfolio backtest, per-bar regime classification_

## What the regime gate exposes

Each engine has a **regime affinity**. Trading in the wrong regime is where
the losses are coming from. The audit gated each engine's trade list by the
regime label at fire-time.

## Per-regime PF breakdown (60d window, 7-coin universe, ETH blacklisted)

| Engine | trend_up | trend_down | range | chop | Action |
|---|---|---|---|---|---|
| **wyckoff-v1** | — | PF 0.71 (10) | **PF 3.79 (5)** | — | Block trend_down |
| **funding-div-v1** | PF 2.0 (6) | PF 1.47 (84) | PF 1.18 (53) | PF 0 (1) | Block chop (defensive) |
| **liq-heatmap-v1** | PF 1.80 (7) | PF 1.62 (22) | PF 1.59 (59) | — | **No block** — robust everywhere |
| **tod-reversion-v1** | PF 1.29 (196) | **PF 0.69 (251)** | PF 1.15 (301) | PF 1.5 (7) | **Block trend_down (-40R!)** |
| **avwap-mesh-v1** | **PF 0.78 (146)** | PF 1.40 (179) | PF 1.13 (547) | PF 1.97 (29) | **Block trend_up (-27R)** |

## Projected post-gate impact

| Engine | Overall PF before | After gate | sumR before | After gate | Change |
|---|--:|--:|--:|--:|---|
| wyckoff-v1 | 1.02 | **3.79** | +3.3 | +5.6 | +70% sumR, +273% PF |
| funding-div-v1 | 1.36 | 1.37 | +27.9 | +27.9 | marginal — chop trade volume tiny |
| liq-heatmap-v1 | 1.61 | 1.61 | +33.5 | +33.5 | no gate (works in all regimes) |
| tod-reversion-v1 | **1.00** | **1.22** | -1.4 | **+39.2** | **+40.6R recovered** |
| avwap-mesh-v1 | 1.14 | 1.22 | +103.9 | **+131.1** | **+27.2R recovered** |

## Two engines were resurrected by regime gating

- **tod-reversion-v1**: Was a paper-cuts churn machine (overall PF 1.00).
  The 251 trades it took in trend_down accounted for the entire bleed (-40.69R).
  Block that one regime → engine becomes net +39R. **PF 1.22 still
  below promote threshold (1.30) but now WITHOUT the structural bleed.**

- **avwap-mesh-v1**: Was outlier-dependent (43% wins from top 5%).
  Block trend_up (where it took -27R on 146 trades) → engine PF 1.22,
  sumR jumps +27R. Trend-mesh fade fundamentally can't work in directional
  markets, the audit confirms this.

## Wyckoff finally makes sense

5 trades in range regime → PF 3.79. That's the Wyckoff thesis: spring
in a trading range = accumulation end, ride the breakout. Outside of
ranges it's noise. The sample is tiny (5 trades over 60d) but the
mechanic IS structurally regime-locked.

## Implementation

`engine/regime.py` in engine-template — local price-action classifier:
- SMA200 anchor (above = uptrend bias)
- 20-bar slope > +1% / < -1% for momentum direction
- ADX(14): >20 = trending, <15 = chop, else range
- Matches KIROSHI's current call on BTC (range, conf 0.84)

`engine/trader.py` calls `regime.classify_latest_bar(latest_250_candles)`
before opening any trade. If label in `BLOCKED_REGIMES` env, skip.

Local computation — no KIROSHI dependency (it only tracks BTC/ETH/SOL).
Every engine classifies per coin per bar independently.

## Live env vars set

| Engine | BLOCKED_REGIMES |
|---|---|
| tod-reversion-v1 | `trend_down` |
| avwap-mesh-v1 | `trend_up` |
| wyckoff-v1 | `trend_down` |
| funding-div-v1 | `chop` |
| liq-heatmap-v1 | (none — works everywhere) |
| venue-lag-v1 | `chop` (defensive default — not backtested) |

## What's next

This was one dimension (regime). We've also identified but not tested:
- **Volatility gate** — most failures clustered on high-ATR memes (DOGE/XRP)
- **MAX_HOLD_BARS** — tod-reversion exits too fast; vary 6 → 12 → 24
- **SL/TP calibration** — every engine on default 2x/4x ATR; strategy-specific likely better
- **Partial exits / trailing** — avwap-mesh's 43% outlier concentration says wins want to run further
- **Session filter** — Asian / London / NY hour-of-day
- **Correlation overlay** — don't fire long-BTC + long-SOL simultaneously
- **Confidence-weighted sizing** — strong signals get more size than marginal ones
