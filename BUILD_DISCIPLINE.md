# Build Discipline — Minimise Render Pipeline Hours

## What changed (2026-05-12)

`autoDeploy=no` on all engine services + PM. Git pushes no longer trigger
Render builds.

## New workflow

### Code change → deploy
```bash
# 1. push code
git push

# 2. trigger deploy when ready (uses build cache — faster, cheaper)
RENDER_TOKEN=... python3 deploy.py pm                # just PM
RENDER_TOKEN=... python3 deploy.py wyckoff-v1        # one engine
RENDER_TOKEN=... python3 deploy.py engines           # all 6 multica engines
RENDER_TOKEN=... python3 deploy.py all               # everything (use sparingly)

# Status
RENDER_TOKEN=... python3 deploy.py status
```

### Env-var change → no rebuild needed
```bash
# Render API: PUT /services/{id}/env-vars/{key}
# Triggers an "envvars_updated" deploy that REUSES last build image
# Free in pipeline minutes
curl -X PUT -H "Authorization: Bearer $RENDER_TOKEN" \
  "https://api.render.com/v1/services/srv-.../env-vars/MAX_HOLD_BARS" \
  -d '{"value":"48"}'
```

### CELL_SEEDS update → no rebuild needed
Same as env-var. The cell_manager picks up new seeds on next `gate_decision()` call.

### What still does a full rebuild
- Manually triggered with `deploy.py` (uses cached pip install — ~30s)
- Manual click in Render dashboard with "Clear cache & deploy" (full rebuild — 2-3min)
- Render Cron service (runs `python3 multica_watcher.py` every 5min — no rebuild)

## Why this matters

Before: every commit = full rebuild = ~60-90s × N engines that share template.
With 6 forks pulling from the same template that's 6 full rebuilds per template change.
At ~10 commits/day that's ~60 build runs = ~1 hour of pipeline.

After: one explicit deploy per change. Env tweaks free.

## When to clear cache

Only when:
- Requirements.txt changed (new pip deps)
- Python version bump needed
- pip cache appears corrupted

Otherwise pass `clearCache: false` (default).
