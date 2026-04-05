# ══════════════════════════════════════════════════════════════════
#   AlgoPlutus — Production Makefile
#   Usage:  make <target>
#   All commands require Docker + .env to be configured
# ══════════════════════════════════════════════════════════════════

.PHONY: help up down restart logs monitor validate backtest hyperopt \
        download-data live dry-run status clean shell-proxy shell-bot

# Force bash for all recipes
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Colours
BOLD  := \033[1m
CYAN  := \033[36m
GREEN := \033[32m
YELLOW:= \033[33m
RED   := \033[31m
NC    := \033[0m

help: ## 📖 Show this help
	@echo ""
	@echo -e "$(BOLD)$(CYAN)  AlgoPlutus — AI Algo Trading Operations$(NC)"
	@echo -e "$(CYAN)  ─────────────────────────────────────────────────$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

# ── Deployment ──────────────────────────────────────────────────────────────
validate: ## ✅ Validate .env before deployment
	@bash scripts/validate.sh

up: validate ## 🚀 Start full stack in DRY-RUN mode
	@echo -e "$(GREEN)Starting AlgoPlutus stack...$(NC)"
	docker compose --env-file .env up -d --build
	@echo -e "$(GREEN)Stack started. Run 'make monitor' to view live status.$(NC)"

down: ## 🛑 Stop all services
	@echo -e "$(YELLOW)Stopping AlgoPlutus stack...$(NC)"
	docker compose --env-file .env down
	@echo -e "$(YELLOW)Stack stopped.$(NC)"

restart: ## 🔄 Restart all services
	@$(MAKE) down
	@$(MAKE) up

rebuild: ## 🔨 Force rebuild all images and restart
	@echo -e "$(YELLOW)Rebuilding all Docker images...$(NC)"
	docker compose --env-file .env build --no-cache
	docker compose --env-file .env up -d

# ── Monitoring ───────────────────────────────────────────────────────────────
logs: ## 📜 Tail all service logs
	docker compose --env-file .env logs -f --tail=100

logs-bot: ## 📜 Tail only freqtrade bot logs
	docker compose --env-file .env logs -f --tail=100 freqtrade

logs-proxy: ## 📜 Tail only CCXT proxy logs
	docker compose --env-file .env logs -f --tail=100 ccxt_proxy

logs-risk: ## 📜 Tail risk manager logs
	docker compose --env-file .env logs -f --tail=100 risk_manager

monitor: ## 📊 Live trading dashboard (refreshes every 30s)
	@bash -c 'while true; do bash user_data/scripts/monitor.sh; sleep 30; done'

status: ## 🔍 Show container status
	@docker compose --env-file .env ps
	@echo ""
	@echo -e "$(CYAN)Proxy health:$(NC)"
	@curl -sf http://localhost:8000/health | python3 -m json.tool 2>/dev/null || echo "  Proxy unreachable"

health: ## 🏥 Check health of all services
	@python3 user_data/scripts/healthcheck.py

# ── Data & Backtesting ────────────────────────────────────────────────────────
download-data: ## 📥 Download historical data for all pairs
	@bash scripts/download_data.sh

backtest: ## 📈 Run backtest (last 30 days by default)
	@bash scripts/backtest.sh

hyperopt: ## 🔬 Run hyperopt (100 epochs)
	@bash scripts/hyperopt.sh

# ── Trading Mode ─────────────────────────────────────────────────────────────
dry-run: ## 🧪 Ensure DRY_RUN=true and restart
	@sed -i '' 's/^DRY_RUN=.*/DRY_RUN=true/' .env
	@echo -e "$(GREEN)DRY_RUN set to true.$(NC)"
	@$(MAKE) restart

live: ## ⚡ SWITCH TO LIVE TRADING — requires confirmation
	@echo -e "$(RED)$(BOLD)⚠️  WARNING: This will switch to LIVE TRADING with REAL MONEY$(NC)"
	@echo -e "$(RED)Ensure your Dhan credentials are set in .env before proceeding.$(NC)"
	@read -p "Type 'YES_I_UNDERSTAND' to continue: " confirm; \
	if [ "$$confirm" = "YES_I_UNDERSTAND" ]; then \
	  sed -i '' 's/^DRY_RUN=.*/DRY_RUN=false/' .env; \
	  echo -e "$(RED)DRY_RUN=false set. Restarting...$(NC)"; \
	  $(MAKE) restart; \
	else \
	  echo -e "$(GREEN)Aborted. DRY_RUN remains true.$(NC)"; \
	fi

# ── Maintenance ──────────────────────────────────────────────────────────────
clean: ## 🧹 Remove old Docker images and logs
	docker compose --env-file .env down --rmi local --volumes --remove-orphans
	@echo -e "$(YELLOW)Old containers/images removed.$(NC)"

prune: ## 🗑️  Docker system prune (frees disk space)
	docker system prune -f

shell-bot: ## 🐚 Open shell in freqtrade container
	docker compose --env-file .env exec freqtrade bash

shell-proxy: ## 🐚 Open shell in proxy container
	docker compose --env-file .env exec ccxt_proxy bash

# ── Git ──────────────────────────────────────────────────────────────────────
push: ## 📤 Push latest changes to GitHub
	@git add -A
	@git commit -m "chore: update production config $$(date +'%Y-%m-%d %H:%M')"
	@git push origin develop
	@echo -e "$(GREEN)Pushed to develop branch.$(NC)"
