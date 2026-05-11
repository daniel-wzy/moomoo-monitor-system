#!/usr/bin/env python3
"""
Moomoo Alert Monitor v4.0 — Subscribe-Push Architecture

On startup:
  1. Fetch full candle history for all tickers (batched, rate-limited)
  2. Subscribe to 30m + 1h push for all tickers
  3. Push handler updates in-memory cache, runs indicators on bar completion
  4. 4h bars derived from 1h cache; daily fetched once at market open

NO more polling loops — OpenD pushes updates, we react.

Usage:
    python3 monitor.py                    # run with config.py watchlist
    python3 monitor.py US.AAPL US.TSLA    # override watchlist via CLI
"""
import sys
import os
import json
import time
import threading
import traceback
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from moomoo import OpenQuoteContext, KLType, SubType, CurKlineHandlerBase, RET_OK

from config import (
    WATCHLIST, TIMEFRAMES, ALERT_COOLDOWN_BARS,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN, MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
    POST_MARKET_HOUR, POST_MARKET_MIN,
    OPEND_HOST, OPEND_PORT, HISTORY_BARS, KTYPE_MAP,
)
from core.data import _filter_regular_hours, _aggregate_4h
from core.alerts import dispatch_alert, format_alert
from core.confluence import ConfluenceEngine
from indicators import ldzn, mmts, lddx, mabs, rscdjc, kdzs, zj
from indicators import bb_squeeze

EST = ZoneInfo("America/New_York")

INDICATORS = {
    "LDZN": ldzn,
    "MMTS": mmts,
    "LDDX": lddx,
    "MABS": mabs,
    "RSCDJC": rscdjc,
    "KDZS": kdzs,
    "ZJ": zj,
}

# Push timeframes — daily is fetched but not subscribed (changes rarely intraday)
PUSH_TIMEFRAMES = ["30m", "1h"]

# Subscription type mapping for push timeframes
SUBTYPE_MAP = {
    "30m": SubType.K_30M,
    "1h":  SubType.K_60M,
}

FIRED_ALERTS = set()
STATE_FILE = os.path.join(os.path.dirname(__file__), "monitor_state.json")

# ─── In-memory candle cache ──────────────────────────────────────
# { ticker: { timeframe: pd.DataFrame } }
CANDLE_CACHE: dict[str, dict[str, pd.DataFrame]] = defaultdict(dict)
CACHE_LOCK = threading.Lock()


# ─── Market Hours ────────────────────────────────────────────────

def now_est() -> datetime:
    return datetime.now(EST)

def is_weekday() -> bool:
    return now_est().weekday() < 5

def is_market_hours() -> bool:
    if not is_weekday():
        return False
    t = now_est()
    market_open  = t.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MIN,  second=0, microsecond=0)
    market_close = t.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)
    return market_open <= t <= market_close

def seconds_until_market_open() -> int:
    t = now_est()
    target = t.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
    if t >= target:
        target += timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return int((target - t).total_seconds())


# ─── State persistence ───────────────────────────────────────────

def load_state():
    global FIRED_ALERTS
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
                FIRED_ALERTS = set(tuple(x) for x in data.get("fired", []))
        except Exception:
            FIRED_ALERTS = set()

def save_state():
    recent = list(FIRED_ALERTS)[-1000:]
    with open(STATE_FILE, "w") as f:
        json.dump({"fired": recent}, f)


# ─── History Fetch (one-time at startup) ────────────────────────

