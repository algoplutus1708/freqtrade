#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  AlgoPlutus — Backtest Runner
#  Usage: bash scripts/backtest.sh [days]
#  Example: bash scripts/backtest.sh 90   (backtest 90 days of data)
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

DAYS="${1:-30}"
STRATEGY="FreqAI_IndianMarket"
CONFIG="user_data/config_freqai.json"
TIMERANGE_END=$(date +%Y%m%d)
TIMERANGE_START=$(date -v-${DAYS}d +%Y%m%d 2>/dev/null || date -d "-${DAYS} days" +%Y%m%d)
TIMERANGE="${TIMERANGE_START}-${TIMERANGE_END}"

echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║    AlgoPlutus — Backtesting Runner       ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${GREEN}Strategy  :${NC} $STRATEGY"
echo -e "  ${GREEN}Timerange :${NC} $TIMERANGE ($DAYS days)"
echo -e "  ${GREEN}Config    :${NC} $CONFIG"
echo ""

# First download data if needed
echo -e "${CYAN}Step 1/2: Downloading historical data...${NC}"
docker compose --env-file .env run --rm freqtrade download-data \
  --config "$CONFIG" \
  --timerange "$TIMERANGE" \
  --timeframe 5m 15m 1h \
  --pairs RELIANCE/INR TCS/INR INFY/INR HDFCBANK/INR \
          WIPRO/INR ICICIBANK/INR BAJFINANCE/INR HINDUNILVR/INR \
          SBIN/INR KOTAKBANK/INR

echo ""
echo -e "${CYAN}Step 2/2: Running backtest...${NC}"
docker compose --env-file .env run --rm freqtrade backtesting \
  --config "$CONFIG" \
  --strategy "$STRATEGY" \
  --freqaimodel LightGBMRegressor \
  --timerange "$TIMERANGE" \
  --export trades \
  --export-filename "user_data/backtest_results/backtest_${TIMERANGE}.json"

echo ""
echo -e "${GREEN}${BOLD}✅  Backtest complete. Results saved to user_data/backtest_results/${NC}"
echo -e "${CYAN}View detailed report: docker compose run --rm freqtrade backtesting-show${NC}"
