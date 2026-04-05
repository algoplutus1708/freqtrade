#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  AlgoPlutus — Historical Data Download Script
#  Downloads OHLCV data for all 10 NSE pairs across required timeframes
#  Usage: bash scripts/download_data.sh [days]
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

DAYS="${1:-60}"
CONFIG="user_data/config_freqai.json"
TIMERANGE_END=$(date +%Y%m%d)
TIMERANGE_START=$(date -v-${DAYS}d +%Y%m%d 2>/dev/null || date -d "-${DAYS} days" +%Y%m%d)
TIMERANGE="${TIMERANGE_START}-${TIMERANGE_END}"

PAIRS=(
  "RELIANCE/INR"
  "TCS/INR"
  "INFY/INR"
  "HDFCBANK/INR"
  "WIPRO/INR"
  "ICICIBANK/INR"
  "BAJFINANCE/INR"
  "HINDUNILVR/INR"
  "SBIN/INR"
  "KOTAKBANK/INR"
)

TIMEFRAMES=("5m" "15m" "1h")

echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║  AlgoPlutus — Data Download              ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${GREEN}Pairs      :${NC} ${#PAIRS[@]} NSE stocks"
echo -e "  ${GREEN}Timeframes :${NC} ${TIMEFRAMES[*]}"
echo -e "  ${GREEN}Timerange  :${NC} $TIMERANGE ($DAYS days)"
echo ""

# The proxy must be running to serve yfinance data
if ! curl -sf http://localhost:8000/health &>/dev/null; then
  echo -e "${CYAN}Starting CCXT Proxy for data download...${NC}"
  docker compose --env-file .env up -d ccxt_proxy
  echo -e "  Waiting for proxy to be ready..."
  sleep 10
fi

for TF in "${TIMEFRAMES[@]}"; do
  echo -e "${CYAN}Downloading ${TF} data...${NC}"
  docker compose --env-file .env run --rm freqtrade download-data \
    --config "$CONFIG" \
    --timerange "$TIMERANGE" \
    --timeframe "$TF" \
    --pairs "${PAIRS[@]}"
  echo -e "  ${GREEN}✅  ${TF} done${NC}"
done

echo ""
echo -e "${GREEN}${BOLD}✅  All data downloaded to user_data/data/${NC}"
echo -e "${CYAN}Data location: user_data/data/binance/${NC}"
ls -lh user_data/data/binance/ 2>/dev/null || true
