"""
deep_search_v2.py — sim with proper concurrency control.

Critical change: simulate the portfolio, not 8 independent coin walks.
A signal fires only if max_concurrent_open hasn't been hit globally.
"""
from __future__ import annotations
import os, sys, time, json, statistics, subprocess, pickle
from pathlib import Path
import urllib.request
import pandas as pd

sys.path.insert(0, '/tmp/multica-fresh')
from deep_search import load_candles, load_funding, CACHE_DIR


def sim_portfolio(candles: dict, funding: dict, detector, trade_params: dict,
                   warmup_pct: float = 0.20, invert: bool = False,
                   max_open_global: int = 3,
                   max_open_per_coin: int = 1) -> list:
    """
    Simulate the entire portfolio chronologically.
    Signals are evaluated on each coin's bars, but a trade only opens if:
      - no existing open trade on this coin (or below max_open_per_coin)
      - global open trades < max_open_global
    """
    # Find common time index — use bars from the first coin to define the timeline
    # (all coins have the same length and roughly same times for HL)
    coins = sorted(candles.keys())
    bar_count = min(len(candles[c]) for c in coins)
    warmup = max(50, int(bar_count * warmup_pct))
    
    open_trades: list = []   # active trades being walked forward
    closed_trades: list = []
    
    for i in range(warmup, bar_count - 1):
        # Step 1: walk forward all open trades by one bar
        still_open = []
        for t in open_trades:
            coin = t['coin']
            if i >= len(candles[coin]): 
                # Coin ran out of data — force-close at last close
                t['exit_px'] = float(candles[coin].iloc[-1]['close'])
                t['close_reason'] = 'NO_DATA'
                _finalise(t)
                closed_trades.append(t)
                continue
            bar = candles[coin].iloc[i]
            h, l = float(bar['high']), float(bar['low'])
            if t['is_long']:
                if l <= t['sl']:
                    t['exit_px'] = t['sl']; t['close_reason'] = 'SL'
                    _finalise(t); closed_trades.append(t); continue
                if h >= t['tp']:
                    t['exit_px'] = t['tp']; t['close_reason'] = 'TP'
                    _finalise(t); closed_trades.append(t); continue
            else:
                if h >= t['sl']:
                    t['exit_px'] = t['sl']; t['close_reason'] = 'SL'
                    _finalise(t); closed_trades.append(t); continue
                if l <= t['tp']:
                    t['exit_px'] = t['tp']; t['close_reason'] = 'TP'
                    _finalise(t); closed_trades.append(t); continue
            # Time-stop check
            t['bars_held'] += 1
            if t['bars_held'] >= t['max_hold']:
                t['exit_px'] = float(bar['close']); t['close_reason'] = 'TIME'
                _finalise(t); closed_trades.append(t); continue
            still_open.append(t)
        open_trades = still_open
        
        # Step 2: evaluate signals on each coin, open if concurrency allows
        # Random coin order would be fairest; we go alphabetical for determinism
        if len(open_trades) >= max_open_global:
            continue  # portfolio full
        
        coins_with_open = {t['coin'] for t in open_trades}
        for coin in coins:
            if len(open_trades) >= max_open_global: break
            if coin in coins_with_open and max_open_per_coin == 1: continue
            
            bars = candles[coin]
            if i >= len(bars): continue
            sl = bars.iloc[:i+1]; sl.attrs['coin'] = coin
            # Attach funding if present
            if funding and coin in funding:
                # Already attached on bars; just ensure column exists
                if 'funding' not in sl.columns:
                    sl = sl.assign(funding=funding[coin]['fundingRate'].reindex(sl.index, method='ffill'))
            try: sig = detector(sl)
            except: sig = None
            if sig is None: continue
            if not {'ref_price','sl_px','tp_px','is_long','max_hold_bars'}.issubset(sig.keys()): continue
            
            entry = float(sig['ref_price'])
            sl_o = float(sig['sl_px']); tp_o = float(sig['tp_px'])
            is_long_o = bool(sig['is_long']); hold = int(sig['max_hold_bars'])
            is_long = (not is_long_o) if invert else is_long_o
            sl_d = abs(entry - sl_o); tp_d = abs(entry - tp_o)
            if is_long:
                sl_p = entry - sl_d; tp_p = entry + tp_d
            else:
                sl_p = entry + sl_d; tp_p = entry - tp_d
            sl_pct = abs(entry - sl_p) / entry
            if sl_pct < 0.001 or sl_pct > 0.10: continue
            
            open_trades.append({
                'coin': coin, 'entry_ts': bars.index[i],
                'entry': entry, 'sl': sl_p, 'tp': tp_p,
                'is_long': is_long, 'max_hold': hold,
                'sl_pct': sl_pct, 'bars_held': 0,
            })
            coins_with_open.add(coin)
    
    # Close any still-open at end of period
    for t in open_trades:
        bars = candles[t['coin']]
        t['exit_px'] = float(bars.iloc[-1]['close'])
        t['close_reason'] = 'PERIOD_END'
        _finalise(t)
        closed_trades.append(t)
    
    return closed_trades


