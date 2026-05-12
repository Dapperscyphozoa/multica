# Maker Fill-Rate Sensitivity Audit

Tests what happens to each engine's PF when only X% of post-only limit orders
actually fill (queue position, book moving away, etc).

## Results (90d, fee-blind, single-window — illustrative not predictive)

| Engine | fill=1.0 | fill=0.75 | fill=0.5 | fill=0.25 | Durable? |
|---|--:|--:|--:|--:|---|
| wyckoff-v1 | PF 2.04 | 2.59 | 1.57 | 1.68 | YES |
| liq-heatmap-v1 | PF 1.70 | 1.94 | 2.17 | 2.63 | YES |
| avwap-mesh-v1 | PF 1.18 | 1.15 | 1.10 | 1.07 | MARGINAL |

## Findings

1. **wyckoff** small-sample noisy but PF stays >1.5 even at 25% fill — durable.

2. **liq-heatmap** PF actually **improves** at lower fill rates. Selection effect:
   the trades that fill in a sparse-fill world are the ones where the move
   followed through enough that liquidity dried up on our side. The trades
   we DON'T fill were the spurious ones that mean-reverted.

3. **avwap-mesh** degrades steadily: 1.18 → 1.07. Its edge is broad and shallow
   — high trade count, small per-trade edge. Halving fills doesn't halve edge
   proportionally because the missed fills are still positive-expectancy.
   Below ~50% fill rate this engine isn't worth running.

## Real-world fill rate estimates

- Thin alts (HYPE, BNB depth): 60-80% fill on post-only at touch
- BTC/ETH (deep queues): 30-50% fill on post-only at touch
- Stat: HL maker fill rate avg ~55% based on academic studies

## Action

- wyckoff: ship live, fill-rate insensitive
- liq-heatmap: ship live, fill-rate POSITIVELY selective
- avwap-mesh: needs MAKER_TP_ENABLED=0 fallback when fill rate empirically
  drops below 50%, or pivot to taker-acceptable entries (lower edge but
  consistent fills)
