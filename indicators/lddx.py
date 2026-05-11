"""
LDDX Indicator — RSI Divergence + KDJ + EMA Crossover + Take-Profit.

Three subsystems:
  A. RSI(14) divergence + KDJ(9,3,3) zone signals + volume confirmation
  B. EMA(10)/EMA(21) crossover + volume surge detection
  C. Adaptive take-profit signal (兑现) based on MA spread + KDJ J-line

Alert signals:
  - LDDX_STRONG_BUY:  KDJ buy in oversold + volume + RSI bottom condition
  - LDDX_STRONG_SELL: KDJ sell in overbought + volume + RSI top condition
  - LDDX_EMA_BUY:     EMA(10) crosses above EMA(21)
  - LDDX_EMA_SELL:    EMA(10) crosses below EMA(21)
  - LDDX_TAKE_PROFIT: Take-profit signal (J dropping from high + KDJ death cross)
"""
import numpy as np
import pandas as pd
from core.ta import (HHV, LLV, MA, EMA, SMA, REF, CROSS,
                     BARSLAST, BACKSET, FILTER)


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all LDDX signals on OHLCV DataFrame."""
    out = df.copy()
    
    _compute_part_a(out)
    _compute_part_b(out)
    _compute_part_c(out)
    
    return out


def _compute_part_a(df: pd.DataFrame):
    """Part A: RSI Divergence + KDJ + Volume."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]
    
    # --- RSI(14) ---
    lc = REF(close).fillna(close)
    diff = close - lc
    temp1 = diff.clip(lower=0)
    temp2 = diff.abs()
    rsi = SMA(temp1, 14, 1) / SMA(temp2, 14, 1).replace(0, 0.000001) * 100
    df["lddx_rsi"] = rsi
    
    # --- Volume ratio ---
    vol_ma5 = MA(vol, 5).replace(0, 0.000001)
    volr = vol / vol_ma5
    is_vol_spike = volr > 1.3
    
    # --- RSI Divergence detection ---
    N = 3
    
    # Top divergence (bearish): price higher, RSI lower
    rsi_n = REF(rsi, N).fillna(0)
    a1 = rsi_n == HHV(rsi, 2 * N + 1)
    b1 = BACKSET(a1, N + 1)
    c1 = FILTER(b1.astype(bool), N)
    period_top = BARSLAST(REF(c1.astype(float)).fillna(0).astype(bool))
    
    # Dynamic lookback for divergence
    top_div = pd.Series(False, index=df.index)
    for i in range(len(df)):
        if c1.iloc[i]:
            pt = period_top.iloc[i]
            if not np.isnan(pt) and pt >= 0:
                lookback = int(pt) + 1
                if i - lookback >= 0:
                    if (close.iloc[i - lookback] < close.iloc[i] and
                            rsi.iloc[i - lookback] > rsi.iloc[i]):
                        top_div.iloc[i] = True
    
    # Bottom divergence (bullish): price lower, RSI higher
    a2 = rsi_n == LLV(rsi, 2 * N + 1)
    b2 = BACKSET(a2, N + 1)
    c2 = FILTER(b2.astype(bool), N)
    period_bot = BARSLAST(REF(c2.astype(float)).fillna(0).astype(bool))
    
    bot_div = pd.Series(False, index=df.index)
    for i in range(len(df)):
        if c2.iloc[i]:
            pb = period_bot.iloc[i]
            if not np.isnan(pb) and pb >= 0:
                lookback = int(pb) + 1
                if i - lookback >= 0:
                    if (close.iloc[i - lookback] > close.iloc[i] and
                            rsi.iloc[i - lookback] < rsi.iloc[i]):
                        bot_div.iloc[i] = True
    
    df["lddx_top_div"] = top_div
    df["lddx_bot_div"] = bot_div
    
    # --- KDJ(9,3,3) ---
    P1, P2, P3 = 9, 3, 3
    hh = HHV(high, P1)
    ll = LLV(low, P1)
    den = (hh - ll).replace(0, 0.000001)
    rsv = (close - ll) / den * 100
    
    k_val = SMA(rsv, P2, 1)
    d_val = SMA(k_val, P3, 1)
    j_val = 3 * k_val - 2 * d_val
    
    df["lddx_k"] = k_val
    df["lddx_d"] = d_val
    df["lddx_j"] = j_val
    
    # Zones
    k_prev = REF(k_val).fillna(50)
    d_prev = REF(d_val).fillna(50)
    j_prev = REF(j_val).fillna(50)
    up_zone = (k_prev >= 75) & (d_prev >= 75) & (j_prev >= 75)
    dn_zone = (k_prev <= 25) & (d_prev <= 25) & (j_prev <= 25)
    
    # Cross signals
    s1 = CROSS(d_val, k_val) & up_zone
    g1 = CROSS(k_val, d_val) & up_zone
    j1 = CROSS(k_val, d_val) & dn_zone
    d1 = CROSS(d_val, k_val) & dn_zone
    
    # BACKSET cancellation
    kill_s = pd.Series(False, index=df.index)
    kill_j = pd.Series(False, index=df.index)
    
    last_s1 = -1
    last_j1 = -1
    for i in range(len(df)):
        if s1.iloc[i]:
            last_s1 = i
        if g1.iloc[i] and last_s1 >= 0:
            kill_s.iloc[last_s1:i + 1] = True
        if j1.iloc[i]:
            last_j1 = i
        if d1.iloc[i] and last_j1 >= 0:
            kill_j.iloc[last_j1:i + 1] = True
    
    base_s = s1 & ~kill_s
    base_j = j1 & ~kill_j
    
    # Volume confirmation
    vol5 = MA(vol, 5)
    is_vol_up = vol > vol5 * 1.2
    
    cond_s_strong = base_s & is_vol_up
    cond_s_weak = base_s & ~is_vol_up
    cond_j_strong = base_j & is_vol_up
    cond_j_weak = base_j & ~is_vol_up
    
    # --- RSI conditions ---
    rsi_ob = rsi >= 70
    rsi_os = rsi <= 30
    rsi_prev = REF(rsi).fillna(50)
    rsi_break_ob = (rsi_prev >= 70) & (rsi < 70)
    rsi_break_os = (rsi_prev <= 30) & (rsi > 30)
    
    top_cond = rsi_ob | top_div | is_vol_spike | rsi_break_ob
    bot_cond = rsi_os | bot_div | is_vol_spike | rsi_break_os
    
    # --- Final signals ---
    df["LDDX_STRONG_SELL"] = cond_s_strong & top_cond
    df["LDDX_STRONG_BUY"] = cond_j_strong & bot_cond
    df["LDDX_WEAK_SELL"] = cond_s_weak & top_cond
    df["LDDX_WEAK_BUY"] = cond_j_weak & bot_cond
    
    # Context
    df["lddx_vol_ratio"] = volr
    df["lddx_vol_spike"] = is_vol_spike


