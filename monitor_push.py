#!/usr/bin/env python3
"""
Moomoo Push-Based Alert Monitor

Event-driven architecture:
  1. Seed: fetch recent history for each ticker/timeframe at startup
  2. Subscribe: register K-line push callbacks for each ticker/timeframe
  3. React: when a new bar starts, the PREVIOUS bar is finalized → run indicators

No polling. No rate-limit hammering. Signals fire the moment a bar closes.

Usage:
    python3 monitor_push.py
"""

import sys
import os
import json
import time
import traceback
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from moomoo import (
    OpenQuoteContext, SubType, KLType, CurKlineHandlerBase, RET_OK
)
from config import (
    WATCHLIST, TIMEFRAMES, ALERT_COOLDOWN_BARS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN, MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
)
from core.data import get_quote_context, fetch_candles, _filter_regular_hours
from core.alerts import dispatch_alert, format_alert
from core.confluence import ConfluenceEngine
from core.delivery import send_telegram
from indicators import ldzn, mmts, lddx, mabs, rscdjc, kdzs, zj
from indicators import bb_squeeze

EST = ZoneInfo("America/New_York")

# Timeframe → Moomoo SubType
SUBTYPE_MAP = {
    "30m":   SubType.K_30M,
    "1h":    SubType.K_60M,
    "4h":    SubType.K_60M,   # We aggregate 1h → 4h manually
    "daily": SubType.K_DAY,
}

# Timeframe → KLType (for seeding history)
KLTYPE_MAP = {
    "30m":   KLType.K_30M,
    "1h":    KLType.K_60M,
    "4h":    KLType.K_60M,
    "daily": KLType.K_DAY,
}

INDICATORS = {
    "LDZN":   ldzn,
    "MMTS":   mmts,
    "LDDX":   lddx,
    "MABS":   mabs,
    "RSCDJC": rscdjc,
    "KDZS":   kdzs,
    "ZJ":     zj,
}

# In-memory candle store: {(ticker, timeframe): pd.DataFrame}
CANDLE_STORE = {}

# Last processed bar time per subscription to detect new bar events
LAST_BAR_TIME = {}  # {(ticker, timeframe): str}

# Fired alert dedup: {(ticker, timeframe, indicator, signal, bar_time)}
FIRED_ALERTS = set()

# Confluence engine (shared)
CONFLUENCE = ConfluenceEngine()


# ─── Market Hours ─────────────────────────────────────────────────

def now_est() -> datetime:
    return datetime.now(EST)

def is_market_hours() -> bool:
    if now_est().weekday() >= 5:
        return False
    t = now_est()
    market_open  = t.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MIN,  second=0, microsecond=0)
    market_close = t.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)
    return market_open <= t <= market_close


# ─── History Seeding ──────────────────────────────────────────────

def seed_history(ctx: OpenQuoteContext, ticker: str, timeframe: str):
    """
    Fetch recent history for a ticker/timeframe and store in CANDLE_STORE.
    Called once at startup for each subscription.
    """
    try:
        df = fetch_candles(ctx, ticker, timeframe)
        if df is None or len(df) < 10:
            print(f"  ⚠️  Insufficient history: {ticker} @ {timeframe}")
            return False

        CANDLE_STORE[(ticker, timeframe)] = df
        last_bar = df.iloc[-1]["time"]
        LAST_BAR_TIME[(ticker, timeframe)] = str(last_bar)
        print(f"  ✅ Seeded {ticker} @ {timeframe}: {len(df)} bars, last={last_bar}")
        return True

    except Exception as e:
        print(f"  ❌ Seed failed {ticker} @ {timeframe}: {e}")
        return False


# ─── Bar Processing ───────────────────────────────────────────────

