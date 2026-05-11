import os
import urllib.request, json, sys

TOKEN = os.environ.get("RENDER_TOKEN", "")
OWNER = "tea-d6ufmnea2pns739be9gg"

SVCS = {
    "liq-heatmap":   "srv-d80ro1gg4nts738u4oqg",
    "funding-div":   "srv-d80ro1jeo5us73fqu2r0",
    "vpin":          "srv-d80ro1jbc2fs738etkjg",
    "venue-lag":     "srv-d80ro1nlk1mc739sfqj0",
    "tod-reversion": "srv-d80ro2vavr4c73aq4ubg",
    "wyckoff":       "srv-d80ro367r5hc73btg24g",
    "avwap-mesh":    "srv-d80ro3dckfvc73ddj610",
}

def logs(svc, n=12):
    url = f"https://api.render.com/v1/logs?ownerId={OWNER}&resource={svc}&limit={n}&direction=backward"
    req = urllib.request.Request(url,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

for name, svc in SVCS.items():
    try:
        d = logs(svc, 8)
        print(f"\n--- {name} ({svc}) ---")
        for l in d.get("logs", [])[:6]:
            ts = l.get("timestamp", "")[11:19]
            msg = l.get("message", "")[:140]
            print(f"  {ts} {msg}")
    except Exception as e:
        print(f"--- {name}: ERR {e}")
