import os
"""Flip PM_CHECK_ENABLED=0 on all 7 new engines via Render env-var update."""
import urllib.request, json, time

TOKEN = os.environ.get("RENDER_TOKEN", "")
SVCS = {
    "liq-heatmap-v1":   "srv-d80ro1gg4nts738u4oqg",
    "funding-div-v1":   "srv-d80ro1jeo5us73fqu2r0",
    "vpin-v1":          "srv-d80ro1jbc2fs738etkjg",
    "venue-lag-v1":     "srv-d80ro1nlk1mc739sfqj0",
    "tod-reversion-v1": "srv-d80ro2vavr4c73aq4ubg",
    "wyckoff-v1":       "srv-d80ro367r5hc73btg24g",
    "avwap-mesh-v1":    "srv-d80ro3dckfvc73ddj610",
}

def api(method, path, body=None):
    url = f"https://api.render.com/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}",
                  "Accept":"application/json","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')

for name, svc in SVCS.items():
    # Update single env var — PUT /services/:id/env-vars/:key
    status, payload = api("PUT", f"/services/{svc}/env-vars/PM_CHECK_ENABLED",
                          {"value": "0"})
    print(f"{name}: PM_CHECK_ENABLED=0 → status={status}")
    time.sleep(0.5)

# Trigger redeploy on each
print("\n--- triggering redeploys ---")
for name, svc in SVCS.items():
    status, payload = api("POST", f"/services/{svc}/deploys", {})
    deploy_id = payload.get("id") if isinstance(payload, dict) else "?"
    print(f"{name}: deploy_id={deploy_id} status={status}")
    time.sleep(0.5)
