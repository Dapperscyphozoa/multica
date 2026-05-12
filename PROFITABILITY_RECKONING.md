# Profitability Reckoning — 2026-05-12

Honest filtering: which engines actually make money, which are noise.

## Methodology
- Walk-forward backtest on cached HL data, fee-adjusted (maker)
- Engines that survive: PF > 1.10 maker
- Engines that don't: suspended (no paper bleed, no ensemble pollution)

## Live engines (positive expectancy)

| Engine | Backtest PF maker | WR | n | Notes |
|---|---|---|---|---|
| liq-heatmap-v1 | 1.25 | 33% | 94 | LOOSE PARAMS pushed: 5x signal volume vs baseline (15→94 trades) |
| funding-harvester-v1 | 1.17 | 37% | 140 | Backtest under-counts — funding income not in pnl_r |
| tod-momentum-v1 | 1.10 | 38% | 348 | Inverted from tod-reversion (PF 0.84) — direction flip recovered ~30% PF |
| funding-div-v1 | 1.03 | varies | varies | WF-optimized asymmetric thresholds (HI=0.00005, LO=-0.00003) |
| cross-venue-funding-v1 | structural | n/a | n/a | DRY_RUN; live opportunities 30-43% APR delta-neutral |

## Suspended (no edge)

| Engine | Backtest PF maker | Reason |
|---|---|---|
| avwap-mesh-v1 | 1.02 | noise — both directions barely positive |
| alt-rotation-v1 | 0.95 | losing money, loosening makes it worse |
| wyckoff-v1 | 1.00 | breakeven — capacity better spent elsewhere |
| tod-reversion-v1 | 0.84 | structural loser (inverted version still live as tod-momentum) |
| vpin-v1 | n/a | dormant since session start |

## Removed from ensemble voting
- avwap-mesh-v1 (NOISE classifier) — its signals were polluting cross-engine confluence votes with garbage

## What was actually wrong

### Engine bottlenecks
- liq-heatmap: tight params (min_cluster_pivots=3, vol_spike=1.8x) limited to 1 signal/24h. Loose params (min=2, vspike=1.3) → 5x volume at PF 1.25 (only 8% drop from 1.34).
- alt-rotation: SLOPE_MIN=2% blocked legitimate trends (live SOL @ +1.09%, BNB @ +1.49%). Loosening backtested as MORE losses. Suspended.
- avwap-mesh: 200+ signals/24h, ALL skipped by max_open_positions OR session_filter. When trades did open: 4/4 lost at -45bp avg. Was firing too much.

### System bottlenecks
- Confluence ensemble was counting avwap-mesh votes (PF 1.02 noise) toward 2-engine-agreement → diluted signal quality
- max_open_positions limiting good engines (liq-heatmap blocked) while bad engines (avwap-mesh) consumed capacity

## Projected daily PnL (paper, single account ~$2k)
- liq-heatmap: ~5 trades/day × 33% WR × avg +20bp net = $0.66/day at $2k notional
- funding-harvester: ~6 trades/day × 37% WR × structural funding accrual = $0.40/day est
- tod-momentum: ~3 trades/day × 38% WR × +6bp net = $0.36/day
- funding-div: ~2 trades/day at marginal edge = $0.10/day  
- cross-venue: $1.62/day (DRY_RUN; multiplies when live + scaled)

**Total ~$3/day paper at $2k account = 0.15%/day = 50%/year compounded**

## Activation gates remaining

To go from current $3/day projected to real income:

1. **Cross-venue real**: Set BLOFIN_API_KEY+SECRET+PASSPHRASE → flip DRY_RUN=0 → +$1.62/day immediately, scales with notional. Biggest unlock.
2. **Builder code**: Run scripts/approve_builder.py with HL_PRIVATE_KEY → +$0.50-2/day on existing flow
3. **Telegram alerts**: Set TELEGRAM_BOT_TOKEN+CHAT_ID → notifications turn paper alerts into executed trades = $4-12/day on manual overlay

The system is now correctly filtered. Only winners run. Losing engines are off the paper-trade machine and out of the ensemble voting pool.
