"""
multica_watcher.py — autonomous health + self-heal loop for the multica engines.

Runs every WATCH_INTERVAL_SEC. For each service:
  1. GET /health        — must return 200 with status=ok
  2. If unhealthy: fetch last 80 log lines, classify the error
  3. Apply known fix patterns (env-var flip, redeploy)
  4. Record incident
  5. Re-check on next loop

Run with:
  python3 multica_watcher.py             # single pass
  python3 multica_watcher.py --loop      # forever (sleep 300s between passes)

Designed for Render's free tier — keeps engines warm too.
"""
from __future__ import annotations
import os
import sys
import time
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

RENDER_TOKEN = os.environ.get("RENDER_TOKEN", "")
RENDER_OWNER = "tea-d6ufmnea2pns739be9gg"
PM_URL = "https://portfolio-manager-7df2.onrender.com"

# All 7 multica engines + the supporting precog stack (read-only watch for those)
SERVICES = {
    # Multica engines (we own these)
    "liq-heatmap-v1":   {"id": "srv-d80ro1gg4nts738u4oqg", "url": "https://liq-heatmap-v1.onrender.com",   "owned": True},
    "funding-div-v1":   {"id": "srv-d80ro1jeo5us73fqu2r0", "url": "https://funding-div-v1.onrender.com",   "owned": True},
    "vpin-v1":          {"id": "srv-d80ro1jbc2fs738etkjg", "url": "https://vpin-v1.onrender.com",          "owned": True, "suspended": True},
    "venue-lag-v1":     {"id": "srv-d80ro1nlk1mc739sfqj0", "url": "https://venue-lag-v1.onrender.com",     "owned": True},
    "tod-reversion-v1": {"id": "srv-d80ro2vavr4c73aq4ubg", "url": "https://tod-reversion-v1.onrender.com", "owned": True, "suspended": True},
    "wyckoff-v1":       {"id": "srv-d80ro367r5hc73btg24g", "url": "https://wyckoff-v1.onrender.com",       "owned": True, "suspended": True},
    "avwap-mesh-v1":    {"id": "srv-d80ro3dckfvc73ddj610", "url": "https://avwap-mesh-v1.onrender.com",    "owned": True, "suspended": True},
    "alt-rotation-v1":  {"id": "srv-d816te8g4nts7398ffmg", "url": "https://alt-rotation-v1.onrender.com", "owned": True, "suspended": True},
    "tod-momentum-v1":  {"id": "srv-d819tve7r5hc73cafabg", "url": "https://tod-momentum-v1.onrender.com", "owned": True},
    "cross-venue-funding-v1": {"id": "srv-d819hqugvqtc73e6tei0", "url": "https://cross-venue-funding-v1.onrender.com", "owned": True},
    "funding-harvester-v1": {"id": "srv-d81abs7lk1mc73a8i9d0", "url": "https://funding-harvester-v1.onrender.com", "owned": True},
    # Supporting (read-only)
    "portfolio-manager": {"id": "srv-d7vjeb6gvqtc73coi4m0", "url": PM_URL,                          "owned": False},
}

INCIDENT_LOG = Path("/tmp/multica/incidents.jsonl")
WATCH_INTERVAL_SEC = 300


def render_api(method: str, path: str, body=None, timeout: int = 20):
    url = f"https://api.render.com/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"Bearer {RENDER_TOKEN}",
                  "Accept":"application/json","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, (json.loads(r.read() or b'{}'))
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'{}')
        except: return e.code, {}
    except Exception as e:
        return 0, {"_err": str(e)}


def http_get(url: str, timeout: int = 12) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return {"ok": True, "status": r.status, "body": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode()[:500]}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def get_logs(svc_id: str, n: int = 80) -> list:
    url = f"https://api.render.com/v1/logs?ownerId={RENDER_OWNER}&resource={svc_id}&limit={n}&direction=backward"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {RENDER_TOKEN}"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return d.get("logs", [])
    except Exception:
        return []


def record(payload: dict):
    INCIDENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with INCIDENT_LOG.open("a") as f:
        payload["ts"] = datetime.now(timezone.utc).isoformat()
        f.write(json.dumps(payload, default=str) + "\n")