def process_completed_bar(ticker: str, timeframe: str):
    """
    Called when we detect a new bar has started — meaning the PREVIOUS bar
    is now finalized. Run all indicators on the stored dataframe.
    """
    df = CANDLE_STORE.get((ticker, timeframe))
    if df is None or len(df) < 50:
        return

    today = now_est().date()

    # The completed bar is iloc[-1] (push-based: the previous bar is now done)
    completed_bar = df.iloc[-1]
    try:
        bar_date = pd.to_datetime(completed_bar["time"]).date()
    except Exception:
        bar_date = None

    # Skip if bar is from a previous session (prior-day bar leak prevention)
    if bar_date and bar_date != today:
        return

    # Skip signals before 9:45 AM ET
    try:
        bar_et = pd.to_datetime(completed_bar["time"])
        bar_mins = bar_et.hour * 60 + bar_et.minute
        if bar_mins < (9 * 60 + 45):
            return
    except Exception:
        pass

    bar_time = str(completed_bar["time"])

    # Run BB squeeze for context
    try:
        df_sq = bb_squeeze.compute(df)
        squeeze_ctx = bb_squeeze.get_squeeze_context(df_sq)
        squeeze_text = bb_squeeze.format_squeeze_context(squeeze_ctx)
    except Exception:
        squeeze_ctx = {}
        squeeze_text = ""

    for ind_name, ind_module in INDICATORS.items():
        try:
            result = ind_module.compute(df)
            # get_signals now uses iloc[-1] since this IS the completed bar
            signals = ind_module.get_signals_completed(result)
        except Exception as e:
            # Fallback: try standard get_signals
            try:
                result = ind_module.compute(df)
                signals = ind_module.get_signals(result)
            except Exception:
                continue

        for sig in signals:
            key = (ticker, timeframe, ind_name, sig["signal"], bar_time)
            if key not in FIRED_ALERTS:
                FIRED_ALERTS.add(key)

                CONFLUENCE.record_signal(
                    ticker, timeframe, ind_name,
                    sig["signal"], sig["direction"], sig["close"]
                )

                conf = CONFLUENCE.score_confluence(ticker, timeframe, sig["direction"])
                sig["confluence"] = conf
                sig["squeeze"] = squeeze_ctx

                base_msg = format_alert(ticker, timeframe, ind_name, sig)
                if squeeze_text:
                    base_msg += f"\n{squeeze_text}"

                enhanced_msg = CONFLUENCE.format_confluence_alert(
                    ticker, timeframe, sig["direction"], base_msg
                )
                sig["enhanced_message"] = enhanced_msg

                dispatch_alert(ticker, timeframe, ind_name, sig)

                conf_tag = f" {conf['emoji']}" if conf.get('emoji') else ""
                print(f"  🔔 {sig['emoji']} {sig['signal']} on {ticker} @ {timeframe}{conf_tag}")


# ─── Push Handler ─────────────────────────────────────────────────

class KlineBarHandler(CurKlineHandlerBase):
    """
    Receives real-time K-line updates from Moomoo.
    Each push contains the current forming bar.
    When the time_key changes, the PREVIOUS bar just closed.
    """

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            return RET_ERROR, data

        try:
            if data is None or data.empty:
                return RET_OK, data

            for _, row in data.iterrows():
                ticker    = row.get("code", "")
                time_key  = str(row.get("time_key", ""))
                ktype_str = str(row.get("k_type", ""))

                # Map ktype to our timeframe label
                timeframe = self._ktype_to_tf(ktype_str)
                if not timeframe:
                    continue

                # Only process during market hours
                if not is_market_hours():
                    continue

                store_key = (ticker, timeframe)
                prev_time = LAST_BAR_TIME.get(store_key)

                if prev_time and time_key != prev_time:
                    # New bar started → previous bar is finalized
                    # Append the previous bar to CANDLE_STORE and process it
                    self._append_and_process(ticker, timeframe, row, time_key)
                elif not prev_time:
                    # First push for this subscription
                    LAST_BAR_TIME[store_key] = time_key

        except Exception as e:
            print(f"  ❌ KlineBarHandler error: {e}")

        return RET_OK, data

    def _ktype_to_tf(self, ktype_str: str) -> str:
        """Map Moomoo k_type string to our timeframe label."""
        ktype_str = ktype_str.upper()
        if "30" in ktype_str:
            return "30m"
        if "60" in ktype_str or "1H" in ktype_str:
            return "1h"
        if "DAY" in ktype_str or "1D" in ktype_str:
            return "daily"
        return ""

    def _append_and_process(self, ticker: str, timeframe: str, row, new_time_key: str):
        """
        The bar at LAST_BAR_TIME just closed. Append it to our df and run indicators.
        Then update LAST_BAR_TIME to the new bar.
        """
        store_key = (ticker, timeframe)
        df = CANDLE_STORE.get(store_key)

        if df is None:
            LAST_BAR_TIME[store_key] = new_time_key
            return

        # Append the new bar (the push data is the just-forming bar,
        # but since time changed, the PREVIOUS bar_time is what closed)
        # Re-fetch just the latest bars to keep df current
        try:
            ctx = get_quote_context()
            new_bar_df = fetch_candles(ctx, ticker, timeframe)
            ctx.close()
            if new_bar_df is not None and len(new_bar_df) >= 10:
                CANDLE_STORE[store_key] = new_bar_df
        except Exception:
            pass  # Keep existing df if fetch fails

        LAST_BAR_TIME[store_key] = new_time_key

        print(f"\n📊 Bar closed: {ticker} @ {timeframe} | {LAST_BAR_TIME[store_key]}")
        process_completed_bar(ticker, timeframe)


