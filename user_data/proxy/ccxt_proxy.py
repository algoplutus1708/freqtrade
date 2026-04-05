"""
AlgoPlutus CCXT Proxy v3.0 — Production
========================================
• Exposes a Binance-compatible REST API so Freqtrade can trade Indian equities
• Market data: real-time OHLCV from yfinance (NSE stocks)
• Order routing: Dhan broker API in live mode, mock responses in dry-run
• TTL-based async cache to avoid hammering yfinance on every candle
• Lazy-init Dhan singleton (no lru_cache on mutable state)
• All 10 NSE stocks in SYMBOL_MAP
"""

import os
import time
import random
import logging
import hashlib
import asyncio
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional, Dict, Any

import yfinance as yf
import uvicorn
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import JSONResponse
from cachetools import TTLCache

# Try dhanhq import (not available in dry-run environments)
try:
    from dhanhq import dhanhq as DhanHQ
    _DHAN_AVAILABLE = True
except ImportError:
    _DHAN_AVAILABLE = False
    DhanHQ = None

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("CCXT-Proxy")

# ── Config from env ───────────────────────────────────────────────────────────
DHAN_CLIENT_ID    = os.environ.get("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")
DRY_RUN           = os.environ.get("DRY_RUN", "true").lower() == "true"

# ── Symbol map: Freqtrade pair → yfinance ticker + Dhan IDs ──────────────────
SYMBOL_MAP: Dict[str, Dict[str, str]] = {
    "RELIANCEINR": {"yf": "RELIANCE.NS", "dhan_id": "500325", "dhan_seg": "NSE_EQ"},
    "TCSINR":      {"yf": "TCS.NS",      "dhan_id": "532540", "dhan_seg": "NSE_EQ"},
    "INFYINR":     {"yf": "INFY.NS",     "dhan_id": "500209", "dhan_seg": "NSE_EQ"},
    "HDFCBANKINR": {"yf": "HDFCBANK.NS", "dhan_id": "500180", "dhan_seg": "NSE_EQ"},
    "WIPROINR":    {"yf": "WIPRO.NS",    "dhan_id": "507685", "dhan_seg": "NSE_EQ"},
    "ICICIBANKINK":{"yf": "ICICIBANK.NS","dhan_id": "532174", "dhan_seg": "NSE_EQ"},
    "BAJFINANCEINR":{"yf":"BAJFINANCE.NS","dhan_id":"500034","dhan_seg": "NSE_EQ"},
    "HINDUNILVRINR":{"yf":"HINDUNILVR.NS","dhan_id":"500696","dhan_seg":"NSE_EQ"},
    "SBININR":     {"yf": "SBIN.NS",     "dhan_id": "500112", "dhan_seg": "NSE_EQ"},
    "KOTAKBANKINR":{"yf": "KOTAKBANK.NS","dhan_id": "500247", "dhan_seg": "NSE_EQ"},
}

# Canonical symbol name mapping (handles "ICICIBANK/INR" → "ICICIBANKINK" quirk)
_PAIR_ALIASES = {
    "ICICIBANKINR": "ICICIBANKINK",
}

INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "60m", "1d": "1d",
}

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "60m": 3_600_000, "1d": 86_400_000,
}

# ── Caches ────────────────────────────────────────────────────────────────────
# TTL cache: key = (symbol, interval, limit), value = klines list
_klines_cache: TTLCache = TTLCache(maxsize=200, ttl=60)   # 60s TTL
_ticker_cache: TTLCache = TTLCache(maxsize=50,  ttl=30)   # 30s TTL
_ei_cache: TTLCache     = TTLCache(maxsize=1,   ttl=300)  # 5-min TTL

_cache_lock = Lock()

# ── Dhan singleton ────────────────────────────────────────────────────────────
_dhan_client: Optional[Any] = None
_dhan_lock = Lock()


