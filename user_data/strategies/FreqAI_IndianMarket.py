#!/usr/bin/env python3
"""
AlgoPlutus — Production FreqAI Strategy v2.0
=============================================
• Multi-timeframe LightGBM regression (5m / 15m / 1h)
• ATR-based dynamic + tightening stoploss
• Market-hours guard (NSE 9:20 AM – 3:00 PM IST, no entries)
• EOD forced exit at 3:10 PM IST via custom_exit()
• Slippage guard in confirm_trade_entry()
• Explicit informative_pairs() for multi-TF data
• Volatility-adjusted position sizing placeholder
"""

import logging
from datetime import datetime, time as dtime
from functools import reduce
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import pandas_ta as pta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

logger = logging.getLogger(__name__)

# ── Indian Market Session (IST) ───────────────────────────────────────────────
MARKET_OPEN_IST     = dtime(9, 20)   # allow 5-min warm-up after open
MARKET_NO_ENTRY_IST = dtime(15, 0)   # no new entries after 3:00 PM IST
MARKET_FORCE_EXIT   = dtime(15, 10)  # force-exit all open trades by 3:10 PM IST
MARKET_CLOSE_IST    = dtime(15, 30)  # session ends


class FreqAI_IndianMarket(IStrategy):
    """
    Production FreqAI Strategy for Indian Stock Market (NSE).

    Signal flow:
        yfinance OHLCV → FreqAI LightGBM → &-target_profit prediction
        → entry when prediction > buy_profit_threshold (with market-hours guard)
        → exit when prediction < sell_profit_threshold OR EOD force-exit
        → stoploss: ATR-based dynamic, tightening at high profit
    """

    INTERFACE_VERSION = 3
    timeframe         = "5m"
    can_short         = False

    # ── ROI table ──────────────────────────────────────────────────────────────
    minimal_roi = {
        "0":   0.04,    # 4%  → immediate
        "30":  0.025,   # 2.5% after 30 min
        "60":  0.015,   # 1.5% after 60 min
        "120": 0.005,   # 0.5% after 2h
        "180": 0.0,     # break-even after 3h
    }

    stoploss = -0.04    # hard -4% failsafe

    trailing_stop                   = True
    trailing_stop_positive          = 0.01
    trailing_stop_positive_offset   = 0.025
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count     = 200
    use_custom_stoploss      = True

    order_types = {
        "entry":               "limit",
        "exit":                "limit",
        "stoploss":            "market",
        "stoploss_on_exchange": False,
    }

    unfilledtimeout = {"entry": 10, "exit": 10, "unit": "minutes"}

    plot_config = {
        "main_plot": {},
        "subplots": {
            "FreqAI_Signal": {"&-target_profit": {"color": "#3498db"}},
            "RSI":           {"rsi": {"color": "#e74c3c"}},
            "MACD": {
                "macd":       {"color": "#3498db"},
                "macdsignal": {"color": "#f39c12"},
            },
            "ATR":           {"atr": {"color": "#9b59b6"}},
            "Confidence":    {"do_predict": {"color": "#2ecc71"}},
        },
    }

    # ── Hyperopt Parameters ────────────────────────────────────────────────────
    buy_profit_threshold  = DecimalParameter(0.003, 0.02,  default=0.007, optimize=True, space="buy")
    sell_profit_threshold = DecimalParameter(-0.01, -0.001, default=-0.003, optimize=True, space="sell")
    atr_multiplier        = DecimalParameter(1.0,   3.0,   default=1.5,   optimize=True, space="sell")
    max_spread_pct        = DecimalParameter(0.001, 0.005, default=0.003, optimize=False, space="buy")

    # ──────────────────────────────────────────────────────────────────────────
    # INFORMATIVE PAIRS
    # ──────────────────────────────────────────────────────────────────────────
    def informative_pairs(self) -> List[Tuple[str, str]]:
        """Declare additional timeframes for multi-TF data loading."""
        pairs = self.dp.current_whitelist()
        return [
            (pair, tf)
            for pair in pairs
            for tf in ("15m", "1h")
        ]

    # ──────────────────────────────────────────────────────────────────────────
    # FREQAI FEATURE ENGINEERING
    # ──────────────────────────────────────────────────────────────────────────
    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        """Multi-period technical indicators — called for each period + timeframe."""
        dataframe[f"%-rsi-period_{period}"] = pta.rsi(dataframe["close"], length=period)
        dataframe[f"%-mfi-period_{period}"] = pta.mfi(
            dataframe["high"], dataframe["low"], dataframe["close"], dataframe["volume"], length=period
        )

        adx = pta.adx(dataframe["high"], dataframe["low"], dataframe["close"], length=period)
        if adx is not None:
            dataframe[f"%-adx-period_{period}"] = adx.get(f"ADX_{period}", np.nan)

        bb = pta.bbands(dataframe["close"], length=period, std=2)
        if bb is not None:
            bbl = bb.get(f"BBL_{period}_2.0", np.nan)
            bbm = bb.get(f"BBM_{period}_2.0", np.nan)
            bbu = bb.get(f"BBU_{period}_2.0", np.nan)
            dataframe[f"%-bb_lower-period_{period}"]  = bbl
            dataframe[f"%-bb_middle-period_{period}"] = bbm
            dataframe[f"%-bb_upper-period_{period}"]  = bbu
            dataframe[f"%-bb_width-period_{period}"]  = (bbu - bbl) / bbm.replace(0, np.nan)

        dataframe[f"%-ema-period_{period}"] = pta.ema(dataframe["close"], length=period)
        dataframe[f"%-sma-period_{period}"] = pta.sma(dataframe["close"], length=period)
        dataframe[f"%-atr-period_{period}"] = pta.atr(
            dataframe["high"], dataframe["low"], dataframe["close"], length=period
        )

        macd = pta.macd(dataframe["close"], fast=12, slow=26, signal=9)
        if macd is not None:
            dataframe[f"%-macd-period_{period}"]       = macd.get("MACD_12_26_9", np.nan)
            dataframe[f"%-macdsignal-period_{period}"] = macd.get("MACDs_12_26_9", np.nan)
            dataframe[f"%-macdhist-period_{period}"]   = macd.get("MACDh_12_26_9", np.nan)

        stoch = pta.stoch(dataframe["high"], dataframe["low"], dataframe["close"])
        if stoch is not None:
            dataframe[f"%-stochk-period_{period}"] = stoch.get("STOCHk_14_3_3", np.nan)
            dataframe[f"%-stochd-period_{period}"] = stoch.get("STOCHd_14_3_3", np.nan)

        return dataframe

    def feature_engineering_expand_basic(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """Baseline features without period expansion."""
        dataframe["%-pct-change"]     = dataframe["close"].pct_change()
        dataframe["%-raw_volume"]     = dataframe["volume"]
        dataframe["%-raw_price"]      = dataframe["close"]
        dataframe["%-volume_change"]  = dataframe["volume"].pct_change()
        dataframe["%-high_low_ratio"] = (
            (dataframe["high"] - dataframe["low"]) / dataframe["close"].replace(0, np.nan)
        )
        # Volatility: rolling std of returns
        dataframe["%-vol_5"]  = dataframe["%-pct-change"].rolling(5).std()
        dataframe["%-vol_20"] = dataframe["%-pct-change"].rolling(20).std()
        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """Time-based and derived features."""
        dataframe["%-day_of_week"]    = dataframe["date"].dt.dayofweek
        dataframe["%-hour_of_day"]    = dataframe["date"].dt.hour
        dataframe["%-minute_of_hour"] = dataframe["date"].dt.minute
        dataframe["%-is_morning"]     = (dataframe["%-hour_of_day"] < 11).astype(int)
        dataframe["%-is_midday"]      = (
            (dataframe["%-hour_of_day"] >= 11) & (dataframe["%-hour_of_day"] < 13)
        ).astype(int)
        dataframe["%-is_afternoon"]   = (dataframe["%-hour_of_day"] >= 13).astype(int)
        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        """
        Regression target: % price change over next 12 candles (= 1h on 5m TF).
        Positive = price rises → buy signal.
        """
        future_close = dataframe["close"].shift(-12)
        dataframe["&-target_profit"] = (
            (future_close - dataframe["close"]) / dataframe["close"].replace(0, np.nan)
        )
        return dataframe

    # ──────────────────────────────────────────────────────────────────────────
    # POPULATE INDICATORS
    # ──────────────────────────────────────────────────────────────────────────
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self.freqai.start(dataframe, metadata, self)
        # Raw ATR for custom stoploss (not a FreqAI feature — computed after FreqAI)
        dataframe["atr"] = pta.atr(dataframe["high"], dataframe["low"], dataframe["close"], length=14)
        dataframe["rsi"] = pta.rsi(dataframe["close"], length=14)
        return dataframe

    # ──────────────────────────────────────────────────────────────────────────
    # MARKET HOURS GUARD
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _ist_time(candle_date) -> Optional[dtime]:
        """Convert candle date to IST time object."""
        try:
            ts = pd.Timestamp(candle_date)
            if ts.tzinfo is not None:
                ts = ts.tz_convert("Asia/Kolkata")
            else:
                # Assume UTC if no timezone info
                ts = ts.tz_localize("UTC").tz_convert("Asia/Kolkata")
            return ts.time()
        except Exception:
            return None

    def _is_entry_allowed(self, candle_date) -> bool:
        """Only allow new entries within NSE session window."""
        t = self._ist_time(candle_date)
        if t is None:
            return True  # permissive fallback
        return MARKET_OPEN_IST <= t <= MARKET_NO_ENTRY_IST

    def _is_force_exit_time(self, candle_date) -> bool:
        """Return True when it's time to force-exit all positions before close."""
        t = self._ist_time(candle_date)
        if t is None:
            return False
        return t >= MARKET_FORCE_EXIT

    # ──────────────────────────────────────────────────────────────────────────
    # ENTRY SIGNAL
    # ──────────────────────────────────────────────────────────────────────────
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        enter_long_conds = []

        if "do_predict" in dataframe.columns and "&-target_profit" in dataframe.columns:
            enter_long_conds.extend([
                dataframe["do_predict"] == 1,
                dataframe["&-target_profit"] > self.buy_profit_threshold.value,
            ])

        if not dataframe.empty and "date" in dataframe.columns:
            enter_long_conds.append(
                dataframe["date"].apply(self._is_entry_allowed)
            )

        if enter_long_conds:
            dataframe.loc[
                reduce(lambda x, y: x & y, enter_long_conds),
                ["enter_long", "enter_tag"],
            ] = (1, "freqai_long")

        return dataframe

    # ──────────────────────────────────────────────────────────────────────────
    # EXIT SIGNAL
    # ──────────────────────────────────────────────────────────────────────────
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_long_conds = []

        if "do_predict" in dataframe.columns and "&-target_profit" in dataframe.columns:
            exit_long_conds.extend([
                dataframe["do_predict"] == 1,
                dataframe["&-target_profit"] < self.sell_profit_threshold.value,
            ])

        if exit_long_conds:
            dataframe.loc[
                reduce(lambda x, y: x & y, exit_long_conds),
                ["exit_long", "exit_tag"],
            ] = (1, "freqai_exit")

        return dataframe

    # ──────────────────────────────────────────────────────────────────────────
    # SLIPPAGE GUARD — confirm_trade_entry()
    # ──────────────────────────────────────────────────────────────────────────
    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> bool:
        """Reject entry if bid-ask spread is too large (prevents slippage on illiquid ticks)."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return True

        last_candle = dataframe.iloc[-1]
        high = last_candle.get("high", rate)
        low  = last_candle.get("low",  rate)
        close = last_candle.get("close", rate)

        if close <= 0:
            return True

        # Proxy spread using H-L range of last candle
        spread_pct = (high - low) / close
        max_spread = self.max_spread_pct.value

        if spread_pct > max_spread:
            logger.info(
                f"⚠️  {pair} — entry rejected: spread {spread_pct:.4%} > max {max_spread:.4%}"
            )
            return False

        return True

    # ──────────────────────────────────────────────────────────────────────────
    # CUSTOM EXIT — EOD forced close
    # ──────────────────────────────────────────────────────────────────────────
    def custom_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        """Force-exit all open positions by 3:10 PM IST to avoid overnight risk."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None

        last_candle_date = dataframe.iloc[-1]["date"]
        if self._is_force_exit_time(last_candle_date):
            logger.info(f"⏰  {pair} — EOD force-exit triggered at {last_candle_date}")
            return "eod_force_exit"

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # DYNAMIC ATR STOPLOSS
    # ──────────────────────────────────────────────────────────────────────────
    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """
        ATR-based dynamic stoploss:
        - Widens in volatile conditions (large ATR)
        - Tightens significantly once profit > 3% to lock gains
        - Never exceeds the hard -6% cap
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty or "atr" not in dataframe.columns:
            return self.stoploss

        atr = dataframe["atr"].iloc[-1]
        if pd.isna(atr) or current_rate <= 0:
            return self.stoploss

        atr_sl = -(atr * self.atr_multiplier.value) / current_rate

        # Profit tiers — tighten stop as profit grows
        if current_profit > 0.05:
            atr_sl = max(atr_sl, -0.005)   # trail at -0.5% above 5% profit
        elif current_profit > 0.03:
            atr_sl = max(atr_sl, -0.01)    # trail at -1% above 3% profit
        elif current_profit > 0.015:
            atr_sl = max(atr_sl, -0.02)    # trail at -2% above 1.5% profit

        return max(atr_sl, -0.06)           # absolute floor: -6%
