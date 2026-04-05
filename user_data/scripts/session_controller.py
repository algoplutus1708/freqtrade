#!/usr/bin/env python3
"""
AlgoPlutus — Production Session Controller v2.0
================================================
• Proper IST timezone via pytz
• NSE holiday calendar 2025 + 2026
• Telegram alerts for every state change (with deduplication)
• Exponential backoff on proxy health failures
• Freqtrade REST API integration (start/stop via API, not docker)
• Graceful SIGTERM / SIGINT handler
• Rotating file logs
"""

import os
import sys
import time
import signal
import datetime
import logging
import logging.handlers
import requests
from typing import Optional

try:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
except ImportError:
    IST = None

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_PATH = os.environ.get("LOG_PATH", "/freqtrade/user_data/logs/session_controller.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("SessionController")

# ── Config from env ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
PROXY_URL      = os.environ.get("PROXY_URL", "http://ccxt_proxy:8000/health")
FT_API_URL     = os.environ.get("FT_API_URL", "http://freqtrade:8080/api/v1")
FT_API_USER    = os.environ.get("FT_API_USERNAME", "freqtrader")
FT_API_PASS    = os.environ.get("FT_API_PASSWORD", "")
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL", "30"))

# ── NSE Holidays ──────────────────────────────────────────────────────────────
# Update this list annually from: https://www.nseindia.com/resources/exchange-communication-holidays
NSE_HOLIDAYS: set = {
    # 2025
    datetime.date(2025, 1, 26),   # Republic Day
    datetime.date(2025, 2, 26),   # Mahashivaratri
    datetime.date(2025, 3, 14),   # Holi
    datetime.date(2025, 3, 31),   # Id-Ul-Fitr (Ramadan Eid)
    datetime.date(2025, 4, 14),   # Dr Ambedkar Jayanti / Ram Navami
    datetime.date(2025, 4, 18),   # Good Friday
    datetime.date(2025, 5, 1),    # Maharashtra Day
    datetime.date(2025, 8, 15),   # Independence Day
    datetime.date(2025, 8, 27),   # Ganesh Chaturthi
    datetime.date(2025, 10, 2),   # Gandhi Jayanti / Dussehra
    datetime.date(2025, 10, 21),  # Diwali Laxmi Pujan (Muhurat trading only)
    datetime.date(2025, 10, 22),  # Diwali Balipratipada
    datetime.date(2025, 11, 5),   # Prakash Gurpurb
    datetime.date(2025, 12, 25),  # Christmas

    # 2026
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 3, 17),   # Holi
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 4, 14),   # Dr Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day
    datetime.date(2026, 8, 15),   # Independence Day
    datetime.date(2026, 10, 2),   # Gandhi Jayanti
    datetime.date(2026, 11, 4),   # Diwali Laxmi Pujan
    datetime.date(2026, 11, 5),   # Diwali Balipratipada
    datetime.date(2026, 12, 25),  # Christmas
}

MARKET_OPEN  = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 30)


# ── Time helpers ──────────────────────────────────────────────────────────────
def ist_now() -> datetime.datetime:
    if IST:
        return datetime.datetime.now(tz=IST).replace(tzinfo=None)
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def is_market_open() -> bool:
    now   = ist_now()
    today = now.date()
    if now.weekday() >= 5:
        return False
    if today in NSE_HOLIDAYS:
        logger.info(f"NSE Holiday: {today} — market closed")
        return False
    t = now.time()
    return MARKET_OPEN <= t < MARKET_CLOSE


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT or "YOUR_" in TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT,
                "text":       f"🤖 *AlgoPlutus*\n{message}",
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


# ── Proxy health ──────────────────────────────────────────────────────────────
def check_proxy_health(fail_count: int = 0) -> bool:
    try:
        r = requests.get(PROXY_URL, timeout=8)
        return r.status_code == 200
    except Exception:
        if fail_count > 0:
            backoff = min(2 ** fail_count, 60)
            time.sleep(backoff)
        return False