def get_dhan_client() -> Optional[Any]:
    """Thread-safe lazy-init singleton for Dhan client."""
    global _dhan_client
    if DRY_RUN or not DHAN_CLIENT_ID or not DHAN_ACCESS_TOKEN or not _DHAN_AVAILABLE:
        return None
    with _dhan_lock:
        if _dhan_client is None:
            try:
                _dhan_client = DhanHQ(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
                logger.info("✅ Dhan client initialised (LIVE mode)")
            except Exception as e:
                logger.error(f"Failed to init Dhan client: {e}")
        return _dhan_client


# ── Helpers ───────────────────────────────────────────────────────────────────
def normalise_symbol(symbol: str) -> str:
    """Normalise symbol to our SYMBOL_MAP key."""
    s = symbol.upper().replace("/", "").replace("-", "")
    return _PAIR_ALIASES.get(s, s)


def symbol_to_yf(symbol: str) -> str:
    s = normalise_symbol(symbol)
    info = SYMBOL_MAP.get(s)
    if info:
        return info["yf"]
    # Fallback: strip INR suffix and append .NS
    base = s.replace("INR", "").replace("INK", "")
    return base + ".NS"


def make_symbol_entry(sym_key: str) -> dict:
    base = sym_key.replace("INR", "").replace("INK", "")
    return {
        "symbol":                    sym_key,
        "status":                    "TRADING",
        "baseAsset":                 base,
        "baseAssetPrecision":        8,
        "quoteAsset":                "INR",
        "quotePrecision":            2,
        "quoteAssetPrecision":       2,
        "baseCommissionPrecision":   8,
        "quoteCommissionPrecision":  2,
        "orderTypes":                ["LIMIT", "MARKET"],
        "icebergAllowed":            True,
        "ocoAllowed":                False,
        "quoteOrderQtyMarketAllowed": True,
        "isSpotTradingAllowed":      True,
        "isMarginTradingAllowed":    False,
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.05",  "maxPrice": "999999.00", "tickSize": "0.05"},
            {"filterType": "LOT_SIZE",     "minQty":   "1",     "maxQty":   "10000.00",  "stepSize": "1"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "100"},
        ],
        "permissions": ["SPOT"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI App
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="AlgoPlutus CCXT Proxy — Indian Market",
    version="3.0.0",
    description="Binance-compatible API proxy for NSE stocks via yfinance + Dhan",
)


# ── Lifecycle ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    mode = "DRY-RUN 🧪" if DRY_RUN else "⚡ LIVE TRADING"
    logger.info(f"AlgoPlutus CCXT Proxy v3.0 starting [{mode}]")
    logger.info(f"Tracking {len(SYMBOL_MAP)} symbols: {list(SYMBOL_MAP.keys())}")
    # Prime Dhan client in live mode
    if not DRY_RUN:
        get_dhan_client()


# ══════════════════════════════════════════════════════════════════════════════
# CORE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status":         "ok",
        "dry_run":        DRY_RUN,
        "dhan_connected": bool(DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN and not DRY_RUN),
        "symbols":        list(SYMBOL_MAP.keys()),
        "server_time":    int(time.time() * 1000),
        "version":        "3.0.0",
    }


@app.get("/api/v3/ping")
async def ping():
    return {}


@app.get("/api/v3/time")
async def server_time():
    return {"serverTime": int(time.time() * 1000)}


@app.get("/api/v3/exchangeInfo")
async def exchange_info():
    cache_key = "exchangeInfo"
    with _cache_lock:
        cached = _ei_cache.get(cache_key)
    if cached:
        return cached

    symbols = [make_symbol_entry(k) for k in SYMBOL_MAP]
    data = {
        "timezone":       "Asia/Kolkata",
        "serverTime":     int(time.time() * 1000),
        "rateLimits":     [],
        "exchangeFilters":[],
        "symbols":        symbols,
    }
    with _cache_lock:
        _ei_cache[cache_key] = data
    logger.info("exchangeInfo refreshed (cached 5min)")
    return data


