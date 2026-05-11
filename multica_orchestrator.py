"""
multica_orchestrator.py — parallel build/deploy agent for the Cyber Psycho stack.

Operates on a manifest of engines. Phases:
  1. FORK     — clone engine-template, swap signal_detector, adjust config
  2. PUSH     — create GH repo + git push (parallel)
  3. DEPLOY   — create Render service (parallel)
  4. MONITOR  — poll deploy status, on failure fetch logs + attempt fix
  5. VERIFY   — smoke test /health endpoints

Designed to be safely re-runnable: if a phase succeeded for an engine,
subsequent runs skip it.
"""
from __future__ import annotations
import os
import sys
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.error
import concurrent.futures
from pathlib import Path

# Make strategies importable
sys.path.insert(0, "/tmp/multica")
from strategies import STRATEGIES

GH_TOKEN = os.environ.get("GH_TOKEN", "")
RENDER_TOKEN = os.environ.get("RENDER_TOKEN", "")
RENDER_OWNER = "tea-d6ufmnea2pns739be9gg"   # from existing services
PM_URL = "https://portfolio-manager-7df2.onrender.com"
HL_WALLET_DEFAULT = "0x3eDaD0649Db466E6E7B9a0Caa3E5d6ddc71B5ffE"

# ─── Manifest ──────────────────────────────────────────────────────────
MANIFEST = [
    {
        "name": "liq-heatmap-v1",
        "cloid_prefix": "liqhmp_",
        "primary": "BTC,ETH,SOL,LINK",
        "secondary": "AVAX,DOGE,BNB,XRP",
        "strategy_params": {
            "cluster_lookback": 120, "pivot_lookback": 5,
            "cluster_band_pct": 0.003, "min_cluster_pivots": 3,
            "sweep_threshold_pct": 0.002, "vol_spike_mult": 1.8,
            "max_cluster_proximity_pct": 0.020,
        },
        "trade_params": {"sl_atr_mult": 1.8, "tp_atr_mult": 4.0,
                         "max_hold_bars": 48, "atr_period": 14},
    },
    {
        "name": "funding-div-v1",
        "cloid_prefix": "fnddiv_",
        "primary": "BTC,ETH,SOL,HYPE",
        "secondary": "AVAX,XRP,DOGE,LINK,SUI,TON",
        "strategy_params": {
            "funding_threshold_hi": 0.0003,
            "funding_threshold_lo": -0.0002,
        },
        "trade_params": {"sl_atr_mult": 2.0, "tp_atr_mult": 4.0,
                         "max_hold_bars": 36, "atr_period": 14},
    },
    {
        "name": "vpin-v1",
        "cloid_prefix": "vpin_",
        "primary": "BTC,ETH,SOL,HYPE",
        "secondary": "AVAX,DOGE",
        "strategy_params": {
            "vpin_window": 50, "vpin_threshold": 0.55,
            "swing_lookback": 24, "extreme_proximity_pct": 0.005,
        },
        "trade_params": {"sl_atr_mult": 1.5, "tp_atr_mult": 4.5,
                         "max_hold_bars": 12, "atr_period": 14},
    },
    {
        "name": "venue-lag-v1",
        "cloid_prefix": "vnlag_",
        "primary": "BTC,ETH,SOL",
        "secondary": "DOGE,AVAX,LINK,XRP",
        "strategy_params": {
            "min_venue_divergence_pct": 0.0025,
        },
        "trade_params": {"sl_atr_mult": 1.2, "tp_atr_mult": 2.5,
                         "max_hold_bars": 4, "atr_period": 14},
    },
    {
        "name": "tod-reversion-v1",
        "cloid_prefix": "todrev_",
        "primary": "BTC,ETH,SOL,LINK",
        "secondary": "AVAX,XRP,DOGE,HYPE",
        "strategy_params": {
            "hour_whitelist": "2,3,4,15,16,17,18",
            "vwap_dev_threshold_pct": 0.004,
        },
        "trade_params": {"sl_atr_mult": 1.5, "tp_atr_mult": 3.0,
                         "max_hold_bars": 6, "atr_period": 14},
    },
    {
        "name": "wyckoff-v1",
        "cloid_prefix": "wyckff_",
        "primary": "BTC,ETH,SOL,LINK",
        "secondary": "AVAX,XRP,DOGE,HYPE,SUI,TON",
        "strategy_params": {
            "range_lookback": 24, "spring_vol_mult": 1.5,
            "breach_max_pct": 0.005,
        },
        "trade_params": {"sl_atr_mult": 2.0, "tp_atr_mult": 6.0,
                         "max_hold_bars": 96, "atr_period": 14},
    },
    {
        "name": "avwap-mesh-v1",
        "cloid_prefix": "avwap_",
        "primary": "BTC,ETH,SOL,LINK",
        "secondary": "AVAX,DOGE,XRP,HYPE",
        "strategy_params": {
            "mesh_min_anchors": 2, "mesh_band_pct": 0.005,
            "approach_max_pct": 0.01,
        },
        "trade_params": {"sl_atr_mult": 1.8, "tp_atr_mult": 4.5,
                         "max_hold_bars": 48, "atr_period": 14},
    },
]

