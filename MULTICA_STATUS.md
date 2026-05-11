# Multica — Stack Status

_Generated automatically by orchestrator. Re-run `multica_orchestrator.py` to refresh._

## Multica engines (7)

| Engine | Repo | Service | URL | Class | Stage |
|---|---|---|---|---|---|
| `liq-heatmap-v1` | `Dapperscyphozoa/liq-heatmap-v1` | `srv-d80ro1gg4nts738u4oqg` | https://liq-heatmap-v1.onrender.com | stop_hunt_fader | paper |
| `funding-div-v1` | `Dapperscyphozoa/funding-div-v1` | `srv-d80ro1jeo5us73fqu2r0` | https://funding-div-v1.onrender.com | funding_divergence | paper |
| `vpin-v1` | `Dapperscyphozoa/vpin-v1` | `srv-d80ro1jbc2fs738etkjg` | https://vpin-v1.onrender.com | order_flow_toxicity | paper |
| `venue-lag-v1` | `Dapperscyphozoa/venue-lag-v1` | `srv-d80ro1nlk1mc739sfqj0` | https://venue-lag-v1.onrender.com | cross_venue_arb | paper |
| `tod-reversion-v1` | `Dapperscyphozoa/tod-reversion-v1` | `srv-d80ro2vavr4c73aq4ubg` | https://tod-reversion-v1.onrender.com | time_of_day_mr | paper |
| `wyckoff-v1` | `Dapperscyphozoa/wyckoff-v1` | `srv-d80ro367r5hc73btg24g` | https://wyckoff-v1.onrender.com | wyckoff_phase | paper |
| `avwap-mesh-v1` | `Dapperscyphozoa/avwap-mesh-v1` | `srv-d80ro3dckfvc73ddj610` | https://avwap-mesh-v1.onrender.com | avwap_confluence | paper |

## PM patches (3)

| Endpoint | Source | Status |
|---|---|---|
| `GET /gex/{BTC\|ETH}` | `pm/gex_loader.py` (Deribit) | LIVE — BTC GEX $+225M positive, gamma flip $79,750 |
| `GET /stable-flow` | `pm/stable_flow_loader.py` (DefiLlama) | LIVE — regime bearish (skewed by DAI wind-down, v2 swap to USDS) |
| `GET /sentiment-velocity/{coin}` | `pm/sentiment_velocity.py` (extends mer.py) | LIVE — awaiting mer_items poller to populate |

## State

- 8 / 8 services healthy
- 16 engines registered in PM (`/engines`)
- All multica engines: `LIVE_TRADING=0`, `PM_CHECK_ENABLED=0`
- Account: ~$505 paper baseline

## Promotion path

```
paper → canary (0.05×) → small (0.25×) → full (1.00×)
```

Per-engine criteria for canary:
- ≥ 30 paper trades
- PF ≥ 1.3 on paper
- No critical bugs in 7-day forward walk
- Backtest agreement (paper PF within 20% of backtest)

Flip via:
1. `PM_CHECK_ENABLED=1` on Render env
2. `pm/config.py` `lifecycle_stage` field → `canary`
3. Push PM redeploy

## Pending / iterate

- [ ] Backtest scripts per engine (in each repo)
- [ ] Swap DAI → USDS in `stable_flow_loader.py`
- [ ] Wire `gex_modifier`, `stable_flow_modifier`, `velocity_modifier` into `pretrade_gate.py` (shadow first)
- [ ] PM cron for `mer.py` RSS poller (so sentiment-velocity has data)
- [ ] Continuous watcher — run on Mac or as Render cron (currently one-pass only)
