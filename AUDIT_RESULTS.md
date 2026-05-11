# Multica Engine Audit — Honest Results

_Updated: 2026-05-11. Portfolio-sim audit with realistic gates._

## Audit gates

A strategy passes IF AND ONLY IF:
- n ≥ 30 trades (sample sufficient)
- **PF ≥ 1.20** (profit factor — total wins / total losses)
- **OOS PF ≥ 1.0** (last 33% trades still profitable)
- **No OOS collapse** (OOS PF ≥ 60% of IS PF)
- **Sharpe per trade ≥ 0.08** (risk-adjusted return)
- **Recovery ratio ≥ 1.5** (sumR / max_drawdown_R)
- max trades/day ≤ 12 (portfolio concurrency)

## Inversion test methodology

For every engine, run the strategy normally AND with `is_long` flipped.
The inverted strategy has the same entry timing and SL/TP magnitudes but
opposite direction. Read:

- NORMAL PF >> INVERTED PF → **real edge in normal direction**
- INVERTED PF >> NORMAL PF → **edge in WRONG direction — flip the code**
- Both ~= 1.0 → **noise, no edge in either direction**

## Results

### ✅ PASS (canary-ready)

**`tod-reversion-v1`** — Time-of-day VWAP mean reversion.

| metric | value |
|---|---|
| trades | 341 |
| WR | 63.3% |
| PF | 1.33 |
| Sharpe/trade | 0.130 |
| recovery (sumR/maxDD) | 2.69 |
| OOS PF | 1.25 |
| max trades/day | 10 |

Inversion test: NORMAL +68R / INVERTED -55R — clean 123R asymmetric edge.

Params: `STRATEGY_VWAP_DEV_THRESHOLD_PCT=0.005, MAX_OPEN_POSITIONS=3`.

---

**`liq-heatmap-v1`** — Stop-hunt cluster fader.

| metric | value |
|---|---|
| trades | 101 |
| WR | 33.7% |
| PF | 1.43 |
| Sharpe/trade | 0.135 |
| recovery | 2.81 |
| OOS PF | 1.33 |
| max trades/day | 8 |

Inversion test: NORMAL PF 1.43 / INVERTED PF 0.52 — clean directional edge.

Params: `STRATEGY_VOL_SPIKE_MULT=1.4, STRATEGY_MIN_CLUSTER_PIVOTS=2, MAX_OPEN_POSITIONS=3`.

### ⚠ MARGINAL (paper, reduced capital)

**`funding-div-v1`** — Funding rate divergence.

| metric | value |
|---|---|
| trades | 141 |
| WR | 41.8% |
| PF | 1.17 |
| Sharpe/trade | 0.072 |
| recovery | 0.69 |
| OOS PF | 1.53 |

Inversion test: NORMAL +14R / INVERTED -3R — edge present but small.

Issue: deep drawdowns relative to total profit (recovery ratio 0.69).
OOS PF 1.53 is encouraging but in-sample recovery fails.

Action: kept at `capital_fraction=0.04` (was 0.08), `MAX_OPEN_POSITIONS=2`. Will revisit after 14d paper if drawdowns moderate.

---

**`wyckoff-v1`** — Spring/Upthrust detector.

| metric | value |
|---|---|
| trades | 47 |
| WR | 29.8% |
| PF | 1.19 |
| **IS PF** | **1.34** |
| **OOS PF** | **0.79** ← OOS collapse |

Inversion test: NORMAL +6R / INVERTED -14R — direction is right, but
the edge doesn't persist out of sample. Curve-fit to in-sample.

Action: kept at `capital_fraction=0.03`, marked `needs_rewrite=True`.
Rewrite path: drop the volume confirmation (over-restrictive), use price-only
confirmation, or move to a different timeframe.

### 🛑 NOISE (demoted)

**`avwap-mesh-v1`** — Anchored VWAP confluence fader.

| metric | NORMAL | INVERTED |
|---|---|---|
| trades | 450 | 401 |
| PF | 1.02 | 1.07 |

Inversion test: both directions ~1.0 — pure noise. No edge in either direction.

Action: `lifecycle_stage=demoted, capital_fraction=0.0`. Service kept running for any open trades to close out, then will be suspended.

---

**`vpin-v1`** — Candle-anatomy aggressor proxy.

Already deprecated/suspended. Real VPIN needs a websocket trade-tape collector
(~2-day build). Repo retained at `Dapperscyphozoa/vpin-v1` with `DEPRECATED.md`.

---

**`venue-lag-v1`** — Cross-venue arbitrage.

Cannot be properly audited from historical data (live Binance/Bybit fetching
inside detector). Kept live for paper observation; assess on real paper PF only.

## Honest scorecard

| count | category |
|---|---|
| 2 | canary-ready (passed full audit) |
| 1 | marginal (edge present but drawdown-heavy) |
| 1 | OOS-overfit (needs rewrite) |
| 1 | pure noise (demoted) |
| 1 | not auditable (live-only, paper-judge) |
| 1 | deprecated (data infrastructure missing) |

## Audit framework files

- `deep_search.py` — candle cache builder (60d/90d, 1h+4h, funding)
- `deep_search_v2.py` — portfolio simulator + audit gates (run this!)
- `sweep_with_gates.py` — wide parameter grid runner

## What I got wrong before this audit

1. **No concurrency control in backtest.** I let strategies fire 20 trades/day
   on 8 coins. Real account would blow up. Fixed: portfolio simulator now caps
   at 3 concurrent globally, 1 per coin.

2. **No inversion test.** I missed that avwap-mesh fires equally in both
   directions — pure noise dressed up as PF 1.15 by lucky asymmetric R.

3. **No OOS / IS split.** I shipped wyckoff v2 with PF 1.79 — its OOS PF was
   0.79. Pure curve fit. Always 67/33 IS/OOS now.

4. **PF as sole metric.** PF 1.15 with Sharpe 0.018 and recovery 0.51 isn't
   "edge" — it's noise that happens to be slightly positive. Need Sharpe AND
   recovery AND OOS persistence.

5. **Loose default thresholds.** First-pass params produced "trades exist + PF
   > 1.0", which I called promotable. Real promotion needs PF ≥ 1.20 with
   Sharpe ≥ 0.08 AND recovery ≥ 1.5 AND oos_pf ≥ 1.0.
