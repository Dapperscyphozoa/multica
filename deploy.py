"""
deploy.py — controlled Render redeploys (autoDeploy is OFF on all services).

Workflow now (per Cyber's directive — minimise build pipeline hours):
  - autoDeploy is OFF on PM + all engine services
  - git push lands code on GitHub but does NOT trigger a Render build
  - This script triggers a build only when explicitly invoked
  - Env-var changes still work (Render redeploys without rebuild)

Usage:
    python3 deploy.py pm                       # redeploy just PM
    python3 deploy.py wyckoff-v1               # one engine
    python3 deploy.py all                      # all engines + PM (use sparingly)
    python3 deploy.py engines                  # all engines, not PM
    python3 deploy.py status                   # show current deploy state
"""
from __future__ import annotations
import os
import sys
import json
import time
import urllib.request
import urllib.error

T = os.environ.get("RENDER_TOKEN", "")
if not T:
    print("ERROR: RENDER_TOKEN unset", file=sys.stderr); sys.exit(1)

SERVICES = {
    "pm":                 "srv-d7vjeb6gvqtc73coi4m0",
    "liq-heatmap-v1":     "srv-d80ro1gg4nts738u4oqg",
    "funding-div-v1":     "srv-d80ro1jeo5us73fqu2r0",
    "venue-lag-v1":       "srv-d80ro1nlk1mc739sfqj0",
    "tod-reversion-v1":   "srv-d80ro2vavr4c73aq4ubg",
    "wyckoff-v1":         "srv-d80ro367r5hc73btg24g",
    "avwap-mesh-v1":      "srv-d80ro3dckfvc73ddj610",
}
ALL_ENGINES = [k for k in SERVICES if k != "pm"]


def api(method, path, body=None):
    req = urllib.request.Request(f"https://api.render.com/v1{path}",
        data=(json.dumps(body).encode() if body else None),
        method=method,
        headers={"Authorization": f"Bearer {T}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def deploy(target_name, sid, clear_cache=False):
    """Deploy. clear_cache=False reuses build cache (faster, cheaper)."""
    body = {"clearCache": "clear"} if clear_cache else {}
    st, payload = api("POST", f"/services/{sid}/deploys", body)
    print(f"  {target_name:<22} deploy → {st} id={payload.get('id','?')}")
    return payload.get('id')


def status():
    print(f"{'service':<22}{'autoDeploy':>14}{'lastDeploy':>22}{'status':>14}")
    print("-" * 76)
    for name, sid in SERVICES.items():
        st, d = api("GET", f"/services/{sid}")
        if st >= 400: print(f"  {name:<20}  err {st}"); continue
        auto = d.get("autoDeploy", "?")
        st2, deps = api("GET", f"/services/{sid}/deploys?limit=1")
        latest = ""
        if deps and isinstance(deps, list) and deps:
            dep = deps[0].get("deploy", deps[0])
            latest = f"{(dep.get('finishedAt') or dep.get('createdAt') or '')[:19]} {dep.get('status','?')}"
        print(f"  {name:<20}{auto:>14}  {latest}")


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "status":
        status(); return
    if cmd == "all":
        targets = list(SERVICES.keys())
    elif cmd == "engines":
        targets = ALL_ENGINES
    elif cmd in SERVICES:
        targets = [cmd]
    else:
        print(f"unknown target: {cmd}"); sys.exit(1)
    print(f"deploying {len(targets)} service(s) (no rebuild — uses cached build)...")
    for t in targets:
        deploy(t, SERVICES[t])
        time.sleep(0.4)


if __name__ == "__main__":
    main()