def _finalise(t: dict):
    if t['is_long']:
        pnl_pct = (t['exit_px'] - t['entry']) / t['entry']
    else:
        pnl_pct = (t['entry'] - t['exit_px']) / t['entry']
    t['pnl_pct'] = pnl_pct
    t['pnl_r'] = pnl_pct / t['sl_pct']
    t['ts'] = t['entry_ts']


def audit_strict(trades: list) -> dict:
    if not trades or len(trades) < 10:
        return {'pass': False, 'n': len(trades) if trades else 0, 'issues': ['too few trades']}
    trades = sorted(trades, key=lambda t: t['ts'])
    n = len(trades)
    rs = [t['pnl_r'] for t in trades]
    wins = [r for r in rs if r > 0]
    wr = len(wins) / n
    gw = sum(r for r in rs if r > 0); gl = abs(sum(r for r in rs if r <= 0))
    pf = (gw / gl) if gl > 0 else float('inf')
    avg_r = statistics.mean(rs); std_r = statistics.stdev(rs) if n > 1 else 1.0
    sharpe = avg_r / std_r if std_r > 0 else 0
    eq = 0; peak = 0; max_dd = 0
    for r in rs:
        eq += r
        if eq > peak: peak = eq
        if peak - eq > max_dd: max_dd = peak - eq
    recovery = (sum(rs) / max_dd) if max_dd > 0 else float('inf')
    split = int(n * 0.67)
    is_t = trades[:split]; oos_t = trades[split:]
    def q(ts):
        if not ts: return None
        r = [t['pnl_r'] for t in ts]
        if not r: return None
        gw = sum(x for x in r if x > 0); gl = abs(sum(x for x in r if x <= 0))
        return {'n': len(r), 'wr': len([x for x in r if x>0])/len(r),
                 'pf': (gw/gl) if gl > 0 else float('inf'), 'sumR': sum(r)}
    is_s = q(is_t); oos_s = q(oos_t)
    days = pd.Series([t['ts'].date() for t in trades]).value_counts()
    
    result = {'n': n, 'wr': wr, 'pf': pf, 'sumR': sum(rs),
              'avg_r': avg_r, 'sharpe': sharpe, 'max_dd': max_dd,
              'recovery': recovery, 'max_per_day': int(days.max()),
              'is_pf': is_s['pf'] if is_s else None,
              'oos_pf': oos_s['pf'] if oos_s else None,
              'is_n': is_s['n'] if is_s else 0, 'oos_n': oos_s['n'] if oos_s else 0}
    
    issues = []
    if n < 25: issues.append(f"n={n}<25")
    if pf < 1.3: issues.append(f"pf={pf:.2f}<1.3")
    if oos_s and oos_s['pf'] < 1.0: issues.append(f"oos_pf={oos_s['pf']:.2f}<1.0")
    if oos_s and is_s and is_s['pf'] > 0 and oos_s['pf'] < 0.7 * is_s['pf']:
        issues.append(f"oos_degraded")
    if sharpe < 0.10: issues.append(f"sharpe={sharpe:.3f}<0.10")
    if recovery < 1.2: issues.append(f"rec={recovery:.2f}<1.2")
    if days.max() > 8: issues.append(f"mxD={days.max()}>8")
    result['issues'] = issues
    result['pass'] = (len(issues) == 0)
    return result


def fmt_audit(a):
    if not a: return "n/a"
    pass_str = "PASS" if a.get('pass') else f"FAIL[{','.join(a.get('issues',[]))[:50]}]"
    return (f"n={a['n']:<3} WR={a['wr']*100:5.1f}% PF={a['pf']:5.2f} "
            f"sumR={a['sumR']:+6.1f} Sh={a['sharpe']:+.3f} dd={a['max_dd']:.1f} "
            f"rec={a['recovery']:+.2f} mxD={a['max_per_day']} "
            f"oosPF={a.get('oos_pf') or 0:.2f} {pass_str}")


def run_portfolio(engine_dir: str, params: dict, candles, funding,
                    interval: str, invert: bool = False,
                    max_open_global: int = 3,
                    max_open_per_coin: int = 1) -> dict:
    sys.path = [p for p in sys.path if not p.startswith('/tmp/sweep-deep-')
                 and not p.startswith('/tmp/combo-')]
    sys.path.insert(0, engine_dir)
    for k in list(sys.modules):
        if k.startswith('engine'): del sys.modules[k]
    os.environ['ENGINE_NAME'] = 'combo'
    os.environ['STATE_DIR'] = '/tmp/combo-state'
    for k, v in params.items():
        os.environ[k] = str(v)
    from engine.config import TRADE_PARAMS, ACTIVE_UNIVERSE
    from engine.signal_detector import evaluate_latest_bar
    
    # Restrict to coins we have in cache
    coins = [c for c in ACTIVE_UNIVERSE if c in candles]
    candles_sub = {c: candles[c] for c in coins}
    
    trades = sim_portfolio(candles_sub, funding, evaluate_latest_bar, TRADE_PARAMS,
                            invert=invert, max_open_global=max_open_global,
                            max_open_per_coin=max_open_per_coin)
    return audit_strict(trades)
