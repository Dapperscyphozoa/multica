# Fee Model Audit — Critical Finding

## The bug

`TRADE_PARAMS["fee_bps_maker"]` and `TRADE_PARAMS["fee_bps_taker"]` were
referenced in trader.py paper-close path but **never defined in config.py**.
Result: every paper trade close silently raised KeyError, fees recorded as
zero. Every audit PF reported was overstated.

## Real fee-adjusted truth (90-day backtest, post-regime-gate)

| Engine | Fee-blind PF | Taker+Taker | Maker+Maker | + Builder |
|---|--:|--:|--:|--:|
| wyckoff-v1 | 2.38 | 2.19 | 2.30 | 2.32 |
| funding-div-v1 | 2.06 | 1.86 | 1.98 | 2.00 |
| liq-heatmap-v1 | 1.68 | 1.55 | 1.63 | 1.64 |
| **tod-reversion-v1** | 1.01 | **0.84** | **0.97** | 0.98 |
| **avwap-mesh-v1** | 1.18 | **1.10** | 1.14 | 1.15 |

## Key takeaways

1. **tod-reversion-v1** is a structural loser at any fee model. PF 0.84 taker,
   PF 0.97 maker. Strategy mechanics produce too many low-edge trades that
   can't survive fee drag. **Deprecate or rewrite.**

2. **avwap-mesh-v1** at maker mode = PF 1.14, sumR +85R. Taker mode =
   PF 1.10, sumR +59R. Fee model alone is 26R difference. Genuinely positive
   only with maker.

3. **High-turnover engines** (tod-reversion 531 trades, avwap-mesh 767 trades)
   pay the heaviest fee tax. Per-trade -0.30R from taker, -0.13R from maker
   round-trip.

4. **Low-turnover engines** (wyckoff 23 trades) barely move under any fee
   model. PF 2.32+ regardless. Most fee-resilient class.

## Action taken

- Added FEE_BPS_MAKER (-0.05) and FEE_BPS_TAKER (4.5) to TRADE_PARAMS
- Added MAKER_ONLY_MODE / MAKER_TP_ENABLED env flags
- Added HL_BUILDER_CODE + BUILDER_KICKBACK_BPS for builder code kickback
- Deployed MAKER_ONLY_MODE=1 + MAKER_TP_ENABLED=1 to all 6 engines

## Open

- Live verification: maker fill rate < 100% in reality. Live PnL will tell
  us the realistic capture rate. Watch /pnl per engine vs paper.
- Builder code: not yet registered. ~1.1bps kickback per taker leg available
  if we register a builder code with HL and route through it.
- Adverse selection cost on maker fills: real risk, not modelled.
