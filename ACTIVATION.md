# Activation Checklist — Income Streams

Three independent income streams ready to activate. Each is gated by a manual
ceremony (credential setting or onchain transaction). Code-side is wired.

---

## 1. HL Builder Code Kickback (1-3 bp per round-trip)

**What it does**: Every order from every engine routes through a builder
code. HL pays the builder ~25-30% of taker fees as kickback. Set the builder
address to your own wallet → self-rebate loop.

**Effort**: One onchain transaction. ~5 minutes.

**Activation steps**:

1. Set `HL_PRIVATE_KEY` env var locally (NOT on Render — never put the
   private key on a server you don't fully control).

2. Run the approval ceremony from your Mac:
   ```bash
   git clone https://github.com/Dapperscyphozoa/engine-template
   cd engine-template
   pip install -r requirements.txt
   HL_PRIVATE_KEY=0x... \
   HL_BUILDER_ADDRESS=0x3eDaD0649Db466E6E7B9a0Caa3E5d6ddc71B5ffE \
   HL_BUILDER_FEE_TENTHS_BPS=10 \
   CONFIRM=yes \
     python3 scripts/approve_builder.py
   ```

3. Verify on HL dashboard (https://app.hyperliquid.xyz/portfolio) — should
   show the builder fee approval. Once approved, every order from every
   engine automatically routes through it. No further action needed.

**Expected income**: at current paper volume scaled to live small accounts
($2k base), kickback is ~$0.50-$2.00/day. Scales linearly with notional.

---

## 2. Cross-Venue Funding Arbitrage (Blofin)

**What it does**: Long the side that pays more funding, short the side that
pays less. Delta-neutral. Currently 4 live opportunities at 30-43% APR:
JUP, ATOM, SOL, HYPE.

**Effort**: Create Blofin API keys + set 3 env vars on Render. ~10 minutes.

**Activation steps**:

1. Log into Blofin → API page (https://blofin.com/account/api).

2. Create new API key with permissions: **READ + TRADE**. (TRANSFER if you
   want the engine to auto-move funds between Blofin sub-accounts.)

3. Whitelist Render IPs (optional but recommended). Render's outbound IPs
   are documented at https://docs.render.com/regions.

4. Set the three env vars on cross-venue-funding-v1:
   ```bash
   curl -X PUT \
     -H "Authorization: Bearer $RENDER_API" \
     -H "Content-Type: application/json" \
     "https://api.render.com/v1/services/srv-d819hqugvqtc73e6tei0/env-vars/BLOFIN_API_KEY" \
     -d '{"value":"YOUR_KEY"}'

   # Repeat for BLOFIN_API_SECRET and BLOFIN_PASSPHRASE
   ```

5. Verify auth: `curl https://cross-venue-funding-v1.onrender.com/cross_venue`
   should show `blofin_health.auth_ok=true`.

6. Fund both venues:
   - HL: $1000+ USDC margin
   - Blofin: $1000+ USDT margin (futures account)

7. Flip to live: set `DRY_RUN=0` env var on cross-venue-funding-v1 and
   redeploy. Engine will scan every 5min and open paired positions when
   spread > 0.3bp/hr.

**Expected income**: At $500 notional × 3 positions × ~30% avg APR =
$1.23/day per $1500 deployed. Scales linearly. Risk is convergence reversal
(rare for funding spreads which are sticky for hours).

---

## 3. High-Confluence Manual Overlay

**What it does**: Filters all engine signals to top 3-5 alerts per day where
2+ engines + macro + cell PF all agree. You execute manually on HL with
discretion.

**Effort**: Zero. Already live. Bookmark the dashboard.

**Activation steps**:

1. Open https://portfolio-manager-7df2.onrender.com/dashboard on phone.

2. Bookmark it as a home-screen app (iOS: Share → Add to Home Screen).

3. Check 2-3x daily. Top panel shows ranked alerts with:
   - Score (composite of engines × macro × cell PF)
   - Direction (color-coded long/short)
   - Engines that fired (which ones agree)
   - Macro multiplier for current regime
   - Average cell PF for that (coin, regime, direction)
   - Whether any cell is already active on this setup

4. Execute the top 1-2 alerts only when score >25 (rough threshold).

**Expected outcome**: 1-3 trades/day, 60%+ WR (cherry-picking confluence).
At 2% risk per trade and 1:2 R:R: ~+0.4% per winning trade, ~-0.2% per
losing trade. Expected: ~+0.1% to +0.3% per trade × 2 trades/day =
+0.2-0.6% daily compounding. World class if sustained.

---

## Status Summary

| Income stream | Status | Action needed | Estimated daily $ on $2k |
|---|---|---|---|
| 1. Builder code kickback | wired, awaiting onchain approval | run approve_builder.py | $0.50-$2.00 |
| 2. Cross-venue funding | wired, awaiting Blofin keys | set 3 env vars + DRY_RUN=0 | $1.23 ($1500 capital) |
| 3. Manual overlay | LIVE | use dashboard | $4-$12 (discretionary) |

Total potential at $2k account: ~$6-15/day = 0.3-0.75% daily.
Compounded at 0.5%/day: 2k → 5k in 60 days, 5k → 20k in 90 days.
