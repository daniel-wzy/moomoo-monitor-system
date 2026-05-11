"""
Technical Analysis helpers — TongDaShin formula equivalents in pandas.
All functions operate on pandas Series and return pandas Series.
"""
import numpy as np
import pandas as pd


def HHV(series: pd.Series, period: int) -> pd.Series:
    """Highest value over N periods (rolling max)."""
    return series.rolling(window=period, min_periods=1).max()


def LLV(series: pd.Series, period: int) -> pd.Series:
    """Lowest value over N periods (rolling min)."""
    return series.rolling(window=period, min_periods=1).min()


def MA(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=1).mean()


def EMA(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def WMA(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average."""
    period = max(1, int(period))
    weights = np.arange(1, period + 1, dtype=float)
    
    def _wma(window):
        if len(window) < period:
            w = np.arange(1, len(window) + 1, dtype=float)
            return np.dot(window, w) / w.sum()
        return np.dot(window, weights) / weights.sum()
    
    return series.rolling(window=period, min_periods=1).apply(_wma, raw=True)


def SMA(series: pd.Series, period: int, weight: int = 1) -> pd.Series:
    """
    TongDaShin SMA (recursive smoothing).
    SMA(X, N, M) = (M * X + (N - M) * prev_SMA) / N
    This is NOT the same as a simple moving average.
    """
    result = np.zeros(len(series))
    result[0] = series.iloc[0]
    m = weight
    n = period
    for i in range(1, len(series)):
        result[i] = (m * series.iloc[i] + (n - m) * result[i - 1]) / n
    return pd.Series(result, index=series.index)


def REF(series: pd.Series, n: int = 1) -> pd.Series:
    """Reference N bars ago (shift forward)."""
    return series.shift(n)


def CROSS(a: pd.Series, b: pd.Series) -> pd.Series:
    """True when series a crosses above series b."""
    return (a > b) & (REF(a) <= REF(b))


def BARSLAST(cond: pd.Series) -> pd.Series:
    """Bars since last True in condition series."""
    result = np.full(len(cond), np.nan)
    last_true = -1
    for i in range(len(cond)):
        if cond.iloc[i]:
            last_true = i
        if last_true >= 0:
            result[i] = i - last_true
    return pd.Series(result, index=cond.index)


def COUNT(cond: pd.Series, period) -> pd.Series:
    """
    Count True values over last N periods.
    If period is a Series (dynamic), uses per-bar lookback.
    """
    if isinstance(period, (int, float)):
        return cond.astype(int).rolling(window=int(period), min_periods=1).sum()
    else:
        # Dynamic period — per-bar lookback
        result = np.zeros(len(cond))
        for i in range(len(cond)):
            n = int(period.iloc[i]) if not np.isnan(period.iloc[i]) else 0
            n = max(0, min(n, i + 1))
            if n > 0:
                result[i] = cond.iloc[max(0, i - n + 1):i + 1].sum()
        return pd.Series(result, index=cond.index)


def ZIG(close: pd.Series, high: pd.Series, low: pd.Series, mode: int, pct: float) -> pd.Series:
    """
    ZIG-ZAG indicator.
    
    mode: 1=high/low, 2=high/low with close confirm, 3=close only
    pct: minimum reversal percentage to register a swing
    
    Returns: Series of interpolated ZIG values between swing points.
    Note: This repaints (like the original TDX implementation).
    """
    n = len(close)
    if n == 0:
        return pd.Series(dtype=float)
    
    # Select source based on mode
    if mode == 3:
        src_high = close.values.astype(float)
        src_low = close.values.astype(float)
    else:
        src_high = high.values.astype(float)
        src_low = low.values.astype(float)
    
    # Find swing points
    swings = []  # list of (index, value, type) where type = 1 (peak) or -1 (trough)
    
    if n < 2:
        return pd.Series(src_high, index=close.index)
    
    # Initialize: find first direction
    direction = 0  # 1 = looking for peak, -1 = looking for trough
    last_high_idx = 0
    last_high_val = src_high[0]
    last_low_idx = 0
    last_low_val = src_low[0]
    
    # Determine initial direction
    for i in range(1, n):
        if src_high[i] > last_high_val * (1 + pct / 100):
            direction = 1  # trending up, looking for peak
            swings.append((last_low_idx, last_low_val, -1))
            last_high_idx = i
            last_high_val = src_high[i]
            break
        elif src_low[i] < last_low_val * (1 - pct / 100):
            direction = -1  # trending down, looking for trough
            swings.append((last_high_idx, last_high_val, 1))
            last_low_idx = i
            last_low_val = src_low[i]
            break
        else:
            if src_high[i] > last_high_val:
                last_high_idx = i
                last_high_val = src_high[i]
            if src_low[i] < last_low_val:
                last_low_idx = i
                last_low_val = src_low[i]
    
    if direction == 0:
        # No significant move found
        return pd.Series(src_high, index=close.index)
    
    # Scan for swings
    start = max(last_high_idx, last_low_idx) + 1
    for i in range(start, n):
        if direction == 1:  # trending up
            if src_high[i] > last_high_val:
                last_high_idx = i
                last_high_val = src_high[i]
            elif src_low[i] < last_high_val * (1 - pct / 100):
                # Reversal down: mark peak
                swings.append((last_high_idx, last_high_val, 1))
                direction = -1
                last_low_idx = i
                last_low_val = src_low[i]
        else:  # trending down
            if src_low[i] < last_low_val:
                last_low_idx = i
                last_low_val = src_low[i]
            elif src_high[i] > last_low_val * (1 + pct / 100):
                # Reversal up: mark trough
                swings.append((last_low_idx, last_low_val, -1))
                direction = 1
                last_high_idx = i
                last_high_val = src_high[i]
    
    # Add the last point (current trend endpoint)
    if direction == 1:
        swings.append((last_high_idx, last_high_val, 1))
    else:
        swings.append((last_low_idx, last_low_val, -1))
    
    # Interpolate between swing points
    result = np.full(n, np.nan)
    for j in range(len(swings)):
        idx, val, _ = swings[j]
        result[idx] = val
    
    # Linear interpolation between swing points
    if len(swings) >= 2:
        for j in range(len(swings) - 1):
            i1, v1, _ = swings[j]
            i2, v2, _ = swings[j + 1]
            if i2 > i1:
                for k in range(i1, i2 + 1):
                    result[k] = v1 + (v2 - v1) * (k - i1) / (i2 - i1)
    
    # Fill leading NaNs
    first_swing = swings[0][0] if swings else 0
    first_val = swings[0][1] if swings else src_high[0]
    for k in range(0, first_swing):
        result[k] = first_val
    
    return pd.Series(result, index=close.index)


def TROUGHBARS(close: pd.Series, high: pd.Series, low: pd.Series,
               mode: int, pct: float, n: int = 1) -> pd.Series:
    """
    Bars since the nth most recent trough in ZIG(mode, pct).
    Returns 0 on the bar where the trough occurs.
    """
    length = len(close)
    
    # Compute ZIG to find swing points
    zig = ZIG(close, high, low, mode, pct)
    
    # Find troughs: ZIG turning up after going down
    troughs = []
    for i in range(1, length):
        if not np.isnan(zig.iloc[i]) and not np.isnan(zig.iloc[i - 1]):
            if i + 1 < length and zig.iloc[i] <= zig.iloc[i - 1] and zig.iloc[i] <= zig.iloc[i + 1]:
                troughs.append(i)
            elif i >= 2 and zig.iloc[i - 1] < zig.iloc[i - 2] and zig.iloc[i] > zig.iloc[i - 1]:
                # The bar before the turn-up is the trough
                troughs.append(i - 1)
    
    # Also check: swing points marked as troughs in ZIG
    # More robust: re-scan the ZIG swings directly
    zig_vals = zig.values
    trough_bars_set = set()
    for i in range(1, length - 1):
        if (not np.isnan(zig_vals[i]) and not np.isnan(zig_vals[i-1]) 
            and not np.isnan(zig_vals[i+1])):
            if zig_vals[i] <= zig_vals[i-1] and zig_vals[i] <= zig_vals[i+1]:
                trough_bars_set.add(i)
    
    # Build sorted unique trough list
    all_troughs = sorted(trough_bars_set)
    
    # For each bar, find bars since nth most recent trough
    result = np.full(length, np.nan)
    for i in range(length):
        # Find troughs up to bar i
        past_troughs = [t for t in all_troughs if t <= i]
        if len(past_troughs) >= n:
            nth_trough = past_troughs[-n]
            result[i] = i - nth_trough
    
    return pd.Series(result, index=close.index)


def DMA(series: pd.Series, alpha: float) -> pd.Series:
    """
    Dynamic Moving Average.
    DMA(X, A) = A * X + (1 - A) * REF(DMA, 1)
    """
    result = np.zeros(len(series))
    result[0] = series.iloc[0]
    a = alpha
    for i in range(1, len(series)):
        result[i] = a * series.iloc[i] + (1 - a) * result[i - 1]
    return pd.Series(result, index=series.index)


def POW(series: pd.Series, exp: float) -> pd.Series:
    """
    Power function with sign preservation for fractional exponents.
    POW(x, 0.3) — preserves sign for negative values.
    """
    vals = series.values.astype(float)
    result = np.sign(vals) * np.power(np.abs(vals), exp)
    return pd.Series(result, index=series.index)


def STD(series: pd.Series, period: int) -> pd.Series:
    """Rolling standard deviation over N periods."""
    return series.rolling(window=period, min_periods=1).std()


def SAR(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 4, step: float = 2, maxp: float = 20) -> pd.Series:
    """
    Parabolic SAR (TDX-style parameters).
    n: initial lookback period
    step: acceleration factor increment (2 = 0.02)
    maxp: max acceleration factor (20 = 0.20)
    """
    af_step = step / 100
    af_max = maxp / 100
    
    length = len(close)
    sar = np.zeros(length)
    
    if length < n:
        return pd.Series(sar, index=close.index)
    
    # Initialize
    h = high.values.astype(float)
    l = low.values.astype(float)
    c = close.values.astype(float)
    
    # Determine initial direction from first n bars
    initial_high = np.max(h[:n])
    initial_low = np.min(l[:n])
    
    is_long = c[n - 1] > c[0]  # initial trend guess
    
    if is_long:
        sar[n - 1] = initial_low
        ep = initial_high
    else:
        sar[n - 1] = initial_high
        ep = initial_low
    
    af = af_step
    
    for i in range(n - 1):
        sar[i] = sar[n - 1]
    
    for i in range(n, length):
        if is_long:
            sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
            # SAR can't be above prior two lows
            sar[i] = min(sar[i], l[i - 1])
            if i >= 2:
                sar[i] = min(sar[i], l[i - 2])
            
            if l[i] < sar[i]:
                # Switch to short
                is_long = False
                sar[i] = ep
                ep = l[i]
                af = af_step
            else:
                if h[i] > ep:
                    ep = h[i]
                    af = min(af + af_step, af_max)
        else:
            sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
            # SAR can't be below prior two highs
            sar[i] = max(sar[i], h[i - 1])
            if i >= 2:
                sar[i] = max(sar[i], h[i - 2])
            
            if h[i] > sar[i]:
                # Switch to long
                is_long = True
                sar[i] = ep
                ep = h[i]
                af = af_step
            else:
                if l[i] < ep:
                    ep = l[i]
                    af = min(af + af_step, af_max)
    
    return pd.Series(sar, index=close.index)


def FILTER(cond: pd.Series, n: int) -> pd.Series:
    """
    When cond is True, suppress the next N bars from being True.
    Signal cooldown / debounce.
    """
    result = np.zeros(len(cond), dtype=bool)
    cooldown = 0
    for i in range(len(cond)):
        if cooldown > 0:
            cooldown -= 1
            result[i] = False
        elif cond.iloc[i]:
            result[i] = True
            cooldown = n
        else:
            result[i] = False
    return pd.Series(result, index=cond.index)


def BACKSET(cond: pd.Series, n_series) -> pd.Series:
    """
    When cond is True, set the previous N bars to 1 (retroactive flag).
    n_series can be int or Series.
    """
    result = np.zeros(len(cond))
    for i in range(len(cond)):
        if cond.iloc[i]:
            if isinstance(n_series, (int, float)):
                n = int(n_series)
            else:
                n = int(n_series.iloc[i]) if not np.isnan(n_series.iloc[i]) else 0
            start = max(0, i - n + 1)
            result[start:i + 1] = 1
    return pd.Series(result, index=cond.index)
