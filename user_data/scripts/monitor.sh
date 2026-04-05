#!/bin/bash
# AlgoPlutus — Live Monitor Dashboard
# Usage: ./monitor.sh [FT_HOST] [FT_PORT] [FT_USER] [FT_PASS]

HOST="${1:-localhost}"
PORT="${2:-8080}"
USER="${3:-freqtrader}"
PASS="${4:-AlgoPlutus@2026!}"
BASE="http://${HOST}:${PORT}/api/v1"
AUTH="--user ${USER}:${PASS}"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

clear
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}      🤖  AlgoPlutus — Live Trading Dashboard${NC}"
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${NC}"
echo ""

# ── Bot Status ────────────────────────────────────────────────────────────────
STATUS=$(curl -sf ${AUTH} "${BASE}/status" 2>/dev/null)
PING=$(curl -sf ${AUTH} "${BASE}/ping" 2>/dev/null)

if [ $? -ne 0 ] || [ -z "$PING" ]; then
    echo -e "${RED}❌ Freqtrade API unreachable at ${BASE}${NC}"
    exit 1
fi

BOT_STATE=$(curl -sf ${AUTH} "${BASE}/count" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"Open trades: {d.get('current',0)}/{d.get('max',0)}\")" 2>/dev/null || echo "N/A")
echo -e "${GREEN}✅ Bot Online${NC}  |  ${BOT_STATE}"
echo ""

# ── Open Trades ───────────────────────────────────────────────────────────────
echo -e "${BOLD}📈 OPEN TRADES${NC}"
echo -e "${CYAN}─────────────────────────────────────────────────────${NC}"
echo "$STATUS" | python3 -c "
import sys, json
try:
    trades = json.load(sys.stdin)
    if not trades:
        print('  No open trades')
    else:
        for t in trades:
            pair   = t.get('pair','?')
            profit = t.get('profit_pct', 0) * 100
            pnl    = t.get('profit_abs', 0)
            dur    = t.get('open_trade_duration', '?')
            tag    = t.get('enter_tag', '')
            color  = '\033[0;32m' if profit >= 0 else '\033[0;31m'
            nc     = '\033[0m'
            print(f'  {pair:<20} {color}{profit:+6.2f}%{nc}  ₹{pnl:+,.2f}  [{dur}]  {tag}')
except Exception as e:
    print(f'  Error: {e}')
" 2>/dev/null
echo ""

# ── Profit Summary ────────────────────────────────────────────────────────────
echo -e "${BOLD}💰 PERFORMANCE SUMMARY${NC}"
echo -e "${CYAN}─────────────────────────────────────────────────────${NC}"
curl -sf ${AUTH} "${BASE}/profit" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    total    = d.get('profit_all_coin', 0)
    today    = d.get('profit_today_abs', 0)
    trades   = d.get('trade_count', 0)
    win      = d.get('winning_trades', 0)
    wr       = (win/trades*100) if trades else 0
    avg_dur  = d.get('avg_duration', 'N/A')
    print(f'  Total P&L     : ₹{total:+,.2f}')
    print(f\"  Today's P&L   : ₹{today:+,.2f}\")
    print(f'  Total Trades  : {trades}  (Win rate: {wr:.1f}%)')
    print(f'  Avg Duration  : {avg_dur}')
except Exception as e:
    print(f'  Error: {e}')
" 2>/dev/null
echo ""

# ── Proxy Health ──────────────────────────────────────────────────────────────
echo -e "${BOLD}🔗 SERVICES${NC}"
echo -e "${CYAN}─────────────────────────────────────────────────────${NC}"
PROXY_HEALTH=$(curl -sf "http://${HOST}:8000/health" 2>/dev/null)
if [ $? -eq 0 ]; then
    DRY=$(echo "$PROXY_HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print('DRY-RUN' if d.get('dry_run') else '⚡ LIVE')" 2>/dev/null)
    echo -e "  CCXT Proxy    : ${GREEN}✅ Healthy${NC}  [${DRY}]"
else
    echo -e "  CCXT Proxy    : ${RED}❌ Down${NC}"
fi

IST=$(python3 -c "from datetime import datetime, timedelta; print((datetime.utcnow()+timedelta(hours=5,minutes=30)).strftime('%H:%M:%S IST'))" 2>/dev/null)
echo -e "  Server Time   : ${IST}"
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
echo -e "  Run: watch -n 30 ./user_data/scripts/monitor.sh"
echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