# ── OHLCV / Klines ───────────────────────────────────────────────────────────
@app.get("/api/v3/klines")
async def get_klines(
    symbol:    str = Query("RELIANCEINR"),
    interval:  str = Query("5m"),
    limit:     int = Query(500),
    startTime: Optional[int] = Query(None),
    endTime:   Optional[int] = Query(None),
):
    cache_key = f"{symbol}|{interval}|{limit}|{startTime}"
    with _cache_lock:
        cached = _klines_cache.get(cache_key)
    if cached:
        return cached

    ticker_name = symbol_to_yf(symbol)
    yf_interval = INTERVAL_MAP.get(interval, "5m")
    logger.info(f"klines → {ticker_name} [{yf_interval}] limit={limit}")

    ticker = yf.Ticker(ticker_name)
    try:
        if startTime:
            start_dt = datetime.fromtimestamp(startTime / 1000)
            min_start = datetime.now() - timedelta(days=59)
            if yf_interval in ("1m", "5m", "15m", "30m") and start_dt < min_start:
                start_dt = min_start
            df = ticker.history(start=start_dt.strftime("%Y-%m-%d"), interval=yf_interval)
        else:
            period = "7d" if yf_interval in ("1m", "5m") else "60d"
            df = ticker.history(period=period, interval=yf_interval)

        if df.empty:
            logger.warning(f"yfinance returned empty data for {ticker_name}")
            return []

        df = df.tail(limit)
        iv_ms = INTERVAL_MS.get(yf_interval, 300_000)

        klines = []
        for ts, row in df.iterrows():
            open_time  = int(ts.timestamp() * 1000)
            close_time = open_time + iv_ms - 1
            klines.append([
                open_time,
                f"{row['Open']:.2f}",
                f"{row['High']:.2f}",
                f"{row['Low']:.2f}",
                f"{row['Close']:.2f}",
                f"{row['Volume']:.2f}",
                close_time,
                f"{row['Volume'] * row['Close']:.2f}",
                int(random.uniform(100, 2000)),
                "0", "0", "0",
            ])

        with _cache_lock:
            _klines_cache[cache_key] = klines
        return klines

    except Exception as e:
        logger.error(f"klines error for {ticker_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Ticker endpoints ─────────────────────────────────────────────────────────
@app.get("/api/v3/ticker/price")
async def ticker_price(symbol: str = Query("RELIANCEINR")):
    ticker_name = symbol_to_yf(symbol)
    cache_key = f"price|{symbol}"
    with _cache_lock:
        cached = _ticker_cache.get(cache_key)
    if cached:
        return cached

    try:
        ticker = yf.Ticker(ticker_name)
        info   = ticker.fast_info
        price  = getattr(info, "last_price", None) or getattr(info, "previous_close", 0) or 0
        result = {"symbol": symbol, "price": f"{price:.2f}"}
        with _cache_lock:
            _ticker_cache[cache_key] = result
        return result
    except Exception as e:
        logger.error(f"ticker/price error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v3/ticker/24hr")
async def ticker_24hr(symbol: str = Query("RELIANCEINR")):
    ticker_name = symbol_to_yf(symbol)
    cache_key = f"24hr|{symbol}"
    with _cache_lock:
        cached = _ticker_cache.get(cache_key)
    if cached:
        return cached

    try:
        ticker = yf.Ticker(ticker_name)
        info   = ticker.fast_info
        price  = getattr(info, "last_price",      0) or getattr(info, "previous_close", 0)
        prev   = getattr(info, "previous_close",  price) or price
        change = price - prev
        pct    = (change / prev * 100) if prev else 0
        vol    = getattr(info, "three_month_average_volume", 0) or 0
        result = {
            "symbol":             symbol,
            "priceChange":        f"{change:.2f}",
            "priceChangePercent": f"{pct:.4f}",
            "lastPrice":          f"{price:.2f}",
            "prevClosePrice":     f"{prev:.2f}",
            "volume":             f"{vol:.2f}",
            "quoteVolume":        f"{vol * price:.2f}",
        }
        with _cache_lock:
            _ticker_cache[cache_key] = result
        return result
    except Exception as e:
        logger.error(f"24hr ticker error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v3/ticker/bookTicker")
async def book_ticker(symbol: str = Query("RELIANCEINR")):
    """Best bid/ask — synthesised from last price with minimal spread."""
    ticker_name = symbol_to_yf(symbol)
    try:
        ticker = yf.Ticker(ticker_name)
        info   = ticker.fast_info
        price  = getattr(info, "last_price", 0) or getattr(info, "previous_close", 1)
        spread = round(price * 0.0001, 2)  # ~0.01% synthetic spread
        return {
            "symbol":   symbol,
            "bidPrice": f"{price - spread:.2f}",
            "bidQty":   "100.00",
            "askPrice": f"{price + spread:.2f}",
            "askQty":   "100.00",
        }
    except Exception as e:
        logger.error(f"bookTicker error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Order Book (depth) ────────────────────────────────────────────────────────
@app.get("/api/v3/depth")
async def order_book(
    symbol: str = Query("RELIANCEINR"),
    limit:  int = Query(5),
):
    """Synthesised order book from last price. Freqtrade uses this for spread checks."""
    ticker_name = symbol_to_yf(symbol)
    try:
        ticker = yf.Ticker(ticker_name)
        info   = ticker.fast_info
        price  = getattr(info, "last_price", 0) or getattr(info, "previous_close", 1)

        bids, asks = [], []
        for i in range(limit):
            offset  = round(price * 0.0002 * (i + 1), 2)
            qty     = str(round(random.uniform(10, 500), 2))
            bids.append([f"{price - offset:.2f}", qty])
            asks.append([f"{price + offset:.2f}", qty])

        return {
            "lastUpdateId": int(time.time() * 1000),
            "bids": bids,
            "asks": asks,
        }
    except Exception as e:
        logger.error(f"depth error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Recent Trades ─────────────────────────────────────────────────────────────
@app.get("/api/v3/trades")
async def recent_trades(
    symbol: str = Query("RELIANCEINR"),
    limit:  int = Query(50),
):
    """Synthesised recent trades from last price."""
    ticker_name = symbol_to_yf(symbol)
    try:
        ticker = yf.Ticker(ticker_name)
        info   = ticker.fast_info
        price  = getattr(info, "last_price", 0) or getattr(info, "previous_close", 1)

        now_ms = int(time.time() * 1000)
        trades = []
        for i in range(min(limit, 50)):
            trade_price = round(price * random.uniform(0.9998, 1.0002), 2)
            trades.append({
                "id":            now_ms - i * 1000,
                "price":         f"{trade_price:.2f}",
                "qty":           f"{random.randint(1, 100):.2f}",
                "quoteQty":      f"{trade_price * random.randint(1, 100):.2f}",
                "time":          now_ms - i * 1000,
                "isBuyerMaker":  random.choice([True, False]),
                "isBestMatch":   True,
            })
        return trades
    except Exception as e:
        logger.error(f"trades error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Account & Balance ─────────────────────────────────────────────────────────
@app.get("/api/v3/account")
async def get_account():
    if not DRY_RUN:
        dhan = get_dhan_client()
        if dhan:
            try:
                funds     = dhan.get_fund_limits()
                available = float(funds.get("data", {}).get("availabelBalance", 100000))
                logger.info(f"Dhan real balance: ₹{available:,.2f}")
                return _build_account_response(available)
            except Exception as e:
                logger.error(f"Dhan balance fetch failed: {e}")

    logger.info("Returning MOCK account balance (dry-run or Dhan unavailable)")
    return _build_account_response(1_000_000.0, mock=True)


def _build_account_response(inr_balance: float, mock: bool = False) -> dict:
    base_assets = [
        {"asset": "RELIANCE",   "free": "0.00", "locked": "0.00"},
        {"asset": "TCS",        "free": "0.00", "locked": "0.00"},
        {"asset": "INFY",       "free": "0.00", "locked": "0.00"},
        {"asset": "HDFCBANK",   "free": "0.00", "locked": "0.00"},
        {"asset": "WIPRO",      "free": "0.00", "locked": "0.00"},
        {"asset": "ICICIBANK",  "free": "0.00", "locked": "0.00"},
        {"asset": "BAJFINANCE", "free": "0.00", "locked": "0.00"},
        {"asset": "HINDUNILVR", "free": "0.00", "locked": "0.00"},
        {"asset": "SBIN",       "free": "0.00", "locked": "0.00"},
        {"asset": "KOTAKBANK",  "free": "0.00", "locked": "0.00"},
    ]
    return {
        "makerCommission":  15,
        "takerCommission":  15,
        "canTrade":         True,
        "canWithdraw":      not mock,
        "canDeposit":       not mock,
        "updateTime":       int(time.time() * 1000),
        "accountType":      "SPOT",
        "balances": [
            {"asset": "INR", "free": f"{inr_balance:.2f}", "locked": "0.00"},
            *base_assets,
        ],
    }


# ── Order Management ──────────────────────────────────────────────────────────
@app.post("/api/v3/order")
async def create_order(request: Request):
    """Route Freqtrade orders to Dhan (live) or mock response (dry-run)."""
    from urllib.parse import parse_qs
    raw    = await request.body()
    params = {k: v[0] for k, v in parse_qs(raw.decode("utf-8")).items()}

    symbol     = params.get("symbol", "RELIANCEINR")
    side       = params.get("side", "BUY").upper()
    order_type = params.get("type", "MARKET").upper()
    quantity   = float(params.get("quantity", 1))
    price      = float(params.get("price", 0))
    order_id   = int(time.time() * 1000)

    logger.info(f"ORDER: {side} {quantity:.0f}x {symbol} @ {price:.2f} [{order_type}]")

    if not DRY_RUN:
        dhan     = get_dhan_client()
        sym_info = SYMBOL_MAP.get(normalise_symbol(symbol), {})
        sec_id   = sym_info.get("dhan_id", "500325")
        segment  = sym_info.get("dhan_seg", "NSE_EQ")

        if dhan and sec_id:
            try:
                resp = dhan.place_order(
                    security_id      = sec_id,
                    exchange_segment = segment,
                    transaction_type = side,
                    quantity         = int(quantity),
                    order_type       = "MARKET" if order_type == "MARKET" else "LIMIT",
                    product_type     = "INTRADAY",
                    price            = price if order_type == "LIMIT" else 0,
                )
                logger.info(f"✅ Dhan order placed: {resp}")
                order_id = resp.get("data", {}).get("orderId", order_id)
            except Exception as e:
                logger.error(f"❌ Dhan order failed: {e}")
                raise HTTPException(status_code=500, detail=f"Dhan order error: {e}")
        else:
            logger.warning("Dhan client unavailable — order not executed")
    else:
        logger.info(f"📝 DRY-RUN: {side} {quantity:.0f}x {symbol} @ {price:.2f}")

    exec_price = price if price > 0 else 0
    return {
        "symbol":              symbol,
        "orderId":             order_id,
        "orderListId":         -1,
        "clientOrderId":       f"algoplutus-{order_id}",
        "transactTime":        int(time.time() * 1000),
        "price":               f"{exec_price:.2f}",
        "origQty":             f"{quantity:.2f}",
        "executedQty":         f"{quantity:.2f}",
        "cummulativeQuoteQty": f"{quantity * max(exec_price, 1):.2f}",
        "status":              "FILLED",
        "timeInForce":         "GTC",
        "type":                order_type,
        "side":                side,
        "fills": [{
            "price":           f"{exec_price:.2f}",
            "qty":             f"{quantity:.2f}",
            "commission":      "0",
            "commissionAsset": "INR",
        }],
    }


@app.delete("/api/v3/order")
async def cancel_order(
    symbol:  str = Query("RELIANCEINR"),
    orderId: str = Query(""),
):
    logger.info(f"CANCEL: {symbol} orderId={orderId}")
    if not DRY_RUN and orderId:
        dhan = get_dhan_client()
        if dhan:
            try:
                resp = dhan.cancel_order(orderId)
                logger.info(f"Dhan cancel: {resp}")
            except Exception as e:
                logger.error(f"Dhan cancel failed: {e}")
    return {
        "symbol":              symbol,
        "origClientOrderId":   orderId,
        "orderId":             orderId,
        "orderListId":         -1,
        "clientOrderId":       orderId,
        "transactTime":        int(time.time() * 1000),
        "price":               "0.00",
        "origQty":             "0.00",
        "executedQty":         "0.00",
        "cummulativeQuoteQty": "0.00",
        "status":              "CANCELED",
        "timeInForce":         "GTC",
        "type":                "LIMIT",
        "side":                "BUY",
    }


@app.get("/api/v3/order")
async def get_order(
    symbol:  str = Query("RELIANCEINR"),
    orderId: str = Query(""),
):
    if not DRY_RUN and orderId:
        dhan = get_dhan_client()
        if dhan:
            try:
                resp = dhan.get_order_by_id(orderId)
                logger.info(f"Dhan order status: {resp}")
            except Exception as e:
                logger.error(f"Dhan get_order failed: {e}")
    return {
        "symbol":              symbol,
        "orderId":             orderId,
        "status":              "FILLED",
        "executedQty":         "1.00",
        "cummulativeQuoteQty": "0.00",
        "price":               "0.00",
        "origQty":             "1.00",
        "side":                "BUY",
        "type":                "MARKET",
        "timeInForce":         "GTC",
    }


@app.get("/api/v3/openOrders")
async def open_orders(symbol: str = Query("RELIANCEINR")):
    if not DRY_RUN:
        dhan = get_dhan_client()
        if dhan:
            try:
                resp   = dhan.get_order_list()
                orders = resp.get("data", [])
                logger.info(f"Dhan open orders: {len(orders)}")
                # TODO: map Dhan order format → Binance format for accurate tracking
                return []
            except Exception as e:
                logger.error(f"Dhan open orders failed: {e}")
    return []


@app.get("/api/v3/myTrades")
async def my_trades(symbol: str = Query("RELIANCEINR")):
    return []


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