# ─── Fix patterns ─────────────────────────────────────────────────────
def classify_error(log_msgs: list[str]) -> str | None:
    """Return a known fix tag or None."""
    txt = "\n".join(log_msgs[-50:])
    if "ModuleNotFoundError" in txt or "ImportError" in txt:
        return "missing_dep"
    if "ENGINE_NAME" in txt and "unset" in txt.lower():
        return "missing_engine_name"
    if "pm_error_401" in txt and "skipped" in txt:
        return "pm_auth_401"
    if "HL_PRIVATE_KEY" in txt and "missing" in txt.lower():
        return "missing_hl_key"
    if "Address already in use" in txt or "EADDRINUSE" in txt:
        return "port_collision"
    if "SyntaxError" in txt or "IndentationError" in txt:
        return "code_syntax"
    if "Out of memory" in txt or "MemoryError" in txt:
        return "oom"
    if "rate_limit" in txt.lower() or "429" in txt:
        return "rate_limited"
    if "exited with code 1" in txt or "Build failed" in txt:
        return "build_fail"
    return None


def fix_attempt(name: str, svc_id: str, tag: str) -> dict:
    """Try to apply a known fix. Returns {applied: bool, action: str}."""
    if tag == "pm_auth_401":
        # Flip PM_CHECK_ENABLED=0
        st, _ = render_api("PUT", f"/services/{svc_id}/env-vars/PM_CHECK_ENABLED",
                           {"value": "0"})
        if st == 200:
            render_api("POST", f"/services/{svc_id}/deploys", {})
            return {"applied": True, "action": "flipped PM_CHECK_ENABLED=0, redeployed"}
    if tag in ("rate_limited",):
        # Bump HL throttle
        st, _ = render_api("PUT", f"/services/{svc_id}/env-vars/HL_MIN_INTERVAL_MS",
                           {"value": "500"})
        if st == 200:
            render_api("POST", f"/services/{svc_id}/deploys", {})
            return {"applied": True, "action": "bumped HL_MIN_INTERVAL_MS=500, redeployed"}
    if tag in ("port_collision", "oom"):
        # Restart
        render_api("POST", f"/services/{svc_id}/deploys", {})
        return {"applied": True, "action": "redeployed (transient)"}
    # No known fix for: missing_dep, code_syntax, missing_engine_name, missing_hl_key, build_fail
    # These need human intervention.
    return {"applied": False, "action": "no_known_fix"}


# ─── Main loop ────────────────────────────────────────────────────────
def get_engine_trade_activity(url: str) -> dict:
    """Fetch last-trade timestamp + 24h/72h/168h trade counts from an engine."""
    closures = http_get(f"{url}/closures?limit=200")
    if not closures.get("ok"):
        return {"ok": False, "reason": "closures_unreachable"}
    rows = closures["body"].get("closures", [])
    now_ms = int(time.time() * 1000)
    last_ts = None
    n_24h = n_72h = n_168h = 0
    for r in rows:
        ts = r.get("ts_close") or r.get("ts_open") or 0
        if not ts: continue
        if last_ts is None or ts > last_ts:
            last_ts = ts
        age_ms = now_ms - ts
        if age_ms < 86400_000: n_24h += 1
        if age_ms < 3*86400_000: n_72h += 1
        if age_ms < 7*86400_000: n_168h += 1
    return {
        "ok": True,
        "last_trade_ts": last_ts,
        "hours_since_last": (now_ms - last_ts) / 3600_000 if last_ts else None,
        "n_trades_24h": n_24h,
        "n_trades_72h": n_72h,
        "n_trades_168h": n_168h,
    }


def get_cell_state(url: str) -> dict:
    """Fetch cell breakdown from engine's /cells endpoint."""
    r = http_get(f"{url}/cells")
    if not r.get("ok"):
        return {"ok": False}
    cells = r["body"].get("cells", [])
    active = [c for c in cells if c.get("stage") == "active"]
    bootstrap = [c for c in cells if c.get("stage") == "bootstrap"]
    demoted = [c for c in cells if c.get("stage") == "demoted"]

    # Drift detection: active cells with PF < 1.3 (close to demotion threshold)
    drift_warning = [c["cell_key"] for c in active
                      if c.get("pf") is not None and c["pf"] < 1.30]
    # Rehab candidates: demoted cells with recent good PF
    rehab = [c["cell_key"] for c in demoted
              if c.get("pf") is not None and c["pf"] > 1.40
              and c.get("n_trades", 0) >= 5]

    return {
        "ok": True,
        "total": len(cells),
        "active_n": len(active),
        "bootstrap_n": len(bootstrap),
        "demoted_n": len(demoted),
        "drift_warning": drift_warning,
        "rehab_candidates": rehab,
    }