# ─── State tracking ────────────────────────────────────────────────────
STATE_FILE = Path("/tmp/multica/state.json")
def load_state() -> dict:
    if STATE_FILE.exists(): return json.loads(STATE_FILE.read_text())
    return {}
def save_state(s: dict):
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))


# ─── Helpers ───────────────────────────────────────────────────────────
def sh(cmd: str, cwd: str = None, check: bool = True, timeout: int = 60):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                        text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"sh fail [{cmd}]: {r.stderr}")
    return r.stdout, r.stderr, r.returncode


def gh_api(method: str, path: str, body=None):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"token {GH_TOKEN}",
                  "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def render_api(method: str, path: str, body=None):
    url = f"https://api.render.com/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"Bearer {RENDER_TOKEN}",
                  "Accept": "application/json",
                  "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read().decode()
            return r.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


# ─── PHASE 1: FORK ─────────────────────────────────────────────────────
def fork_engine(entry: dict, work_dir: str = "/tmp/multica/repos") -> str:
    """Clone engine-template, swap signal_detector, patch config. Returns local path."""
    name = entry["name"]
    repo_path = f"{work_dir}/{name}"
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    if Path(repo_path).exists():
        shutil.rmtree(repo_path)

    # 1. Clone template
    sh(f"git clone -q https://{GH_TOKEN}@github.com/Dapperscyphozoa/engine-template.git {repo_path}")
    sh(f"rm -rf .git", cwd=repo_path)

    # 2. Replace signal_detector.py
    Path(f"{repo_path}/engine/signal_detector.py").write_text(STRATEGIES[name])

    # 3. Patch render.yaml: name, cloid_prefix, primary/secondary, strategy params
    rp = Path(f"{repo_path}/render.yaml")
    txt = rp.read_text()
    txt = txt.replace('name: engine-template', f'name: {name}')
    txt = txt.replace('value: "engine-template"', f'value: "{name}"')
    txt = txt.replace('value: "tmpl_"', f'value: "{entry["cloid_prefix"]}"')
    txt = txt.replace('value: "BTC,ETH,SOL,LINK"', f'value: "{entry["primary"]}"', 1)
    txt = txt.replace('value: "AVAX,DOGE,BNB,XRP"', f'value: "{entry["secondary"]}"', 1)
    # Inject extra STRATEGY_PARAMS env vars below STRATEGY_TIMEFRAME
    extra_env = []
    for k, v in entry["strategy_params"].items():
        extra_env.append(f'      - key: STRATEGY_{k.upper()}\n        value: "{v}"')
    extra_env_block = "\n" + "\n".join(extra_env)
    txt = txt.replace(
        '      - key: CANDLES_HISTORY\n        value: "200"',
        f'      - key: CANDLES_HISTORY\n        value: "200"{extra_env_block}'
    )
    # Patch TRADE_PARAMS via env
    for k, v in entry["trade_params"].items():
        env_key = k.upper()
        # Replace default values
        old_key_block = None
        for default_val in ["1.8", "2.0", "4.0", "4.5", "48", "36", "14"]:
            cand = f'      - key: {env_key}\n        value: "{default_val}"'
            if cand in txt:
                old_key_block = cand
                break
        if old_key_block:
            txt = txt.replace(old_key_block,
                              f'      - key: {env_key}\n        value: "{v}"', 1)
    rp.write_text(txt)

    # 4. Patch config.py to also read STRATEGY_*_* env vars (so STRATEGY_PARAMS gets the new keys)
    cp = Path(f"{repo_path}/engine/config.py")
    cfg = cp.read_text()
    # Build STRATEGY_PARAMS injection
    extra_params_lines = []
    for k in entry["strategy_params"]:
        env_key = f"STRATEGY_{k.upper()}"
        # Decide type by value
        v = entry["strategy_params"][k]
        if isinstance(v, bool):
            cast = f'os.environ.get("{env_key}", "{int(v)}") == "1"'
        elif isinstance(v, int):
            cast = f'int(os.environ.get("{env_key}", "{v}"))'
        elif isinstance(v, float):
            cast = f'float(os.environ.get("{env_key}", "{v}"))'
        else:
            cast = f'os.environ.get("{env_key}", "{v}")'
        extra_params_lines.append(f'    "{k}": {cast},')
    extra_params_block = "\n".join(extra_params_lines)
    cfg = cfg.replace(
        '    # ... fork adds its own keys here\n}',
        f'{extra_params_block}\n    # fork keys above\n}}'
    )
    cp.write_text(cfg)

    # 5. README — minimal note
    Path(f"{repo_path}/README.md").write_text(
        f"# {name}\n\nForked from engine-template by multica orchestrator.\n"
        f"Strategy: see `engine/signal_detector.py`.\n\nPM-coordinated, PAPER mode by default.\n"
    )

    # 6. Verify syntax of changed files
    for f in ["engine/signal_detector.py", "engine/config.py"]:
        out, _, rc = sh(f"python3 -c 'import ast; ast.parse(open(\"{repo_path}/{f}\").read())'",
                        check=False)
        if rc != 0:
            raise RuntimeError(f"{name}: {f} syntax fail")

    return repo_path


