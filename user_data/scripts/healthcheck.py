#!/usr/bin/env python3
"""
AlgoPlutus — Unified Health Check
Verifies all services and reports detailed status.
Usage: python3 user_data/scripts/healthcheck.py
"""

import sys
import os
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
FT_HOST  = os.environ.get("FT_HOST",  "localhost")
FT_PORT  = os.environ.get("FT_PORT",  "8080")
FT_USER  = os.environ.get("FT_API_USERNAME", "freqtrader")
FT_PASS  = os.environ.get("FT_API_PASSWORD", "AlgoPlutus@2026!")
PROXY_HOST = os.environ.get("PROXY_HOST", "localhost")

FT_BASE    = f"http://{FT_HOST}:{FT_PORT}/api/v1"
PROXY_BASE = f"http://{PROXY_HOST}:8000"

# ── Colours ───────────────────────────────────────────────────────────────────
OK   = "\033[32m✅\033[0m"
FAIL = "\033[31m❌\033[0m"
WARN = "\033[33m⚠️\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
NC   = "\033[0m"


def check(label: str, fn) -> bool:
    try:
        result = fn()
        print(f"  {OK}  {label}: {result}")
        return True
    except Exception as e:
        print(f"  {FAIL}  {label}: {e}")
        return False


def ft_get(path: str) -> dict:
    r = requests.get(f"{FT_BASE}{path}", auth=(FT_USER, FT_PASS), timeout=8)
    r.raise_for_status()
    return r.json()


def proxy_get(path: str) -> dict:
    r = requests.get(f"{PROXY_BASE}{path}", timeout=8)
    r.raise_for_status()
    return r.json()


def main():
    print(f"\n{BOLD}{CYAN}  AlgoPlutus — Health Check{NC}")
    print(f"{CYAN}  {'─'*50}{NC}\n")

    all_ok = True

    # ── CCXT Proxy ─────────────────────────────────────────────────────────────
    print(f"{BOLD}1. CCXT Proxy (:{8000}){NC}")

    def proxy_health():
        d = proxy_get("/health")
        mode = "DRY-RUN 🧪" if d.get("dry_run") else "⚡ LIVE"
        syms = len(d.get("symbols", []))
        return f"{mode} | {syms} symbols | v{d.get('version', '?')}"

    def proxy_ping():
        proxy_get("/api/v3/ping")
        return "OK"

    def proxy_exinfo():
        d = proxy_get("/api/v3/exchangeInfo")
        n = len(d.get("symbols", []))
        return f"{n} symbols in exchange info"

    def proxy_kline():
        d = proxy_get("/api/v3/klines?symbol=RELIANCEINR&interval=5m&limit=5")
        return f"{len(d)} candles returned"

    all_ok &= check("Health", proxy_health)
    all_ok &= check("Ping", proxy_ping)
    all_ok &= check("Exchange Info", proxy_exinfo)
    all_ok &= check("Klines (RELIANCE)", proxy_kline)

    print()

    # ── Freqtrade Bot ──────────────────────────────────────────────────────────
    print(f"{BOLD}2. Freqtrade Bot (:{FT_PORT}){NC}")

    def ft_ping():
        ft_get("/ping")
        return "OK"

    def ft_state():
        d = ft_get("/status")
        count = len(d) if isinstance(d, list) else 0
        return f"{count} open trades"

    def ft_profit():
        d = ft_get("/profit")
        pnl = d.get("profit_all_coin", 0) or 0
        trades = d.get("trade_count", 0) or 0
        return f"P&L: ₹{pnl:.2f} | Total trades: {trades}"

    def ft_version():
        d = ft_get("/version")
        return d.get("version", "unknown")

    all_ok &= check("Ping / Auth", ft_ping)
    all_ok &= check("Open Trades", ft_state)
    all_ok &= check("P&L Summary", ft_profit)
    all_ok &= check("Bot Version", ft_version)

    print()

    # ── Log Files ──────────────────────────────────────────────────────────────
    print(f"{BOLD}3. Log Files{NC}")
    log_dir = Path("user_data/logs")
    log_files = [
        "freqtrade.log",
        "session_controller.log",
        "risk_manager.log",
    ]
    for lf in log_files:
        path = log_dir / lf
        if path.exists():
            size = path.stat().st_size
            print(f"  {OK}  {lf}: {size/1024:.1f} KB")
        else:
            print(f"  {WARN}  {lf}: not found (starts on first run)")

    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"{CYAN}  {'─'*50}{NC}")
    if all_ok:
        print(f"  {OK} {BOLD}All critical checks passed — system healthy{NC}\n")
        sys.exit(0)
    else:
        print(f"  {FAIL} {BOLD}Some checks failed — review above{NC}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
