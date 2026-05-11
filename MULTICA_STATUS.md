# Multica Status

_generated: 2026-05-11T11:33:30.457908Z_

## Deployed engines

| Engine | Repo | Service ID | URL | Fork | Push | Deploy | Smoke |
|---|---|---|---|:-:|:-:|:-:|:-:|
| `liq-heatmap-v1` | `Dapperscyphozoa/liq-heatmap-v1` | `srv-d80ro1gg4nts738u4oqg` | https://liq-heatmap-v1.onrender.com | ✓ | ✓ | ✓ | ✓ |
| `funding-div-v1` | `Dapperscyphozoa/funding-div-v1` | `srv-d80ro1jeo5us73fqu2r0` | https://funding-div-v1.onrender.com | ✓ | ✓ | ✓ | ✓ |
| `vpin-v1` | `Dapperscyphozoa/vpin-v1` | `srv-d80ro1jbc2fs738etkjg` | https://vpin-v1.onrender.com | ✓ | ✓ | ✓ | ✓ |
| `venue-lag-v1` | `Dapperscyphozoa/venue-lag-v1` | `srv-d80ro1nlk1mc739sfqj0` | https://venue-lag-v1.onrender.com | ✓ | ✓ | ✓ | ✓ |
| `tod-reversion-v1` | `Dapperscyphozoa/tod-reversion-v1` | `srv-d80ro2vavr4c73aq4ubg` | https://tod-reversion-v1.onrender.com | ✓ | ✓ | ✓ | ✓ |
| `wyckoff-v1` | `Dapperscyphozoa/wyckoff-v1` | `srv-d80ro367r5hc73btg24g` | https://wyckoff-v1.onrender.com | ✓ | ✓ | ✓ | ✓ |
| `avwap-mesh-v1` | `Dapperscyphozoa/avwap-mesh-v1` | `srv-d80ro3dckfvc73ddj610` | https://avwap-mesh-v1.onrender.com | ✓ | ✓ | ✓ | ✓ |

## Configuration

- All engines: `LIVE_TRADING=0` (paper)
- All engines: `PM_CHECK_ENABLED=0` (bootstrap; flip to 1 after PM_AUTH_TOKEN provisioned)
- PM registry patched: 7 new entries, all `lifecycle_stage=paper`

## Promotion path

```
paper → canary (0.05×) → small (0.25×) → full (1.00×)
```

Per engine, after 14d paper + backtest validation:

1. Set `PM_CHECK_ENABLED=1` on Render env
2. Edit `pm/config.py` lifecycle_stage to `canary` and push PM redeploy
3. Monitor 7d at canary; promote to `small` if PF >= 1.4 with N >= 30 trades