# ─── PHASE 2: PUSH ─────────────────────────────────────────────────────
def push_engine(entry: dict, repo_path: str) -> str:
    """Create GH repo + push. Returns repo full_name."""
    name = entry["name"]

    # Create repo (idempotent)
    status, payload = gh_api("GET", f"/repos/Dapperscyphozoa/{name}")
    if status == 404:
        s2, p2 = gh_api("POST", "/user/repos", {
            "name": name,
            "description": f"Cyber Psycho engine — {name}",
            "private": False,
            "auto_init": False,
        })
        if s2 not in (201, 200):
            raise RuntimeError(f"repo create fail [{name}]: {p2}")

    # git init + commit + push
    sh("git init -q -b main", cwd=repo_path)
    sh("git add .", cwd=repo_path)
    sh(f'git -c user.email=mca@cp.local -c user.name=multica commit -q -m "initial: {name} from engine-template (multica)"',
       cwd=repo_path)
    sh(f"git remote add origin https://{GH_TOKEN}@github.com/Dapperscyphozoa/{name}.git",
       cwd=repo_path, check=False)
    sh("git push -u origin main -f", cwd=repo_path, timeout=120)
    return f"Dapperscyphozoa/{name}"


# ─── PHASE 3: DEPLOY (Render service create) ───────────────────────────
def deploy_engine(entry: dict, hl_wallet: str = HL_WALLET_DEFAULT,
                   hl_private_key: str = "") -> dict:
    """Create Render service. Returns service info."""
    name = entry["name"]
    # Check if exists already
    status, payload = render_api("GET", f"/services?name={name}&limit=10")
    if isinstance(payload, list):
        for item in payload:
            svc = item.get("service", {})
            if svc.get("name") == name:
                return {"id": svc.get("id"), "url": svc.get("serviceDetails", {}).get("url"),
                        "existed": True}

    body = {
        "type": "web_service",
        "name": name,
        "ownerId": RENDER_OWNER,
        "repo": f"https://github.com/Dapperscyphozoa/{name}",
        "branch": "main",
        "rootDir": "",
        "buildCommand": "pip install -r requirements.txt",
        "startCommand": "python3 server.py",
        "autoDeploy": "yes",
        "serviceDetails": {
            "env": "python",
            "plan": "starter",
            "region": "oregon",
            "healthCheckPath": "/health",
            "disk": {
                "name": f"{name}-state",
                "mountPath": "/var/data",
                "sizeGB": 1,
            },
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "python3 server.py",
            },
        },
        "envVars": [
            {"key": "ENGINE_NAME", "value": name},
            {"key": "CLOID_PREFIX", "value": entry["cloid_prefix"]},
            {"key": "LIVE_TRADING", "value": "0"},
            {"key": "PM_CHECK_ENABLED", "value": "1"},
            {"key": "PM_URL", "value": PM_URL},
            {"key": "PRIMARY_UNIVERSE", "value": entry["primary"]},
            {"key": "SECONDARY_UNIVERSE", "value": entry["secondary"]},
            {"key": "HL_WALLET", "value": hl_wallet},
            {"key": "HL_PRIVATE_KEY", "value": hl_private_key},
            {"key": "STATE_DIR", "value": "/var/data"},
        ],
    }
    # Inject strategy params + trade params as env vars
    for k, v in entry["strategy_params"].items():
        body["envVars"].append({"key": f"STRATEGY_{k.upper()}", "value": str(v)})
    for k, v in entry["trade_params"].items():
        body["envVars"].append({"key": k.upper(), "value": str(v)})

    status, payload = render_api("POST", "/services", body)
    if status not in (201, 200):
        raise RuntimeError(f"render create [{name}] fail [{status}]: {payload}")
    svc = payload.get("service") or payload
    return {"id": svc.get("id"), "url": svc.get("serviceDetails", {}).get("url"),
            "existed": False, "raw": payload}


