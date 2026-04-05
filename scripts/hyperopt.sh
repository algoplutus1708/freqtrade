#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  AlgoPlutus — Hyperopt Runner
#  Usage: bash scripts/hyperopt.sh [epochs] [spaces]
#  Example: bash scripts/hyperopt.sh 200 buy sell
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

EPOCHS="${1:-100}"
SPACES="${2:-buy sell}"
STRATEGY="FreqAI_IndianMarket"
CONFIG="user_data/config_freqai.json"
DAYS=30
TIMERANGE_END=$(date +%Y%m%d)
TIMERANGE_START=$(date -v-${DAYS}d +%Y%m%d 2>/dev/null || date -d "-${DAYS} days" +%Y%m%d)
TIMERANGE="${TIMERANGE_START}-${TIMERANGE_END}"

echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║    AlgoPlutus — Hyperopt Runner          ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${GREEN}Strategy  :${NC} $STRATEGY"
echo -e "  ${GREEN}Epochs    :${NC} $EPOCHS"
echo -e "  ${GREEN}Spaces    :${NC} $SPACES"
echo -e "  ${GREEN}Timerange :${NC} $TIMERANGE"
echo ""
echo -e "${CYAN}This may take several minutes...${NC}"
echo ""

docker compose --env-file .env run --rm freqtrade hyperopt \
  --config "$CONFIG" \
  --strategy "$STRATEGY" \
  --freqaimodel LightGBMRegressor \
  --hyperopt-loss SharpeHyperOptLoss \
  --spaces $SPACES \
  --epochs "$EPOCHS" \
  --timerange "$TIMERANGE" \
  --random-state 42 \
  -j -1

echo ""
echo -e "${GREEN}${BOLD}✅  Hyperopt complete. Results in user_data/hyperopt_results/${NC}"
echo -e "${CYAN}Show best params: docker compose run --rm freqtrade hyperopt-show --best${NC}"
