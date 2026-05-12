# Session 2026-05-12 — Round 2 (Expansion + Persistence)

## Shipped this session

### A. Cross-venue scan expanded 20 → 178 coins
- Computed HL ∩ Blofin intersection (178 coins both venues support)
- Parallelized scan_opportunities + scan_price_divergence
  (ThreadPoolExecutor 12 workers, ~2s for full 178-coin scan)
- Set MAX_OPEN_POSITIONS=6, MIN_SPREAD=0.15 to take more opportunities
- Result: 15 funding ops surfaced vs 4 before, 6 paired positions open
- DRY_RUN projection up from $1.62/day → $2.46/day

### B. Walk-forward optimizer v2 (wf_optimize_v2.py)
- Supports STRATEGY_PARAMS_OVERRIDES JSON sweep via --sparam KEY:val1,val2
- Returns ranked configs by min(sharpe) across 3 non-overlapping windows
- Tested liq-heatmap on both 60d cache + new 365d data
- Best config: cluster_band_pct=0.005, sweep_threshold_pct=0.0015
  - 60d data: min_sharpe -0.079, min_pf 0.83 (marginally negative)
  - 365d data: min_sharpe -0.134, min_pf 0.73 (consistently negative)
- Confirms walk-forward audit conclusion: no engine has cross-regime edge alone

### C. Multi-year historical data ingestion
- tools/fetch_historical.py — pulls hourly candles from CryptoCompare
- 365 days × 8765 hourly bars per coin
- Used for WF optimization across full bull/sideways/correction cycle
- /tmp/multica/hist/hist_365d.pkl saved (BTC + ETH + SOL)

### D. Whale tracker module (pm/whale_tracker.py)
- Polls HL userFills for configured WHALE_ADDRESSES
- Aggregates 4h fill flow by coin: net direction, notional, n_whales
- Endpoint: GET /whale_bias
- Integrated into alerts.gather_alerts:
  - +3 to alert score if whales agree with direction
  - -2 if whales fighting
- Requires manual research to populate WHALE_ADDRESSES env (HL leaderboard not public API-accessible)

### E. Alerts persistence + hit-rate evaluation
- pm/alerts_log.py — JSONL append-only log
- Snapshot triggered every 5min by notifier poller
- /alerts/hit_rate?hours=24 — computes WR by score bucket
- Will reveal empirical reliability of confluence scoring after 1-2 days

### F. Bug fixes
- PM /profit_projection cvf fetch timeout 4s → 8s (was killing fetch mid-scan)
- Dashboard panel fetches 4s → 6s (same reason)

## Live state (as of session close)

| Service | Status | Output |
|---|---|---|
| cross-venue-funding-v1 | DRY_RUN, 6/6 positions | $2.46/day projected, 178 coins scanned |
| 8 perp engines | paper | building cell history |
| portfolio-manager | live | 7 endpoints serving (alerts, confluence, coverage, macro_state, profit_projection, whale_bias, hit_rate) |
| Watcher | 11/12 healthy | tracking everything |

## Walk-forward verdict (confirmed across all engines)

| Engine | 60d single window | 365d cross-window | Conclusion |
|---|---|---|---|
| funding-div-v1 | PF 2.06 | min_pf 1.03 | Only engine that passes WF |
| liq-heatmap-v1 | PF 1.68 | min_pf 0.73 | Single-window only |
| wyckoff-v1 | PF 2.38 | (small sample) | Insufficient data |
| avwap-mesh-v1 | PF 1.18 | min_pf 0.98 | At breakeven |
| tod-momentum-v1 | PF 1.10 | (not tested) | Marginal |

**Takeaway**: standalone engines are mostly noise. The portfolio works via:
1. Mechanical edges (builder code kickback, cross-venue funding) → no alpha required
2. Confluence stack filtering noise to high-quality setups → ensemble effect
3. Manual overlay on top score >25 alerts → discretionary alpha

## What activates income now (Cyber checklist refresher)

1. Telegram bot for alerts — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID on PM
2. Blofin API keys — set 3 env vars on cross-venue-funding-v1, fund both venues, DRY_RUN=0
3. Builder code — run scripts/approve_builder.py with HL_PRIVATE_KEY + CONFIRM=yes
4. Whale addresses — research HL top traders, set WHALE_ADDRESSES on PM