# ─── PHASE 4: MONITOR ─────────────────────────────────────────────────
def get_deploy_status(service_id: str) -> dict:
    status, payload = render_api("GET", f"/services/{service_id}/deploys?limit=1")
    if isinstance(payload, list) and payload:
        d = payload[0].get("deploy", {})
        return {"status": d.get("status"), "id": d.get("id"),
                "created": d.get("createdAt"), "finished": d.get("finishedAt")}
    return {"status": "unknown"}


def get_deploy_logs(service_id: str, deploy_id: str, n: int = 100) -> str:
    status, payload = render_api("GET", f"/services/{service_id}/deploys/{deploy_id}/logs")
    if isinstance(payload, list):
        return "\n".join(l.get("message", "") for l in payload[-n:])
    return str(payload)


def smoke_test(url: str) -> dict:
    """Hit /health. Returns {ok, status, body}."""
    if not url:
        return {"ok": False, "reason": "no_url"}
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=15) as r:
            body = json.loads(r.read())
            return {"ok": True, "body": body}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


# ─── ORCHESTRATION ────────────────────────────────────────────────────
def run_one(entry: dict, state: dict) -> dict:
    name = entry["name"]
    s = state.setdefault(name, {})
    print(f"\n══ {name} ══", flush=True)

    # FORK
    if "fork_done" not in s:
        try:
            path = fork_engine(entry)
            s["fork_done"] = True
            s["repo_path"] = path
            print(f"  [fork] {name} → {path}", flush=True)
        except Exception as e:
            s["fork_err"] = str(e)
            print(f"  [fork] FAIL: {e}", flush=True)
            return s
    save_state(state)

    # PUSH
    if "push_done" not in s:
        try:
            full = push_engine(entry, s["repo_path"])
            s["push_done"] = True
            s["repo_full"] = full
            print(f"  [push] {name} → {full}", flush=True)
        except Exception as e:
            s["push_err"] = str(e)
            print(f"  [push] FAIL: {e}", flush=True)
            return s
    save_state(state)

    # DEPLOY
    if "deploy_done" not in s:
        try:
            d = deploy_engine(entry)
            s["deploy_done"] = True
            s["service_id"] = d.get("id")
            s["url"] = d.get("url") or f"https://{name}.onrender.com"
            print(f"  [deploy] {name} → {s['service_id']} ({s['url']})", flush=True)
        except Exception as e:
            s["deploy_err"] = str(e)
            print(f"  [deploy] FAIL: {e}", flush=True)
            return s
    save_state(state)
    return s