def _compute_part_b(df: pd.DataFrame):
    """Part B: EMA Crossover + Volume Surge."""
    close = df["close"]
    vol = df["volume"]
    
    ema10 = EMA(close, 10)
    ema21 = EMA(close, 21)
    slope = ema10 - ema21
    slope_prev = REF(slope).fillna(0)
    
    df["LDDX_EMA_BUY"] = (slope > 0) & (slope_prev <= 0)
    df["LDDX_EMA_SELL"] = (slope < 0) & (slope_prev >= 0)
    
    # Volume surge
    vma5 = MA(vol, 5).replace(0, 0.000001)
    df["lddx_vol_surge"] = vol > vma5 * 1.8
    
    df["lddx_ema10"] = ema10
    df["lddx_ema21"] = ema21


def _compute_part_c(df: pd.DataFrame):
    """Part C: Take-Profit Signal (兑现)."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    
    ma10 = MA(close, 10)
    ma30 = MA(close, 30)
    ts = ((ma10 - ma30).abs() / ma30.replace(0, 0.000001)) * 100
    
    q = ts > 2.5
    r = (ts > 1.5) & (ts <= 2.5)
    
    # RSI(14) — reuse same calculation
    lc = REF(close).fillna(close)
    diff = close - lc
    rsi14 = SMA(diff.clip(lower=0), 14, 1) / SMA(diff.abs(), 14, 1).replace(0, 0.000001) * 100
    
    # KDJ with EMA smoothing
    hh9 = HHV(high, 9)
    ll9 = LLV(low, 9)
    den9 = (hh9 - ll9).replace(0, 0.000001)
    rsv_c = (close - ll9) / den9 * 100
    
    k_c = EMA(rsv_c, 3)
    d_c = EMA(k_c, 3)
    j_c = 3 * k_c - 2 * d_c
    
    # Death cross
    ks_c = CROSS(d_c, k_c)
    
    # J dropping
    j_prev = REF(j_c).fillna(0)
    jdn = j_c < j_prev
    
    # J high threshold (adaptive)
    j_thresh = pd.Series(82.0, index=df.index)
    j_thresh[q] = 88.0
    j_thresh[r] = 85.0
    jhig = j_c > j_thresh
    
    df["LDDX_TAKE_PROFIT"] = jdn & jhig & ks_c
    df["lddx_j_c"] = j_c
    df["lddx_ma_spread"] = ts


# ─── Signal definitions ─────────────────────────────────────────

ALERT_SIGNALS = {
    "LDDX_STRONG_BUY": {
        "column": "LDDX_STRONG_BUY",
        "direction": "BUY",
        "emoji": "📈",
        "description": "LDDX Strong Buy — KDJ oversold + volume + RSI bottom condition",
    },
    "LDDX_STRONG_SELL": {
        "column": "LDDX_STRONG_SELL",
        "direction": "SELL",
        "emoji": "📉",
        "description": "LDDX Strong Sell — KDJ overbought + volume + RSI top condition",
    },
    "LDDX_EMA_BUY": {
        "column": "LDDX_EMA_BUY",
        "direction": "BUY",
        "emoji": "🔵",
        "description": "LDDX EMA Crossover — EMA(10) crosses above EMA(21)",
    },
    "LDDX_EMA_SELL": {
        "column": "LDDX_EMA_SELL",
        "direction": "SELL",
        "emoji": "🔵",
        "description": "LDDX EMA Crossover — EMA(10) crosses below EMA(21)",
    },
    "LDDX_TAKE_PROFIT": {
        "column": "LDDX_TAKE_PROFIT",
        "direction": "SELL",
        "emoji": "💰",
        "description": "LDDX Take Profit — J dropping from high zone + KDJ death cross",
    },
}

# Default: strong signals + take profit
ACTIVE_ALERTS = ["LDDX_STRONG_BUY", "LDDX_STRONG_SELL", "LDDX_TAKE_PROFIT"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check last COMPLETED bar for signals (iloc[-2], not the in-progress bar)."""
    signals = []
    if len(df) < 2:
        return signals
    last = df.iloc[-2]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            # Build context details
            details = []
            if last.get("lddx_top_div", False):
                details.append("RSI bearish divergence")
            if last.get("lddx_bot_div", False):
                details.append("RSI bullish divergence")
            if last.get("lddx_vol_spike", False):
                details.append("Volume spike (>1.3x)")
            if last.get("lddx_vol_surge", False):
                details.append("Volume surge (>1.8x)")
            
            rsi_val = round(last.get("lddx_rsi", 0), 1)
            
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
                # LDDX-specific context
                "rsi": rsi_val,
                "details": ", ".join(details) if details else "—",
                # Compatibility fields
                "hma_state": f"RSI: {rsi_val} | {', '.join(details) if details else 'Clean signal'}",
                "trend": "BULL" if last.get("lddx_ema10", 0) > last.get("lddx_ema21", 0) else "BEAR",
                "stop_long": None,
                "stop_short": None,
            }
            signals.append(context)
    
    return signals

def get_signals_completed(df):
    """Push-mode: use iloc[-1] since this IS the completed bar."""
    import pandas as pd
    if len(df) < 2:
        return []
    # Temporarily append a dummy row so get_signals (which uses iloc[-2]) reads our bar
    dummy = df.iloc[-1:].copy()
    df_ext = pd.concat([df, dummy], ignore_index=True)
    return get_signals(df_ext)