# ── Freqtrade API ─────────────────────────────────────────────────────────────
def ft_api_call(method: str, path: str, json_body: Optional[dict] = None) -> Optional[dict]:
    auth = (FT_API_USER, FT_API_PASS)
    url  = f"{FT_API_URL}{path}"
    try:
        r = (requests.get if method == "GET" else requests.post)(
            url, json=json_body or {}, auth=auth, timeout=10
        )
        if r.status_code < 300:
            return r.json()
        logger.warning(f"FT API {method} {path} → {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.warning(f"FT API error ({path}): {e}")
        return None


def start_bot():
    resp = ft_api_call("POST", "/start")
    logger.info(f"Bot start → {resp}")
    send_telegram("✅ *Bot Started* — Indian market is open. Trading begins.")


def stop_bot():
    resp = ft_api_call("POST", "/stop")
    logger.info(f"Bot stop → {resp}")
    send_telegram("🛑 *Bot Stopped* — Market closed. Open positions handled by trailing stop.")


def get_trade_summary() -> str:
    perf = ft_api_call("GET", "/profit")
    if not perf:
        return "P&L data unavailable."
    pnl     = perf.get("profit_all_coin", 0) or 0
    trades  = perf.get("trade_count", 0) or 0
    winning = perf.get("winning_trades", 0) or 0
    wr      = (winning / trades * 100) if trades else 0
    return f"Trades: {trades} | Win%: {wr:.1f}% | P&L: ₹{pnl:,.2f}"


# ── Session Controller ────────────────────────────────────────────────────────
class SessionController:
    def __init__(self):
        self.market_open      = False
        self.proxy_healthy    = False
        self.bot_running      = False
        self.proxy_fail_count = 0
        self._running         = True

        # Register graceful shutdown handlers
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT,  self._shutdown)

    def _shutdown(self, signum, frame):
        logger.info(f"Received signal {signum} — shutting down gracefully...")
        send_telegram("⚠️ Session Controller stopped (received shutdown signal).")
        self._running = False

    def run(self):
        logger.info("═══════════════════════════════════════════════════")
        logger.info("  AlgoPlutus Session Controller v2.0 — STARTED")
        logger.info(f"  Poll interval : {POLL_INTERVAL}s")
        logger.info(f"  Market hours  : {MARKET_OPEN} – {MARKET_CLOSE} IST")
        logger.info(f"  NSE holidays  : {len(NSE_HOLIDAYS)} days configured")
        logger.info("═══════════════════════════════════════════════════")
        send_telegram(
            f"🚀 *Session Controller v2.0 started*\n"
            f"Market hours: {MARKET_OPEN}–{MARKET_CLOSE} IST\n"
            f"Poll: every {POLL_INTERVAL}s"
        )

        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Unexpected tick error: {e}")
            time.sleep(POLL_INTERVAL)

        logger.info("Session Controller exited cleanly.")

    def _tick(self):
        now    = ist_now()
        open_  = is_market_open()
        health = check_proxy_health(self.proxy_fail_count)

        # ── Proxy transitions ─────────────────────────────────────────────────
        if health and not self.proxy_healthy:
            logger.info("✅ CCXT Proxy is HEALTHY")
            send_telegram("✅ *CCXT Proxy healthy* — data feed live.")
            self.proxy_healthy    = True
            self.proxy_fail_count = 0

        elif not health and self.proxy_healthy:
            self.proxy_fail_count += 1
            logger.warning(f"❌ CCXT Proxy UNHEALTHY (failure #{self.proxy_fail_count})")
            send_telegram(f"❌ *CCXT Proxy DOWN* (#{self.proxy_fail_count}) — bot suspended.")
            self.proxy_healthy = False

        elif not health:
            self.proxy_fail_count += 1

        # ── Market transitions ────────────────────────────────────────────────
        if open_ and not self.market_open:
            logger.info(f"🔔 Market OPENED at {now.strftime('%H:%M:%S')} IST")
            send_telegram(f"📈 *NSE Market OPEN* ({now.strftime('%H:%M')} IST)\n{get_trade_summary()}")
            self.market_open = True

        elif not open_ and self.market_open:
            logger.info(f"🔕 Market CLOSED at {now.strftime('%H:%M:%S')} IST")
            self.market_open = False
            send_telegram(f"📉 *NSE Market CLOSED* ({now.strftime('%H:%M')} IST)\n{get_trade_summary()}")

        # ── Bot state management ──────────────────────────────────────────────
        should_run = open_ and health

        if should_run and not self.bot_running:
            start_bot()
            self.bot_running = True

        elif not should_run and self.bot_running:
            stop_bot()
            self.bot_running = False

        logger.info(
            f"[{now.strftime('%H:%M:%S')} IST] "
            f"Market={'OPEN' if open_ else 'CLOSED'} | "
            f"Proxy={'UP' if health else 'DOWN'} | "
            f"Bot={'RUNNING' if self.bot_running else 'STOPPED'}"
        )


if __name__ == "__main__":
    SessionController().run()
