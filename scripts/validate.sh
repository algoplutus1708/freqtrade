#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  AlgoPlutus — Pre-deployment Validation Script
#  Checks all requirements before bringing up the Docker stack
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

PASS=0; FAIL=0; WARN=0

pass()  { echo -e "  ${GREEN}✅  $1${NC}"; ((PASS++)); }
fail()  { echo -e "  ${RED}❌  $1${NC}"; ((FAIL++)); }
warn()  { echo -e "  ${YELLOW}⚠️   $1${NC}"; ((WARN++)); }
header(){ echo -e "\n${BOLD}${CYAN}── $1 ──────────────────────────────────────────${NC}"; }

echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║  AlgoPlutus — Pre-deployment Validator   ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ── Docker ────────────────────────────────────────────────────────────────────
header "Docker"
if docker info &>/dev/null; then
  pass "Docker daemon is running"
else
  fail "Docker daemon is NOT running — start Docker Desktop first"
fi

if docker compose version &>/dev/null; then
  pass "Docker Compose plugin available"
else
  fail "Docker Compose not found — run: pip install docker-compose"
fi

# ── .env file ─────────────────────────────────────────────────────────────────
header ".env File"
if [[ ! -f ".env" ]]; then
  fail ".env file not found — copy from env.example: cp env.example .env"
  exit 1
else
  pass ".env file exists"
fi

# Source .env safely (ignore export errors)
set +a
# shellcheck disable=SC1091
while IFS='=' read -r key value; do
  [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
  declare "$key"="${value}" 2>/dev/null || true
done < .env
set -a

# ── Required variables ─────────────────────────────────────────────────────────
header "Required Environment Variables"

check_var() {
  local var="$1"
  local val="${!var:-}"
  if [[ -z "$val" ]]; then
    fail "$var is not set"
  elif [[ "$val" == YOUR_* || "$val" == CHANGE_ME* || "$val" == GENERATE_* ]]; then
    fail "$var still has placeholder value: '$val'"
  else
    pass "$var is set"
  fi
}

check_var "FT_API_USERNAME"
check_var "FT_API_PASSWORD"
check_var "JWT_SECRET_KEY"
check_var "WS_TOKEN"
check_var "INITIAL_BALANCE_INR"

# ── Telegram (warn only if placeholder) ───────────────────────────────────────
header "Telegram Alerts"
TELE_TOKEN="${TELEGRAM_TOKEN:-}"
TELE_CHAT="${TELEGRAM_CHAT_ID:-}"

if [[ -z "$TELE_TOKEN" || "$TELE_TOKEN" == YOUR_* ]]; then
  warn "TELEGRAM_TOKEN not set — Telegram alerts will be disabled"
else
  pass "TELEGRAM_TOKEN is set"
fi

if [[ -z "$TELE_CHAT" || "$TELE_CHAT" == YOUR_* ]]; then
  warn "TELEGRAM_CHAT_ID not set — Telegram alerts will be disabled"
else
  pass "TELEGRAM_CHAT_ID is set"
fi

# ── Dhan credentials (warn only in dry-run) ─────────────────────────────────
header "Dhan Broker Credentials"
DR="${DRY_RUN:-true}"
DHAN_ID="${DHAN_CLIENT_ID:-}"
DHAN_TOK="${DHAN_ACCESS_TOKEN:-}"

if [[ "$DR" == "true" ]]; then
  pass "DRY_RUN=true — Dhan credentials not required for dry-run"
else
  if [[ -z "$DHAN_ID" || "$DHAN_ID" == YOUR_* ]]; then
    fail "DRY_RUN=false but DHAN_CLIENT_ID is not set"
  else
    pass "DHAN_CLIENT_ID is set"
  fi
  if [[ -z "$DHAN_TOK" || "$DHAN_TOK" == YOUR_* ]]; then
    fail "DRY_RUN=false but DHAN_ACCESS_TOKEN is not set"
  else
    pass "DHAN_ACCESS_TOKEN is set"
  fi
fi

# ── Risk parameters ────────────────────────────────────────────────────────────
header "Risk Parameters"
MAX_LOSS="${MAX_DAILY_LOSS_PCT:-0.02}"
MAX_TRADE="${MAX_TRADE_SIZE_INR:-50000}"
INIT_BAL="${INITIAL_BALANCE_INR:-100000}"

pass "MAX_DAILY_LOSS_PCT = ${MAX_LOSS} ($(echo "scale=0; ${MAX_LOSS}*100" | bc)%)"
pass "MAX_TRADE_SIZE_INR = ₹${MAX_TRADE}"
pass "INITIAL_BALANCE_INR = ₹${INIT_BAL}"

# Sanity check: loss limit > 0 and < 1
if (( $(echo "$MAX_LOSS <= 0 || $MAX_LOSS >= 1" | bc -l) )); then
  fail "MAX_DAILY_LOSS_PCT must be between 0 and 1 (e.g., 0.02 for 2%)"
fi

# ── Required files ─────────────────────────────────────────────────────────────
header "Required Files"
required_files=(
  "user_data/config_freqai.json"
  "user_data/strategies/FreqAI_IndianMarket.py"
  "user_data/proxy/ccxt_proxy.py"
  "user_data/scripts/session_controller.py"
  "user_data/scripts/risk_manager.py"
  "docker/Dockerfile.custom"
  "docker/Dockerfile.session"
  "user_data/proxy/Dockerfile"
)

for f in "${required_files[@]}"; do
  if [[ -f "$f" ]]; then
    pass "Found: $f"
  else
    fail "Missing: $f"
  fi
done

# ── JWT secret strength check ─────────────────────────────────────────────────
header "Security"
JWT="${JWT_SECRET_KEY:-}"
if [[ ${#JWT} -lt 32 ]]; then
  fail "JWT_SECRET_KEY is too short (min 32 chars). Generate: python3 -c \"import secrets; print(secrets.token_hex(32))\""
else
  pass "JWT_SECRET_KEY length OK (${#JWT} chars)"
fi

API_PASS="${FT_API_PASSWORD:-}"
if [[ ${#API_PASS} -lt 12 ]]; then
  warn "FT_API_PASSWORD is short (${#API_PASS} chars) — use at least 12 characters"
else
  pass "FT_API_PASSWORD length OK"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}── Validation Summary ──────────────────────────────────${NC}"
echo -e "  ${GREEN}✅  Passed : $PASS${NC}"
echo -e "  ${YELLOW}⚠️   Warnings: $WARN${NC}"
echo -e "  ${RED}❌  Failed : $FAIL${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo -e "  ${RED}${BOLD}VALIDATION FAILED — fix the errors above before deploying.${NC}"
  exit 1
else
  echo -e "  ${GREEN}${BOLD}✅  All checks passed. Safe to run 'make up'${NC}"
  exit 0
fi