def classify_activity_alert(activity: dict, has_active_cells: bool) -> str | None:
    """Determine if an engine should be flagged for stale trading.

    Returns: None | 'COLD_24H' | 'STALE_72H' | 'DEAD_168H'
    """
    if not activity.get("ok"):
        return None
    hrs = activity.get("hours_since_last")
    if hrs is None:
        # No trades ever — only alert if engine claims active cells
        return "NO_TRADES_EVER" if has_active_cells else None
    if hrs >= 168: return "DEAD_168H"
    if hrs >= 72:  return "STALE_72H"
    if hrs >= 24 and activity.get("n_trades_24h", 0) == 0:
        return "COLD_24H"
    return None


def check_one(name: str, info: dict) -> dict:
    if info.get("suspended"):
        return {"name": name, "ok": True, "suspended": True,
                "mode": "suspended"}
    url = info["url"]
    h = http_get(f"{url}/health")
    if h.get("ok") and h.get("body", {}).get("status") == "ok":
        # Healthy. Now check activity + cell state.
        activity = get_engine_trade_activity(url) if info.get("owned") else {"ok": False}
        cells = get_cell_state(url) if info.get("owned") else {"ok": False}
        alert = None
        if info.get("owned"):
            has_active = cells.get("active_n", 0) > 0
            alert = classify_activity_alert(activity, has_active)
        return {"name": name, "ok": True,
                "mode": h["body"].get("mode_effective"),
                "halted": h["body"].get("halted"),
                "activity": activity, "cells": cells, "alert": alert}
    # Unhealthy
    logs = get_logs(info["id"], 80)
    msgs = [l.get("message", "") for l in logs]
    tag = classify_error(msgs)
    result = {"name": name, "ok": False, "status": h.get("status"),
              "error": h.get("error") or h.get("body"), "tag": tag}
    if tag and info.get("owned"):
        fix = fix_attempt(name, info["id"], tag)
        result["fix"] = fix
    record(result)
    return result


def check_pm_visibility() -> dict:
    """Verify PM still sees the multica engines."""
    r = http_get(f"{PM_URL}/engines")
    if not r.get("ok"):
        return {"ok": False, "reason": "pm_unreachable"}
    engines = r["body"].get("engines", {})
    missing = []
    for name in SERVICES:
        if SERVICES[name].get("owned") and name not in engines:
            missing.append(name)
    return {"ok": not missing, "missing": missing, "total_registered": len(engines)}


def one_pass(verbose: bool = True):
    print(f"\n=== watcher pass @ {datetime.now(timezone.utc).isoformat()} ===")
    results = []
    for name, info in SERVICES.items():
        r = check_one(name, info)
        results.append(r)
        if verbose:
            if r["ok"]:
                if r.get("suspended"):
                    print(f"  — {name}: suspended (deprecated)")
                else:
                    a = r.get("activity") or {}
                    c = r.get("cells") or {}
                    hrs = a.get("hours_since_last")
                    hrs_str = f"{hrs:.0f}h" if hrs is not None else "never"
                    cell_str = (f"cells:{c.get('active_n',0)}A/{c.get('bootstrap_n',0)}B/"
                                f"{c.get('demoted_n',0)}D" if c.get("ok") else "")
                    alert = r.get("alert")
                    alert_str = f" [{alert}]" if alert else ""
                    print(f"  ✓ {name}: ok ({r.get('mode','?')}) "
                          f"last_trade={hrs_str} t24h={a.get('n_trades_24h',0)} {cell_str}{alert_str}")
                    if c.get("drift_warning"):
                        print(f"      ⚠ drift: {','.join(c['drift_warning'][:5])}")
                    if c.get("rehab_candidates"):
                        print(f"      ↺ rehab: {','.join(c['rehab_candidates'][:5])}")
            else:
                fix = r.get("fix", {})
                print(f"  ✗ {name}: status={r.get('status')} tag={r.get('tag')} "
                      f"fix_applied={fix.get('applied', False)} action={fix.get('action','')}")

    pm = check_pm_visibility()
    if verbose:
        print(f"  PM visibility: {'✓' if pm['ok'] else '✗'} "
              f"(registered={pm.get('total_registered')}, missing={pm.get('missing', [])})")

    # Summary
    healthy = sum(1 for r in results if r["ok"])
    print(f"\n  summary: {healthy}/{len(results)} healthy")
    return results


if __name__ == "__main__":
    if "--loop" in sys.argv:
        while True:
            try:
                one_pass()
            except Exception as e:
                print(f"  watcher error: {e}")
            time.sleep(WATCH_INTERVAL_SEC)
    else:
        one_pass()