def fetch_history_batch(ctx, tickers: list[str], timeframe: str,
                        batch_size: int = 10, batch_delay: float = 2.0):
    """
    Fetch full candle history for all tickers on one timeframe.
    Processes in batches with delay to avoid OpenD rate limits.
    """
    ktype_str = KTYPE_MAP[timeframe]
    ktype = getattr(KLType, ktype_str)

    days_back = {"30m": 30, "1h": 60, "4h": 120, "daily": 400}
    end_date   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    start_date = (datetime.now() - timedelta(days=days_back[timeframe])).strftime('%Y-%m-%d %H:%M:%S')
    num_bars   = HISTORY_BARS[timeframe]

    ok = 0
    fail = 0
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        for ticker in batch:
            try:
                ret, data, _ = ctx.request_history_kline(
                    ticker, ktype=ktype,
                    start=start_date, end=end_date,
                    max_count=num_bars,
                )
                if ret != 0:
                    print(f"  ⚠️  History fetch failed {ticker} {timeframe}: {data}")
                    fail += 1
                    continue

                cols = ["time_key", "open", "close", "high", "low", "volume"]
                if "turnover" in data.columns:
                    cols.append("turnover")
                df = data[cols].copy()
                df.rename(columns={"time_key": "time"}, inplace=True)
                df["time"] = pd.to_datetime(df["time"])
                df = df.sort_values("time").reset_index(drop=True)

                if timeframe in ("30m", "1h", "4h"):
                    df = _filter_regular_hours(df)

                with CACHE_LOCK:
                    if timeframe == "4h":
                        # Derive 4h from 1h — stored separately
                        CANDLE_CACHE[ticker]["4h"] = _aggregate_4h(df)
                    else:
                        CANDLE_CACHE[ticker][timeframe] = df

                ok += 1
            except Exception as e:
                print(f"  ❌ {ticker} {timeframe}: {e}")
                fail += 1

        if i + batch_size < len(tickers):
            time.sleep(batch_delay)

    return ok, fail


def prefetch_all_history(ctx, watchlist: list[str]):
    """Fetch history for all timeframes at startup."""
    for tf in ["30m", "1h", "daily"]:
        print(f"  📥 Fetching {tf} history for {len(watchlist)} tickers...")
        ok, fail = fetch_history_batch(ctx, watchlist, tf)
        print(f"     ✅ {ok} ok, ❌ {fail} failed")
        time.sleep(1)

    # Build 4h from cached 1h data
    built = 0
    with CACHE_LOCK:
        for ticker in watchlist:
            if "1h" in CANDLE_CACHE[ticker]:
                CANDLE_CACHE[ticker]["4h"] = _aggregate_4h(CANDLE_CACHE[ticker]["1h"])
                built += 1
    print(f"  📊 Built 4h cache for {built} tickers from 1h data")


# ─── Indicator Runner ────────────────────────────────────────────

def run_indicators_on_ticker(ticker: str, timeframe: str,
                              confluence: ConfluenceEngine) -> list[dict]:
    """
    Run all indicators on the cached candle data for (ticker, timeframe).
    Returns list of new alert dicts.
    """
    alerts = []

    with CACHE_LOCK:
        df = CANDLE_CACHE.get(ticker, {}).get(timeframe)

    if df is None or len(df) < 50:
        return alerts

    try:
        bar_time = str(df.iloc[-2]["time"])

        # Only fire on today's session bars
        completed_bar_time = pd.to_datetime(df.iloc[-2]["time"])
        if completed_bar_time.date() != now_est().date():
            return alerts

        df_squeezed = bb_squeeze.compute(df)
        squeeze_ctx = bb_squeeze.get_squeeze_context(df_squeezed)
        squeeze_text = bb_squeeze.format_squeeze_context(squeeze_ctx)

        for ind_name, ind_module in INDICATORS.items():
            result  = ind_module.compute(df)
            signals = ind_module.get_signals(result)

            for sig in signals:
                key = (ticker, timeframe, ind_name, sig["signal"], bar_time)
                if key in FIRED_ALERTS:
                    continue

                FIRED_ALERTS.add(key)

                confluence.record_signal(
                    ticker, timeframe, ind_name,
                    sig["signal"], sig["direction"], sig["close"]
                )
                conf = confluence.score_confluence(ticker, timeframe, sig["direction"])

                sig["confluence"] = conf
                sig["squeeze"]    = squeeze_ctx

                base_msg = format_alert(ticker, timeframe, ind_name, sig)
                if squeeze_text:
                    base_msg += f"\n{squeeze_text}"
                sig["enhanced_message"] = confluence.format_confluence_alert(
                    ticker, timeframe, sig["direction"], base_msg
                )

                dispatch_alert(ticker, timeframe, ind_name, sig)

                conf_tag   = f" {conf['emoji']}" if conf.get('emoji') else ""
                squeeze_tag = " 💥" if squeeze_ctx.get("squeeze_fire") else ""
                print(f"  🔔 {sig.get('emoji','')} {sig['signal']} on "
                      f"{ticker} @ {timeframe}{conf_tag}{squeeze_tag}")

                alerts.append(sig)

    except Exception as e:
        print(f"  ❌ Indicator error {ticker} @ {timeframe}: {e}")
        traceback.print_exc()

    return alerts


