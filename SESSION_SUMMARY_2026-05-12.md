# Multica Session 2026-05-12 — Confluence Architecture Deployed

## Killed
- **tod-reversion-v1**: PF 0.84 taker / 0.97 maker — structural loser at every fee model.
  Suspended on Render, deprecated in PM registry, marked suspended in watcher.

## Built and live

### 1. Dead-engine + cell health tracker (PM dashboard extension)
- Drift detection: active cells with PF < 1.30 (near demotion floor)
- Rehab candidates: demoted cells with shadow PF > 1.40, n_trades >= 5
- Stale alerts: 24h cold / 72h stale / 168h dead
- New PM /coverage endpoint aggregates cell state across all engines

### 2. Fee model fix (critical bug)
- TRADE_PARAMS["fee_bps_maker"] / ["fee_bps_taker"] were referenced but never defined
- Every paper close raised silent KeyError; fees recorded as zero
- All previous audit PFs were overstated by 12-46R per engine
- Added FEE_BPS_MAKER (-0.05) + FEE_BPS_TAKER (4.5) + MAKER_ONLY_MODE
- Builder code kickback infrastructure (BUILDER_KICKBACK_BPS)

### 3. Maker mode deployed across all 6 active engines
- MAKER_ONLY_MODE=1, MAKER_TP_ENABLED=1
- Saves 5-10bps per round-trip vs taker-only

### 4. Volatility regime overlay
- engine/regime.classify_vol_regime() ATR-percentile classifier
- Size modifiers: quiet 1.2x / normal 1.0x / noisy 0.75x
- Composable with price regime gate

### 5. Cross-engine portfolio netting (NET_DEDUP_MODE=size)
- PM /net_position/{coin} endpoint
- Trader hook reduces size 50% when net exposure already same direction
- ~30% expected fee reduction on overlapping signals

### 6. Macro confluence sensor
- PM /macro_state — DXY trend + BTC dominance + regime classification
- Current state: btc_season (BTC dom 58.3%, DXY flat)
- Engines scale size by per-(coin_class, direction) multiplier:
  - long_alt 0.6x / short_alt 1.3x / long_btc 1.3x / short_btc 0.7x
- Walk-forward audit demanded this missing dimension

### 7. Cross-engine ensemble voting (ENSEMBLE_CONFLUENCE_ENABLED)
- PM /confluence/{coin}/{direction} polls all engines for matching signals
- 2 engines = 1.3x, 3 = 1.6x, 4+ = 2.0x cap
- Companion to net dedup: dedup prevents bleed, confluence boosts on agreement

### 8. L2 orderbook imbalance (L2_IMBALANCE_ENABLED)
- hl_data.fetch_l2_book() + compute_book_imbalance()
- Aligned imbalance scaled to size mult or hard skip
- Catches book/signal divergence at place-time

## Walk-forward audit verdict
3-window cross-validation (within HL 210-day history limit):
- wyckoff-v1: sharpe +0.35 / **-0.06** / +0.30 — FAIL (middle window negative)
- liq-heatmap-v1: sharpe +0.01 / -0.01 / +0.15 — FAIL (only recent has edge)
- avwap-mesh-v1: sharpe -0.07 / +0.03 / +0.03 — FAIL (early window -80R)

None of the engines pass walk-forward as standalone. This is why the
confluence layer matters — engines aren't alpha alone, they're noisy signals
that need cross-validation by other systems.

## Engine size chain (final architecture)
```
base notional
  × cell_size_mult            (per-cell PF scaling, capped at PF_SIZE_CAP)
  × vol_regime_mult           (quiet 1.2 / normal 1.0 / noisy 0.75)
  × macro_confluence_mult     (per coin_class+direction lookup)
  × ensemble_confluence_mult  (per matching-direction engine count)
  × l2_imbalance_mult         (per book lean alignment)
  × net_dedup_mult            (0.5× if already exposed, else 1.0)
```

Net effect: an engine alone in a regime-misaligned, quiet-vol, book-opposed
setup with existing exposure trades at ~0.07× of base. An engine in
concordance with 3+ others, perfect macro alignment, noisy-vol, book-aligned,
fresh exposure trades at ~3.5× of base.

## Engines parked
- **liq-cascade-v1**: built and committed but failed initial backtest
  (PF 0.87 maker, n=63). Thesis needs L2 depth integration (not just funding+OI).
  Kept in repo for v2 with WebSocket L2 subscription.

## What's left to build
1. HL builder code registration (1.1bps kickback per taker leg) — manual ceremony
2. Cash-and-carry funding harvester (long spot + short perp) — needs HL spot
3. v2 liq-cascade with WebSocket L2 depth — proper liquidation cluster proxy
4. Walk-forward param optimization (max worst-window Sharpe objective)
5. Adverse-selection-aware maker queue model (50% fill rate baseline)
6. Multi-source historical (>210d) — Kaiko/CryptoCompare ingest for true regime testing

## Health
6/8 engines healthy at session end. liq-heatmap-v1 had 1 trade in last hour
(first under new fee+confluence regime). Watcher tracking activity per
engine + per cell.
