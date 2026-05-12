# Bug Fix + Cleanup Pass

## Bugs fixed

### Critical
1. **PM single-threaded HTTPServer caused /confluence + /coverage 502s.**
   - Both endpoints poll 6 engines sequentially with 4-8s timeouts.
   - Worst case ~48s sequential, exceeded Render's 30s gateway → 502.
   - Fix: ThreadingHTTPServer + ThreadPoolExecutor parallel polls.
   - Result: /confluence 1.16s, /coverage 0.33s (was timing out).

2. **SESSION_HOURS env vars set but never enforced in code.**
   - Previously pushed `SESSION_HOURS=7,8,9,10,11,12,13,14,15` to funding-div
     etc. expecting it to work. Engines fired 24/7 ignoring the setting.
   - Fix: implemented gate in trader.attempt_trade() at top of function.
   - Per-engine deployed per sweep findings:
     - funding-div: London (PF 2.61 vs 1.59 all-hours)
     - liq-heatmap: NY+off (PF 1.83-3.03 vs 1.61)
     - avwap-mesh: Lon+NY (avoids -28R off-hours)

### Cleanup
3. **Stale env vars deleted** (had been set by parallel session but never
   implemented — BOOK_AMPLIFIER_MODE, ENSEMBLE_VOTING_MODE,
   MACRO_CONFLUENCE_MODE). 3 stale vars × 6 engines = 18 deleted.

4. **/coverage endpoint URL list** included tod-reversion-v1 (deprecated)
   and missed alt-rotation-v1. Updated.

5. **stall_tracker legacy fallback** returned `cell_drift: None` instead of
   `[]` for engines that don't expose `/cells`. Inconsistent — fixed.

6. **alt-rotation-v1 had 0 cells** (never seeded). Pushed 28 seed cells
   per coin × regime × direction based on backtest stats (PF 1.16 maker).

7. **Sandbox bloat: 194 stale /tmp working directories** from clone-prop-redeploy
   cycles. Cleaned, kept 4 useful ones.

## Health after pass
- 8/8 services healthy (PM + 5 active engines + 2 suspended/deprecated)
- /confluence, /coverage, /macro_state, /net_position all serving sub-1.5s
- 28 active cells across funding-div + liq-heatmap + avwap-mesh
- 117 bootstrap cells accumulating data
- 1 outstanding alert: funding-div NO_TRADES_EVER (will clear once SESSION_HOURS
  window hits — engine will be in London-only mode from next scan)
