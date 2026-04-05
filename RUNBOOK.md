# AlgoPlutus — Going Live Runbook

> **Read this entire document before switching `DRY_RUN=false`.**

---

## Phase 1 — Prerequisites

### 1.1 Dhan API Setup
1. Log in to [Dhan Console](https://console.dhan.co)
2. Navigate to **API Hub** → **Create Token**
3. Note your **Client ID** and **Access Token**
4. Verify Intraday (`INTRADAY`) product type is enabled on your account
5. Ensure your account has **sufficient funds** (minimum ₹1,00,000 recommended)

### 1.2 Telegram Bot Setup
1. Open Telegram → message `@BotFather` → send `/newbot`
2. Follow prompts to get your **Bot Token**
3. Message your new bot once (say anything)
4. Get your Chat ID: visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat":{"id":...}`

### 1.3 Generate Strong Secrets
```bash
# Generate JWT secret (run this in terminal)
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_hex(32))"

# Generate WebSocket token
python3 -c "import secrets; print('WS_TOKEN=' + secrets.token_hex(16))"
```
Copy these into your `.env` file.

---

## Phase 2 — Dry-Run Validation

> **Always complete a full week of dry-run before going live.**

### 2.1 Start dry-run stack
```bash
make validate   # Must show 0 failures
make up         # Start all services
```

### 2.2 Monitor for 1 full week
```bash
make monitor                    # Live dashboard
make logs                       # Full log stream
python3 user_data/scripts/healthcheck.py  # Service health
```

**Check daily:**
- [ ] Bot is starting and stopping with NSE market hours
- [ ] Telegram alerts arriving at market open/close
- [ ] No critical errors in `user_data/logs/freqtrade.log`
- [ ] FreqAI model training completing (look for "Training complete" in logs)
- [ ] P&L CSV being written to `user_data/logs/daily_pnl.csv`

### 2.3 Review dry-run results
```bash
# Check P&L summary
cat user_data/logs/daily_pnl.csv

# View open trades API
curl -s http://localhost:8080/api/v1/profit -u freqtrader:YOUR_PASS | python3 -m json.tool
```

**Only proceed if:**
- [ ] Win rate > 45% over dry-run period
- [ ] No consecutive multi-day losses
- [ ] Strategy trades at least 3–5 times per week

---

## Phase 3 — Pre-Live Checklist

Complete every item before flipping `DRY_RUN=false`:

- [ ] Dry-run ran for at least **5 trading days** without critical errors
- [ ] Dhan credentials tested (curl Dhan API manually: `python3 -c "from dhanhq import dhanhq; d=dhanhq('ID','TOKEN'); print(d.get_fund_limits())"`)
- [ ] Capital amount in `.env` (`INITIAL_BALANCE_INR`) matches actual Dhan account balance
- [ ] `MAX_DAILY_LOSS_PCT=0.02` (2%) is acceptable — this is your hard stop for the day
- [ ] `MAX_TRADE_SIZE_INR=50000` is within your per-trade risk tolerance
- [ ] Telegram alerts confirmed working during dry-run
- [ ] `JWT_SECRET_KEY` and `FT_API_PASSWORD` are strong random values (not defaults)
- [ ] `.env` is in `.gitignore` (confirmed: `git check-ignore .env`)
- [ ] FreqAI model has been successfully trained (check `user_data/freqaimodels/` is non-empty)

---

## Phase 4 — Going Live

### 4.1 Switch to live mode
```bash
make live
# Type 'YES_I_UNDERSTAND' when prompted
```

This will:
1. Set `DRY_RUN=false` in `.env`
2. Rebuild and restart all services
3. All orders will now route to your real Dhan account

### 4.2 Verify live mode immediately
```bash
# Check proxy reports live mode
curl http://localhost:8000/health

# Expected: "dry_run": false, "dhan_connected": true

# Watch logs for first real order
make logs-bot
```

### 4.3 First-day live monitoring
**Watch every 30 minutes during first live day:**
```bash
make monitor
```

**Verify via Dhan app:**
- Check your Dhan mobile app / console for matching orders
- Each AlgoPlutus order will show as `INTRADAY` orders on NSE

---

## Phase 5 — Daily Operations

### Routine (every trading day)
```bash
# 9:00 AM IST — Stack should auto-start; verify
make status
make monitor

# End of day (after 3:30 PM IST)
cat user_data/logs/daily_pnl.csv | tail -5
```

### Weekly maintenance
```bash
# Re-run hyperopt to keep model fresh
make hyperopt

# Review and clear old logs if >500MB
du -sh user_data/logs/
```

### Emergency: Stop trading immediately
```bash
# Stop bot via API (preserves positions — trailing stop still active)
curl -X POST http://localhost:8080/api/v1/stop -u freqtrader:YOUR_PASS

# OR nuclear option — stop everything
make down
```

### Emergency: Force-exit all positions
```bash
# Force-exit via API
curl -X POST http://localhost:8080/api/v1/forceexit \
  -u freqtrader:YOUR_PASS \
  -H "Content-Type: application/json" \
  -d '{"tradeid": "all"}'
```

---

## Risk Limits Reference

| Parameter | Default | Description |
|---|---|---|
| `MAX_DAILY_LOSS_PCT` | 2% | Bot halts when daily loss > 2% of capital |
| `MAX_TRADE_SIZE_INR` | ₹50,000 | Warning if single trade exceeds this |
| `INITIAL_BALANCE_INR` | ₹1,00,000 | Reference capital for % calculations |
| `stoploss` | -4% | Hard per-trade stop |
| `trailing_stop` | 1% offset | Trailing stop activates at 2.5% profit |
| `max_open_trades` | 6 | Maximum concurrent positions |
| EOD force-exit | 3:10 PM IST | All positions closed before market end |

---

## Troubleshooting

### Bot not starting
```bash
make logs-bot        # Check for config errors
docker compose ps    # Check container status
```

### FreqAI model not training
```bash
# Model needs enough historical data (30 days by default)
make download-data   # Re-download data
# Then restart:
make restart
```

### Dhan orders not placing
```bash
# Test Dhan API directly
docker compose exec ccxt_proxy python3 -c "
import os; from dhanhq import dhanhq
d = dhanhq(os.environ['DHAN_CLIENT_ID'], os.environ['DHAN_ACCESS_TOKEN'])
print(d.get_fund_limits())
"
```

### Telegram alerts not working
```bash
# Test manually
curl -X POST "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" \
  -d "chat_id=$TELEGRAM_CHAT_ID&text=Test from AlgoPlutus"
```

---

## Support

- Freqtrade docs: https://www.freqtrade.io
- FreqAI docs: https://www.freqtrade.io/en/stable/freqai/
- Dhan API docs: https://dhanhq.co/docs/v2/
