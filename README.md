# multica

Parallel build/deploy/self-heal orchestrator for the Cyber Psycho federated
trading stack. Forks the `engine-template` into N independent Render services,
each running a distinct strategy, all coordinated by Portfolio Manager.

## Files

| File | Role |
|---|---|
| `strategies.py` | Strategy code (one `signal_detector.py` per engine, embedded as strings) |
| `multica_orchestrator.py` | Phase 1-5: fork → push → deploy → monitor → smoke-test |
| `multica_watcher.py` | Continuous health + self-heal loop with known-pattern fix attempts |
| `patch_pm_check.py` | Utility: bulk-flip `PM_CHECK_ENABLED` env across all services |
| `check_logs.py` | Utility: pull recent runtime logs for each service |
| `state.json` | Orchestrator state — `{engine: {fork_done, push_done, deploy_done, url, ...}}` |
| `incidents.jsonl` | Watcher incident log |
| `MULTICA_STATUS.md` | Auto-generated deployment status table |

## Run

```bash
# Ship/refresh all engines (idempotent — skips completed phases)
python3 multica_orchestrator.py

# One-off health check + auto-heal
python3 multica_watcher.py

# Continuous watcher (5min loop)
python3 multica_watcher.py --loop
```

## Architecture

```
engine-template (canonical)
   │
   │  multica orchestrator forks 7 ways:
   ├──► liq-heatmap-v1     stop-hunt cluster fader
   ├──► funding-div-v1     funding-rate divergence
   ├──► vpin-v1            volume-synchronized PIN toxicity
   ├──► venue-lag-v1       cross-venue price discovery lag
   ├──► tod-reversion-v1   time-of-day mean reversion to VWAP
   ├──► wyckoff-v1         spring / upthrust phase detector
   └──► avwap-mesh-v1      anchored VWAP cluster fader

All deploy as independent Render services, each registered in PM at
https://portfolio-manager-7df2.onrender.com/engines.
```

## Self-heal patterns recognized

`multica_watcher.py` classifies log tails and auto-fixes:

| Pattern | Action |
|---|---|
| `pm_error_401` in skips | Flip `PM_CHECK_ENABLED=0`, redeploy |
| 429 / rate_limited | Bump `HL_MIN_INTERVAL_MS=500`, redeploy |
| `Address already in use` | Redeploy (transient) |
| `Out of memory` | Redeploy (transient) |
| `ModuleNotFoundError`, `SyntaxError`, `Build failed` | Logged, human required |

## Promotion path

```
paper → canary (0.05×) → small (0.25×) → full (1.00×)
```

PM `/size/{engine}` returns the multiplier on engine default notional. Paper
engines get 0.0 — they log signals/trades but place no live orders. Promotion
is manual via `pm/config.py` `lifecycle_stage` field.

## Required env vars

```bash
export GH_TOKEN=ghp_...      # GitHub PAT with repo scope
export RENDER_TOKEN=rnd_...  # Render API token
```

Tokens are never committed — orchestrator + watcher read from env at runtime.
