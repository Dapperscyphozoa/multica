# Session 2026-05-12 — Income Streams Activation

## Shipped this session (post-confluence-stack work)

### A. Push notifications (Telegram + Discord)
- `pm/notifier.py` with debounced send + per-(coin, direction) hash
- Background poller in PM main (every 300s default)
- Endpoints: `/notifier/test`, `/notifier/status`
- Pushes high-confluence alerts (score >= 25) AND cross-venue funding ops (APR >= 30%)
- Required env vars on PM (Cyber sets):
  - TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, OR
  - DISCORD_WEBHOOK_URL

### B. HL Builder Code Kickback
- engine/hl_exchange.py: `_builder_info()` injected into every order call
- scripts/approve_builder.py: onchain approval ceremony (Cyber runs once)
- All 6 active engines have `HL_BUILDER_ADDRESS` env pre-set to wallet
- Expected: 1.1bp kickback per taker leg = $0.50-$2.00/day at small scale

### C. Cross-Venue Funding Arb (Blofin integration)
- engine/blofin_client.py: full HMAC-SHA256 REST wrapper, public+private endpoints
- engine/cross_venue_engine.py: paired-position open/close + price divergence scan
- engine/blofin_client.coin_size_to_contracts: contract spec lookup with 1h cache
- New repo + Render service: cross-venue-funding-v1.onrender.com
- Live opportunities now: JUP 43%, ATOM 38%, SOL 37%, HYPE 30% APR
- 3 DRY_RUN paired positions open ($1.62/day projected income)
- Required env on Render service:
  - BLOFIN_API_KEY, BLOFIN_API_SECRET, BLOFIN_PASSPHRASE
  - DRY_RUN=0 (when ready to go live)

### D. tod-momentum-v1 (inverted tod-reversion)
- Flipped is_long polarity + fixed TP placement (was VWAP, now direction-correct)
- Backtest: PF 1.096 maker, 348 trades, 38% WR — marginal positive
- Deployed at tod-momentum-v1.onrender.com
- Registered in PM, paper mode

### E. funding-harvester-v1
- Holds positions across HL settlement to collect funding payments
- Tight SL (0.5x ATR), wide TP (3.0x ATR), 2-bar hold
- Funding-direction logic: long when shorts pay, short when longs pay
- Deployed at funding-harvester-v1.onrender.com
- Required env: STRATEGY_FUNDING_MIN, ATR mults

### F. Walk-forward optimizer harness
- walk_forward_optimize.py: max worst-window-Sharpe objective
- Generic env-param sweeping
- STRATEGY_PARAMS_OVERRIDES env JSON for arbitrary fork-param tuning
- Plumbed to all 8 forks

### G. WF-optimized funding-div params
- Tested 4 funding threshold combinations × 3 windows
- Best: HI=0.00005, LO=-0.00003 (asymmetric) — min_sharpe +0.015, min_pf 1.03
- Pushed to live service + redeployed

### H. /profit_projection PM endpoint
- Computes live $/day estimate across all income streams
- Current projection in DRY_RUN: $1.62 from cross-venue funding + $20 per executed alert + $0.50 from builder kickback

### I. PM dashboard panels
- High-confluence alerts (manual overlay)
- Cross-venue funding arb (delta-neutral yield)
- Price divergence (fast convergence arb)
- Engine activity table

### J. Watcher updated
- Now tracks 11 multica services (added alt-rotation, tod-momentum, cross-venue-funding, funding-harvester)

---

## Live state

| Engine | URL | Status |
|---|---|---|
| liq-heatmap-v1 | live | paper, 12 active cells |
| funding-div-v1 | live | paper, 7 active cells, WF-optimized thresholds |
| venue-lag-v1 | live | paper |
| wyckoff-v1 | live | paper, 12 bootstrap cells |
| avwap-mesh-v1 | live | paper, 16 active cells, demoted (NOISE) |
| alt-rotation-v1 | live | paper, 28 bootstrap cells |
| tod-momentum-v1 | live | paper, 5 bootstrap cells |
| cross-venue-funding-v1 | live | DRY_RUN, 3 paired positions open |
| funding-harvester-v1 | live | paper, just deployed |
| portfolio-manager | live | confluence stack + alerts + notifier |
| vpin-v1 | suspended | deprecated |
| tod-reversion-v1 | suspended | deprecated (PF 0.84) |

---

## What activates income (Cyber checklist)

| Stream | Action | Estimated daily $ on $2k account |
|---|---|---|
| 1. Manual overlay alerts | LIVE NOW — just check dashboard / set up Telegram bot | $4-12 (discretionary) |
| 2. Telegram push | Set TELEGRAM_BOT_TOKEN + CHAT_ID on PM | 0 (just enables push) |
| 3. Cross-venue funding | Set BLOFIN keys, fund both venues, DRY_RUN=0 | $1.62/day baseline (more on bigger notional) |
| 4. Builder kickback | Run approve_builder.py on Mac, CONFIRM=yes | $0.50-$2.00 |

Combined: ~$6-15/day at $2k account. 0.3-0.75% daily compounding.
