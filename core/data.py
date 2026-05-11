"""
Moomoo data fetcher — pulls candle data from OpenD.
Handles 4h aggregation from 60min candles.
Filters out pre/post-market data for intraday timeframes.
"""
import time
import pandas as pd
import numpy as np
from moomoo import OpenQuoteContext, KLType, SubType, KL_FIELD
from config import OPEND_HOST, OPEND_PORT, HISTORY_BARS, KTYPE_MAP


def get_quote_context():
    """Create and return a connected quote context."""
    ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
    return ctx


def _filter_regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter intraday candles to regular trading hours only (9:30-16:00 EST).
    Removes pre-market and after-hours bars that can distort indicators.
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])

    # Convert to EST if not already timezone-aware
    if df["time"].dt.tz is None:
        # Moomoo returns US stock times in Eastern
        df["_hour"] = df["time"].dt.hour
        df["_minute"] = df["time"].dt.minute
    else:
        est = df["time"].dt.tz_convert("America/New_York")
        df["_hour"] = est.dt.hour
        df["_minute"] = est.dt.minute

    # Keep only bars between 9:30 and 16:00
    mask = (
        ((df["_hour"] == 9) & (df["_minute"] >= 30)) |
        ((df["_hour"] >= 10) & (df["_hour"] < 16))
    )
    df = df[mask].drop(columns=["_hour", "_minute"]).reset_index(drop=True)
    return df


def fetch_candles(ctx: OpenQuoteContext, ticker: str, timeframe: str) -> pd.DataFrame:
    """
    Fetch OHLCV candle data for a ticker and timeframe.

    For intraday timeframes (30m, 4h), filters to regular market hours only.

    Args:
        ctx: Connected OpenQuoteContext
        ticker: e.g. "US.AAPL"
        timeframe: "30m", "4h", or "daily"

    Returns:
        DataFrame with columns: time, open, high, low, close, volume
    """
    ktype_str = KTYPE_MAP[timeframe]
    ktype = getattr(KLType, ktype_str)
    num_bars = HISTORY_BARS[timeframe]

    from datetime import datetime, timedelta
    end_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # Fetch enough history for indicator lookbacks (max 600 bars back)
    days_back = {
        "30m": 30,   # ~390 30m bars in 30 trading days
        "1h": 60,    # ~480 1h bars in 60 trading days
        "4h": 120,   # enough for 4h lookback
        "daily": 400, # 400 trading days
    }
    start_date = (datetime.now() - timedelta(days=days_back.get(timeframe, 60))).strftime('%Y-%m-%d %H:%M:%S')

    ret, data, _ = ctx.request_history_kline(
        ticker,
        ktype=ktype,
        start=start_date,
        end=end_date,
        max_count=num_bars,
    )

    if ret != 0:
        raise Exception(f"Failed to fetch {timeframe} candles for {ticker}: {data}")

    cols = ["time_key", "open", "close", "high", "low", "volume"]
    if "turnover" in data.columns:
        cols.append("turnover")
    df = data[cols].copy()
    df.rename(columns={"time_key": "time"}, inplace=True)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    # Filter pre/post-market for intraday timeframes
    if timeframe in ("30m", "1h", "4h"):
        df = _filter_regular_hours(df)

    # Aggregate to 4h if needed
    if timeframe == "4h":
        df = _aggregate_4h(df)

    return df


def _aggregate_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate 60-min candles into 4-hour candles.

    US market session 4h blocks (ET):
      Block 1: 09:30 - 13:30 (4 hours)
      Block 2: 13:30 - 16:00 (2.5 hours — treated as one block)

    IMPORTANT: Only include completed blocks. A block is complete when
    all its expected 1h bars are present. Block 1 needs 4 bars (9:30-13:30),
    Block 2 needs at least 2 bars (13:30-16:00, completes at market close).
    This prevents a partial first-hour-of-day bar from generating a false 4h signal.
    """
    df = df_1h.copy()
    df["time"] = pd.to_datetime(df["time"])

    # Assign market-hours-based 4h blocks
    def assign_block(ts):
        # Normalize to date + time-of-day in ET
        h = ts.hour
        m = ts.minute
        mins_since_open = (h * 60 + m) - (9 * 60 + 30)  # minutes since 9:30 AM
        if mins_since_open < 0:
            return None  # pre-market, should already be filtered
        if mins_since_open < 4 * 60:  # 09:30 - 13:30
            block_label = ts.normalize() + pd.Timedelta(hours=9, minutes=30)
        else:  # 13:30 - 16:00
            block_label = ts.normalize() + pd.Timedelta(hours=13, minutes=30)
        return block_label

    df["block"] = df["time"].apply(assign_block)
    df = df[df["block"].notna()]  # drop any pre-market rows

    # Only keep COMPLETE blocks:
    # Block 1 (9:30-13:30): expect 4 bars ending at 10:30, 11:30, 12:30, 13:30
    # Block 2 (13:30-16:00): expect at least 1 bar; complete when last bar is 15:30 or later
    block_counts = df.groupby("block")["time"].count()
    block_max_time = df.groupby("block")["time"].max()

    def is_complete(block_start):
        count = block_counts.get(block_start, 0)
        max_time = block_max_time.get(block_start)
        if max_time is None:
            return False
        max_h = max_time.hour
        max_m = max_time.minute
        # Block 1: complete when 13:30 bar is present (count >= 4)
        if block_start.hour == 9:
            return count >= 4
        # Block 2: complete when 15:30 bar is present (market close block)
        else:
            return (max_h == 15 and max_m >= 30) or max_h >= 16

    complete_blocks = [b for b in df["block"].unique() if is_complete(b)]
    df = df[df["block"].isin(complete_blocks)]

    # Floor to 4-hour blocks
    df["block"] = df["block"]  # already assigned above

    agg_dict = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    if "turnover" in df.columns:
        agg_dict["turnover"] = "sum"
    agg = df.groupby("block").agg(agg_dict).reset_index()

    agg.rename(columns={"block": "time"}, inplace=True)
    return agg


def subscribe_ticker(ctx: OpenQuoteContext, ticker: str, timeframe: str):
    """Subscribe to real-time candle updates for a ticker."""
    ktype_str = KTYPE_MAP[timeframe]
    ktype = getattr(SubType, ktype_str) if hasattr(SubType, ktype_str) else None

    if ktype:
        ret, data = ctx.subscribe([ticker], [ktype])
        if ret != 0:
            print(f"  ⚠️  Subscribe failed for {ticker} {timeframe}: {data}")
        else:
            print(f"  ✅ Subscribed: {ticker} @ {timeframe}")
