# Blofin Live Status — 2026-05-12

## What's live

✅ Blofin API auth working (key + secret, no passphrase required for this key)
✅ Blofin USDT balance reads: 4.18e-09 USDT (essentially zero — needs funding)
✅ HL + Blofin both reachable from cross-venue-funding-v1 service
✅ 6 paired DRY_RUN positions open across both directions:
    JUP, SOL, ATOM (long HL / short Blofin — collect HL funding)
    DOGE, WIF, SUI (short HL / long Blofin — collect Blofin funding)
✅ Projected income (DRY_RUN): $2.46/day on $3000 notional ($500 per leg × 6)
✅ Scan interval: 10min, 30 coin universe
✅ Notifier infrastructure ready (Telegram + Discord, just need tokens)

## What's needed to flip to LIVE

1. **Fund Blofin USDT** — currently 4.18e-09. Deposit at least $300-1000 to cover 6 × $500 positions.
   Note: Blofin shows futures balance — make sure USDT is in the FUTURES account (use /asset/transfer if it's in funding/spot).

2. **Fund HL USDC** — similar. Probably already has the paper wallet 0x3eDaD0... funded with some test USDC.
   Check live HL margin via the existing HL_ADDRESS.

3. **Flip DRY_RUN=0** on cross-venue-funding-v1 service:
   ```
   curl -X PUT \
     -H "Authorization: Bearer rnd_GbOYfugIiAl0ihJR2O2wOjYNpWUz" \
     -H "Content-Type: application/json" \
     "https://api.render.com/v1/services/srv-d819hqugvqtc73e6tei0/env-vars/DRY_RUN" \
     -d '{"value":"0"}'
   ```
   Then redeploy.

4. **Lower MAX_OPEN_POSITIONS** if you don't have enough capital for 6 × $500.
   Recommend starting with `MAX_OPEN_POSITIONS=2` and `POSITION_NOTIONAL_USD=200` for a $500 starting account.

5. **Set up notifications** (optional but recommended):
   Create Telegram bot (@BotFather), get token + chat_id.
   Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env vars on PM service.

## Live verification

Once activated:
- Check `https://cross-venue-funding-v1.onrender.com/cross_venue` — positions should show `live: true`
- Check Blofin app — should see open SWAP positions in /trade page
- Check HL app — should see open positions in portfolio
- PM dashboard `/dashboard` — funding arb panel will show live opportunities being captured

## Risk notes

1. Both venues must be online. If either goes down, you have unhedged exposure.
2. Funding rate changes every 8 hours on Blofin, every 1 hour on HL.
3. Cross-venue execution is best-effort — Blofin leg might fail after HL leg fills, in which case engine auto-reverses HL.
4. Convergence reversal (spread flips while position open) → stop loss = close paired position immediately.