def run_all_phases(manifest=MANIFEST):
    state = load_state()
    print(f"=== multica orchestrator | {len(manifest)} engines ===", flush=True)

    # Phase 1+2+3 serialized per engine but engines can be parallelized
    # Use ThreadPoolExecutor for parallel pushes (network-bound)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(run_one, e, state): e["name"] for e in manifest}
        for f in concurrent.futures.as_completed(futures):
            name = futures[f]
            try:
                f.result()
            except Exception as e:
                print(f"  [{name}] exception: {e}", flush=True)
    save_state(state)
    return state


def monitor_deploys(state: dict, timeout_min: int = 12):
    """Phase 4: poll each service's deploy status. Print logs on failure."""
    print("\n=== monitor deploys ===", flush=True)
    deadline = time.time() + timeout_min * 60
    pending = {n: s for n, s in state.items() if s.get("service_id")}
    done = {}
    while pending and time.time() < deadline:
        for name in list(pending):
            sid = pending[name]["service_id"]
            ds = get_deploy_status(sid)
            st = ds.get("status")
            if st in ("live", "succeeded"):
                done[name] = "live"
                print(f"  [{name}] LIVE", flush=True)
                pending.pop(name)
            elif st in ("build_failed", "update_failed", "deploy_failed", "canceled"):
                logs = get_deploy_logs(sid, ds.get("id"), n=80)
                done[name] = "FAILED"
                state[name]["deploy_status"] = st
                state[name]["last_logs"] = logs[-3000:]
                print(f"  [{name}] FAILED ({st}). Logs tail:\n{logs[-1500:]}", flush=True)
                pending.pop(name)
            else:
                # building or update_in_progress
                pass
        if pending:
            time.sleep(20)
    for name in pending:
        state[name]["deploy_status"] = "timeout"
        done[name] = "TIMEOUT"
        print(f"  [{name}] TIMEOUT after {timeout_min}m", flush=True)
    save_state(state)
    return done


def smoke_all(state: dict):
    print("\n=== smoke test /health ===", flush=True)
    for name, s in state.items():
        url = s.get("url")
        if not url:
            continue
        r = smoke_test(url)
        s["smoke"] = r
        print(f"  [{name}] {'OK' if r['ok'] else 'FAIL'}: {r.get('body', r.get('reason'))}",
              flush=True)
    save_state(state)


if __name__ == "__main__":
    state = run_all_phases()
    if any(s.get("deploy_done") for s in state.values()):
        monitor_deploys(state)
        smoke_all(state)
    print("\n=== final state ===")
    for n, s in state.items():
        flags = []
        for k in ("fork_done","push_done","deploy_done"):
            flags.append(("✓" if s.get(k) else "✗") + k.replace("_done",""))
        print(f"  {n}: {' '.join(flags)} | url={s.get('url')} | smoke={s.get('smoke', {}).get('ok')}")