# ─── Candle Push Handler ─────────────────────────────────────────

class CandlePushHandler(CurKlineHandlerBase):
    """
    Receives real-time candle updates from OpenD.
    Updates cache, detects bar completion, triggers indicators.
    """

    def __init__(self, confluence: ConfluenceEngine):
        super().__init__()
        self.confluence = confluence
        # Track last seen bar time per (ticker, timeframe) to detect new bars
        self._last_bar_time: dict[tuple, str] = {}

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK or data is None or data.empty:
            return ret, data

        if not is_market_hours():
            return ret, data

        try:
            row = data.iloc[0]
            ticker   = row["code"]
            bar_time = str(row["time_key"])
            ktype    = str(row["k_type"])

            # Map ktype back to our timeframe key
            tf_map = {"K_30M": "30m", "K_60M": "1h"}
            timeframe = tf_map.get(ktype)
            if timeframe is None:
                return ret, data

            # Build a one-row DataFrame for the current bar
            new_row = pd.DataFrame([{
                "time":     pd.to_datetime(bar_time),
                "open":     float(row["open"]),
                "high":     float(row["high"]),
                "low":      float(row["low"]),
                "close":    float(row["close"]),
                "volume":   float(row["volume"]),
                "turnover": float(row.get("turnover", 0)),
            }])

            bar_key = (ticker, timeframe)
            prev_bar_time = self._last_bar_time.get(bar_key)

            with CACHE_LOCK:
                df = CANDLE_CACHE.get(ticker, {}).get(timeframe)
                if df is None:
                    CANDLE_CACHE[ticker][timeframe] = new_row
                else:
                    last_cached = str(df.iloc[-1]["time"])
                    if last_cached == bar_time:
                        # Update in-place (same bar, new tick)
                        CANDLE_CACHE[ticker][timeframe].iloc[-1] = new_row.iloc[0]
                    else:
                        # New bar appended — previous bar is now complete
                        CANDLE_CACHE[ticker][timeframe] = pd.concat(
                            [df, new_row], ignore_index=True
                        ).tail(HISTORY_BARS.get(timeframe, 500)).reset_index(drop=True)

                # Rebuild 4h from updated 1h cache
                if timeframe == "1h" and "1h" in CANDLE_CACHE.get(ticker, {}):
                    CANDLE_CACHE[ticker]["4h"] = _aggregate_4h(CANDLE_CACHE[ticker]["1h"])

            # Fire indicators when a new bar starts (previous bar just closed)
            if prev_bar_time is not None and bar_time != prev_bar_time:
                # Previous bar is complete — run indicators on it
                for tf in ([timeframe, "4h"] if timeframe == "1h" else [timeframe]):
                    alerts = run_indicators_on_ticker(ticker, tf, self.confluence)
                    if alerts:
                        summary = self.confluence.get_active_confluences()
                        elevated = [c for c in summary if c["score"] >= 3]
                        if elevated:
                            for c in elevated:
                                print(f"  🎯 CONFLUENCE: {c['ticker']} {c['direction']} "
                                      f"(Score {c['score']}) — "
                                      f"{', '.join(c['indicators'])}")
                save_state()

            self._last_bar_time[bar_key] = bar_time

        except Exception as e:
            print(f"  ❌ Push handler error: {e}")
            traceback.print_exc()

        return ret, data


# ─── Subscribe All Tickers ────────────────────────────────────────

