#!/usr/bin/env python3
"""
AlgoPlutus — Risk Management Service v2.0
==========================================
• Polls Freqtrade REST API every 60s
• Enforces MAX_DAILY_LOSS_PCT — force-exits all positions, stops bot
• Validates per-trade size against MAX_TRADE_SIZE_INR
• Writes daily P&L CSV to logs/
• Sends Telegram alerts on any breach
• Resets daily limits at midnight
• Graceful SIGTERM handler
"""

import os
import csv
import sys
import time
import signal
import logging
import logging.handlers
import requests
from datetime import datetime, date
from typing import Optional

LOG_PATH = os.environ.get("LOG_PATH", "/freqtrade/user_data/logs/risk_manager.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RiskMgr] %(levelname)s: %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("RiskManager")

# ── Config from env ─────────────────────────────────────────────────────────
FT_API_URL      = os.environ.get("FT_API_URL",       "http://freqtrade:8080/api/v1")
FT_API_USER     = os.environ.get("FT_API_USERNAME",  "freqtrader")
FT_API_PASS     = os.environ.get("FT_API_PASSWORD",  "")
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT   = os.environ.get("TELEGRAM_CHAT_ID", "")
MAX_DAILY_LOSS  = float(os.environ.get("MAX_DAILY_LOSS_PCT",  "0.02"))
MAX_TRADE_INR   = float(os.environ.get("MAX_TRADE_SIZE_INR",  "50000"))
INITIAL_BALANCE = float(os.environ.get("INITIAL_BALANCE_INR", "100000"))
PNL_CSV_PATH    = "/freqtrade/user_data/logs/daily_pnl.csv"
POLL_INTERVAL   = 60  # seconds


# ── Helpers ──────────────────────────────────────────────────────────────────
def telegram(msg: str):
    if not TELEGRAM_TOKEN or "YOUR_" in TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT,
                "text":       f"🚨 *AlgoPlutus Risk*\n{msg}",
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")


def ft(method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
    auth = (FT_API_USER, FT_API_PASS)
    url  = f"{FT_API_URL}{path}"
    try:
        r = (requests.get if method == "GET" else requests.post)(
            url, json=body or {}, auth=auth, timeout=15
        )
        return r.json() if r.status_code < 300 else None
    except Exception as e:
        logger.warning(f"FT API {path}: {e}")
        return None


def force_exit_all():
    trades = ft("GET", "/status") or []
    if not isinstance(trades, list):
        trades = []
    for t in trades:
        tid = t.get("trade_id")
        if tid:
            resp = ft("POST", "/forceexit", {"tradeid": str(tid)})
            logger.warning(f"Force-exit trade {tid}: {resp}")


def write_pnl_csv(today: date, pnl: float, trade_count: int, win_rate: float):
    file_exists = os.path.exists(PNL_CSV_PATH)
    with open(PNL_CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["date", "pnl_inr", "trade_count", "win_rate_pct", "initial_balance_inr"])
        w.writerow([today.isoformat(), f"{pnl:.2f}", trade_count, f"{win_rate:.2f}", f"{INITIAL_BALANCE:.2f}"])
    logger.info(f"P&L CSV: {today} | ₹{pnl:.2f} | {trade_count} trades | {win_rate:.1f}% WR")


def check_trade_sizes():
    """Warn if any single open trade exceeds MAX_TRADE_SIZE_INR."""
    trades = ft("GET", "/status") or []
    if not isinstance(trades, list):
        return
    for t in trades:
        stake_amount = float(t.get("stake_amount", 0))
        open_rate    = float(t.get("open_rate", 0))
        amount       = float(t.get("amount", 0))
        trade_value  = amount * open_rate if open_rate else stake_amount

        if trade_value > MAX_TRADE_INR:
            pair = t.get("pair", "?")
            logger.warning(
                f"⚠️  Trade {pair} value ₹{trade_value:,.0f} exceeds "
                f"MAX_TRADE_SIZE_INR ₹{MAX_TRADE_INR:,.0f}"
            )
            telegram(
                f"⚠️ *Trade Size Warning*\n"
                f"Pair: {pair}\n"
                f"Value: ₹{trade_value:,.0f}\n"
                f"Limit: ₹{MAX_TRADE_INR:,.0f}"
            )


# ── Risk Manager ─────────────────────────────────────────────────────────────
class RiskManager:
    def __init__(self):
        self.daily_loss_breached = False
        self.last_pnl_date: Optional[date] = None
        self._running = True

        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT,  self._shutdown)

    def _shutdown(self, signum, frame):
        logger.info(f"Received signal {signum} — Risk Manager shutting down.")
        telegram("⚠️ Risk Manager stopped (shutdown signal received).")
        self._running = False

    def run(self):
        logger.info("═══════════════════════════════════════════════════")
        logger.info("  AlgoPlutus Risk Manager v2.0 — STARTED")
        logger.info(f"  Max daily loss    : {MAX_DAILY_LOSS*100:.1f}% of ₹{INITIAL_BALANCE:,.0f}")
        logger.info(f"  Max trade size    : ₹{MAX_TRADE_INR:,.0f}")
        logger.info(f"  Poll interval     : {POLL_INTERVAL}s")
        logger.info("═══════════════════════════════════════════════════")
        telegram(
            f"🛡 *Risk Manager v2.0 started*\n"
            f"Max daily loss: {MAX_DAILY_LOSS*100:.1f}% (₹{INITIAL_BALANCE * MAX_DAILY_LOSS:,.0f})\n"
            f"Max trade size: ₹{MAX_TRADE_INR:,.0f}"
        )

        while self._running:
            try:
                self._check()
            except Exception as e:
                logger.error(f"Risk check error: {e}")
            time.sleep(POLL_INTERVAL)

        logger.info("Risk Manager exited cleanly.")

    def _check(self):
        now   = datetime.now()
        today = now.date()

        # Reset daily breach flag at midnight
        if self.last_pnl_date and self.last_pnl_date != today:
            self.daily_loss_breached = False
            logger.info("🌅 New trading day — risk limits reset.")
            telegram("🌅 *New trading day* — daily risk limits reset.")

        profit_data = ft("GET", "/profit")
        if not profit_data:
            logger.warning("Could not fetch profit data from Freqtrade API")
            return

        # Extract P&L metrics safely
        daily_pnl   = float(profit_data.get("profit_today_abs") or 0)
        total_trades = int(profit_data.get("trade_count") or 0)
        winning      = int(profit_data.get("winning_trades") or 0)
        win_rate     = (winning / total_trades * 100) if total_trades else 0

        max_loss_inr = INITIAL_BALANCE * MAX_DAILY_LOSS
        loss_pct     = abs(daily_pnl) / INITIAL_BALANCE if (daily_pnl < 0 and INITIAL_BALANCE > 0) else 0

        logger.info(
            f"Daily P&L: ₹{daily_pnl:.2f} | "
            f"Max loss allowed: ₹{max_loss_inr:.2f} | "
            f"Trades: {total_trades} | WR: {win_rate:.1f}%"
        )

        # ── Per-trade size check ───────────────────────────────────────────────
        check_trade_sizes()

        # ── Daily loss limit breach ────────────────────────────────────────────
        if daily_pnl < 0 and loss_pct >= MAX_DAILY_LOSS and not self.daily_loss_breached:
            logger.critical(
                f"🚨 DAILY LOSS LIMIT BREACHED: ₹{daily_pnl:.2f} "
                f"({loss_pct*100:.2f}%) > max {MAX_DAILY_LOSS*100:.1f}%"
            )
            telegram(
                f"🚨 *DAILY LOSS LIMIT BREACHED*\n"
                f"Loss: ₹{abs(daily_pnl):,.2f} ({loss_pct*100:.2f}%)\n"
                f"Limit: {MAX_DAILY_LOSS*100:.1f}%\n"
                f"Action: *Forcing exits + stopping bot*"
            )
            force_exit_all()
            time.sleep(5)
            ft("POST", "/stop")
            self.daily_loss_breached = True
            logger.warning("Bot stopped by risk manager for the day.")

        # ── End-of-session daily P&L summary ──────────────────────────────────
        mkt_close_h = 15
        if now.hour == mkt_close_h and now.minute >= 35 and self.last_pnl_date != today:
            write_pnl_csv(today, daily_pnl, total_trades, win_rate)
            self.last_pnl_date = today
            telegram(
                f"📊 *Daily Summary — {today}*\n"
                f"P&L: ₹{daily_pnl:+,.2f}\n"
                f"Trades: {total_trades} | Win Rate: {win_rate:.1f}%\n"
                f"Balance used: ₹{INITIAL_BALANCE:,.0f}"
            )


if __name__ == "__main__":
    RiskManager().run()