# ─── Subscription Management ──────────────────────────────────────

def subscribe_all(ctx: OpenQuoteContext, watchlist: list) -> bool:
    """Subscribe to K-line pushes for all tickers and timeframes."""
    handler = KlineBarHandler()
    ctx.set_handler(handler)

    # Moomoo groups 30m and 1h under K_30M and K_60M
    # We subscribe to the underlying KLTypes (not 4h separately since it's aggregated)
    subtypes_to_sub = list(set([
        SUBTYPE_MAP[tf] for tf in TIMEFRAMES
        if tf in SUBTYPE_MAP and tf != "4h"  # 4h is aggregated from 1h
    ]))

    print(f"\n📡 Subscribing {len(watchlist)} tickers × {len(subtypes_to_sub)} subtypes...")

    # Moomoo allows max ~100 subscriptions at a time — batch if needed
    BATCH_SIZE = 20
    for i in range(0, len(watchlist), BATCH_SIZE):
        batch = watchlist[i:i + BATCH_SIZE]
        ret, data = ctx.subscribe(batch, subtypes_to_sub, is_first_push=False, subscribe_push=True)
        if ret != RET_OK:
            print(f"  ⚠️  Subscribe batch {i//BATCH_SIZE+1} failed: {data}")
        else:
            print(f"  ✅ Subscribed batch {i//BATCH_SIZE+1}: {len(batch)} tickers")
        time.sleep(1)  # Rate limit

    return True


# ─── Main ─────────────────────────────────────────────────────────

def main():
    watchlist = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║     🚀 Moomoo Push-Based Monitor — Event Driven            ║
╠══════════════════════════════════════════════════════════════╣
║  Tickers:    {len(watchlist):<47}║
║  Timeframes: {', '.join(TIMEFRAMES):<47}║
║  Indicators: {', '.join(INDICATORS.keys()):<47}║
║  Mode:       Push (bar close events, no polling)            ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # Connect to OpenD
    ctx = get_quote_context()
    print("✅ Connected to OpenD")

    # Seed history for all tickers/timeframes
    print("\n📥 Seeding historical data...")
    seed_failures = 0
    for ticker in watchlist:
        for tf in TIMEFRAMES:
            if not seed_history(ctx, ticker, tf):
                seed_failures += 1
            time.sleep(0.6)  # Respect rate limits during seeding (60 req/30s)

    print(f"\n✅ Seeding complete. {len(watchlist) * len(TIMEFRAMES) - seed_failures} seeded, {seed_failures} failed.")

    # Subscribe to push events
    subscribe_all(ctx, watchlist)

    # Also run a daily scan at open (9:45 AM) for any signals on daily bars
    last_daily_scan = None

    print("\n🎯 Listening for bar close events... (Ctrl+C to stop)\n")

    try:
        while True:
            t = now_est()

            # Daily scan: run indicators on daily bars once per day at 9:45 AM
            today_str = t.strftime("%Y-%m-%d")
            if (t.hour == 9 and t.minute >= 45 and
                    last_daily_scan != today_str and
                    t.weekday() < 5):
                print(f"\n📋 Running daily bar scan at {t.strftime('%H:%M')} ET...")
                for ticker in watchlist:
                    process_completed_bar(ticker, "daily")
                last_daily_scan = today_str

            time.sleep(10)  # Just keep alive; actual work is done in push callbacks

    except KeyboardInterrupt:
        print("\n\n👋 Monitor stopped.")
    finally:
        try:
            ctx.unsubscribe_all()
        except Exception:
            pass
        ctx.close()
        print("🔌 Disconnected from OpenD")


if __name__ == "__main__":
    main()