def subscribe_all(ctx, watchlist: list[str], handler: CandlePushHandler,
                  batch_size: int = 20, batch_delay: float = 1.0):
    """
    Subscribe all tickers for push timeframes (30m, 1h).
    Batched to stay within OpenD subscription quota.
    """
    subtypes = [SUBTYPE_MAP[tf] for tf in PUSH_TIMEFRAMES]
    ok = 0
    fail = 0

    ctx.set_handler(handler)

    for i in range(0, len(watchlist), batch_size):
        batch = watchlist[i:i + batch_size]
        ret, msg = ctx.subscribe(batch, subtypes, is_first_push=True, subscribe_push=True)
        if ret == RET_OK:
            ok += len(batch)
        else:
            print(f"  ⚠️  Subscribe batch failed: {msg}")
            fail += len(batch)
        if i + batch_size < len(watchlist):
            time.sleep(batch_delay)

    return ok, fail


# ─── Daily History Refresh ────────────────────────────────────────

def refresh_daily_cache(ctx, watchlist: list[str]):
    """Refresh daily candle cache (called once at market open)."""
    print("  📅 Refreshing daily candle cache...")
    ok, fail = fetch_history_batch(ctx, watchlist, "daily", batch_size=10, batch_delay=2.0)
    print(f"  ✅ Daily cache refreshed: {ok} ok, {fail} failed")


# ─── Main ─────────────────────────────────────────────────────────

def main():
    watchlist = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST

    if not watchlist:
        print("❌ No tickers in watchlist!")
        sys.exit(1)

    ind_names = ", ".join(INDICATORS.keys())
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║     🐮 Moomoo Alert Monitor v4.0 — Subscribe-Push Mode     ║
╠══════════════════════════════════════════════════════════════╣
║  Tickers:    {len(watchlist):<47}║
║  Timeframes: {', '.join(TIMEFRAMES):<47}║
║  Indicators: {ind_names:<47}║
║  Push:       30m + 1h (OpenD push, no polling)              ║
║  Daily:      fetched once at market open                    ║
║  Hours:      9:30 AM — 4:00 PM EST (weekdays only)         ║
╚══════════════════════════════════════════════════════════════╝
    """)

    load_state()
    confluence = ConfluenceEngine()

    ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
    print("✅ Connected to OpenD")

    daily_refreshed_date = None

    try:
        while True:
            t = now_est()

            if is_market_hours():
                today = t.date()

                # On first entry each trading day: fetch history + subscribe
                if daily_refreshed_date != today:
                    print(f"\n🌅 Market open — initializing for {today}")

                    print("📥 Pre-fetching candle history (batched)...")
                    prefetch_all_history(ctx, watchlist)

                    print("📡 Subscribing to push feeds...")
                    ok, fail = subscribe_all(
                        ctx, watchlist,
                        CandlePushHandler(confluence)
                    )
                    print(f"   ✅ Subscribed: {ok} tickers, ❌ failed: {fail}")

                    daily_refreshed_date = today
                    print("🟢 Monitor live — waiting for candle push events...\n")

                # Stay alive — push events handled by callback thread
                time.sleep(30)

            else:
                # Outside market hours
                if is_weekday():
                    wait = seconds_until_market_open()
                    if t.hour < MARKET_OPEN_HOUR or (
                        t.hour == MARKET_OPEN_HOUR and t.minute < MARKET_OPEN_MIN
                    ):
                        wake = min(wait, 300)
                        print(f"💤 Market opens in {wait // 60}m — sleeping {wake}s...")
                    else:
                        print(f"💤 Market closed — sleeping 5m...")
                        wake = 300
                else:
                    wait = seconds_until_market_open()
                    wake = min(wait, 3600)
                    print(f"💤 Weekend — market opens in {wait // 3600}h {(wait % 3600) // 60}m")

                time.sleep(wake)

    except KeyboardInterrupt:
        print("\n\n👋 Monitor stopped.")
    finally:
        ctx.close()
        save_state()
        print("🔌 Disconnected from OpenD")


if __name__ == "__main__":
    main()
