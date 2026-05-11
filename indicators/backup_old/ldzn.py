"""
LDZN Indicator — Full Python implementation.

4 systems:
  1. RTE (Range Trend Evaluator) — dual-timeframe stochastic reversals
  2. VXLD PRO — HMA momentum + risk control
  3. FVG — Fair Value Gap detection
  4. KDJ + Volume — quantitative entry signals

Alert signals (configurable per indicator):
  - RTE_OS_REV: Bottom reversal (BUY)
  - RTE_OB_REV: Top reversal (SELL)
"""
import numpy as np
import pandas as pd
from core.ta import HHV, LLV, MA, EMA, WMA, SMA, REF, CROSS, BARSLAST, COUNT


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all LDZN signals on OHLCV DataFrame.
    
    Expects columns: open, close, high, low, volume
    Returns DataFrame with all computed columns appended.
    """
    out = df.copy()
    
    _compute_rte(out)
    _compute_vxld(out)
    _compute_fvg(out)
    _compute_kdj(out)
    
    return out


def _compute_rte(df: pd.DataFrame):
    """Part 1: RTE Core System."""
    # Parameters
    SHORT = 21
    LONG = 112
    TH = 20
    SF = 7
    LF = 3
    AVG_MA = 3
    USE_AVG = 0
    
    src = df["close"]
    
    # Short range
    hh_s = HHV(src, SHORT)
    ll_s = LLV(src, SHORT)
    den_s = hh_s - ll_s
    den_s_safe = den_s.replace(0, 0.000001)
    sr_raw = 100 * (src - hh_s) / den_s_safe
    
    # Long range
    hh_l = HHV(src, LONG)
    ll_l = LLV(src, LONG)
    den_l = hh_l - ll_l
    den_l_safe = den_l.replace(0, 0.000001)
    lr_raw = 100 * (src - hh_l) / den_l_safe
    
    # Smoothing
    sr = EMA(sr_raw, SF) if SF > 1 else sr_raw
    lr = EMA(lr_raw, LF) if LF > 1 else lr_raw
    
    # Average (for optional mode)
    avg_raw = (sr_raw + lr_raw) / 2
    avg = EMA(avg_raw, AVG_MA) if AVG_MA > 1 else avg_raw
    
    # Overbought / Oversold states
    if USE_AVG:
        ob = avg >= -TH
        os_ = avg <= (-100 + TH)
    else:
        ob = (sr >= -TH) & (lr >= -TH)
        os_ = (sr <= (-100 + TH)) & (lr <= (-100 + TH))
    
    # Signal triggers
    # Reversals — leaving OB/OS zone
    df["rte_ob"] = ob
    df["rte_os"] = os_
    ob_prev = REF(ob.astype(float)).fillna(0).astype(bool)
    os_prev = REF(os_.astype(float)).fillna(0).astype(bool)
    df["RTE_OB_REV"] = ob_prev & ~ob         # was OB, now not → SELL
    df["RTE_OS_REV"] = os_prev & ~os_        # was OS, now not → BUY
    
    # Zone entries
    df["RTE_OB_START"] = ob & ~ob_prev
    df["RTE_OS_START"] = os_ & ~os_prev
    
    # Store intermediate values for context
    df["rte_sr"] = sr
    df["rte_lr"] = lr
    
    # Trend line (for context in alerts)
    df["trend_val"] = WMA(WMA(src, 8), 8)
    df["ma_180"] = MA(src, 180)


def _compute_vxld(df: pd.DataFrame):
    """Part 2: VXLD PRO — HMA momentum + risk control."""
    HMA_P = 18
    SLOPE_T = 0.02
    
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]
    
    # HMA calculation
    hma_half = WMA(close, HMA_P // 2)
    hma_full = WMA(close, HMA_P)
    hma_diff = 2 * hma_half - hma_full
    hma_sqrt = max(1, int(np.sqrt(HMA_P)))
    hma_main = WMA(hma_diff, hma_sqrt)
    
    hma_prev = REF(hma_main)
    hma_slope = (hma_main - hma_prev) / hma_prev.replace(0, 0.000001) * 100
    
    # States
    df["vxld_is_red"] = hma_slope > SLOPE_T
    df["vxld_is_green"] = hma_slope < -SLOPE_T
    df["vxld_is_yellow"] = hma_slope.abs() <= SLOPE_T
    
    # Trend filter
    ma_filter = EMA(close, 60)
    df["vxld_bull_trend"] = close > ma_filter
    df["vxld_bear_trend"] = close < ma_filter
    
    # Smart stops
    df["vxld_stop_long"] = REF(LLV(low, 5))   # stop for longs
    df["vxld_stop_short"] = REF(HHV(high, 5))  # stop for shorts
    
    # Volume breakout
    hv_bar = vol == HHV(vol, 30)
    bars_since_hv = BARSLAST(hv_bar)
    # Simplified breakout: current close > high of last highest-volume bar
    ref_high_at_hv = pd.Series(np.nan, index=df.index)
    for i in range(len(df)):
        n = bars_since_hv.iloc[i]
        if not np.isnan(n) and n >= 0:
            idx = max(0, i - int(n))
            ref_high_at_hv.iloc[i] = high.iloc[idx]
    
    df["vxld_breakout"] = (close > ref_high_at_hv) & (REF(close) <= ref_high_at_hv)
    df["hma_slope"] = hma_slope


def _compute_fvg(df: pd.DataFrame):
    """Part 3: FVG — Fair Value Gap detection."""
    ATR_N = 14
    ATR_MUL = 0.5
    BODY_MUL = 1.2
    TREND_P = 60
    EXT_LEN = 30
    
    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    
    # ATR
    tr = pd.concat([
        high - low,
        (REF(close) - high).abs(),
        (REF(close) - low).abs()
    ], axis=1).max(axis=1)
    atr = MA(tr, ATR_N)
    
    # Body analysis
    avg_body = MA((close - open_).abs(), 20)
    mid_body = (REF(close) - REF(open_)).abs()
    is_big = mid_body > REF(avg_body) * BODY_MUL
    
    # Trend
    ma_trend = MA(close, TREND_P)
    trend_up = close > ma_trend
    trend_dn = close < ma_trend
    
    # Raw FVG
    raw_bull = low > REF(high, 2)
    raw_bear = high < REF(low, 2)
    gap_bull = low - REF(high, 2)
    gap_bear = REF(low, 2) - high
    
    # Filtered
    df["fvg_bull"] = raw_bull & (gap_bull > atr * ATR_MUL) & is_big & trend_up
    df["fvg_bear"] = raw_bear & (gap_bear > atr * ATR_MUL) & is_big & trend_dn
    
    # Gap levels (for the bar where FVG forms)
    df["fvg_bull_top"] = np.where(df["fvg_bull"], low, np.nan)
    df["fvg_bull_bot"] = np.where(df["fvg_bull"], REF(high, 2), np.nan)
    df["fvg_bear_top"] = np.where(df["fvg_bear"], REF(low, 2), np.nan)
    df["fvg_bear_bot"] = np.where(df["fvg_bear"], high, np.nan)
    
    # Tracking (simplified — track most recent FVG)
    t_bull = BARSLAST(df["fvg_bull"])
    t_bear = BARSLAST(df["fvg_bear"])
    
    # Keep levels from most recent FVG
    bb_top = pd.Series(np.nan, index=df.index)
    bb_bot = pd.Series(np.nan, index=df.index)
    ss_top = pd.Series(np.nan, index=df.index)
    ss_bot = pd.Series(np.nan, index=df.index)
    
    for i in range(len(df)):
        # Bull FVG tracking
        tb = t_bull.iloc[i]
        if not np.isnan(tb) and 0 <= int(tb) <= EXT_LEN:
            src_idx = i - int(tb)
            if src_idx >= 0:
                bb_top.iloc[i] = df["fvg_bull_top"].iloc[src_idx]
                bb_bot.iloc[i] = df["fvg_bull_bot"].iloc[src_idx]
        
        # Bear FVG tracking
        tb2 = t_bear.iloc[i]
        if not np.isnan(tb2) and 0 <= int(tb2) <= EXT_LEN:
            src_idx = i - int(tb2)
            if src_idx >= 0:
                ss_top.iloc[i] = df["fvg_bear_top"].iloc[src_idx]
                ss_bot.iloc[i] = df["fvg_bear_bot"].iloc[src_idx]
    
    # Invalidation
    bull_alive = (~df["fvg_bull"]) & (t_bull >= 0) & (t_bull <= EXT_LEN) & bb_bot.notna()
    bear_alive = (~df["fvg_bear"]) & (t_bear >= 0) & (t_bear <= EXT_LEN) & ss_top.notna()
    
    # Check if price closed below/above
    for i in range(len(df)):
        if bull_alive.iloc[i] and close.iloc[i] < bb_bot.iloc[i]:
            bull_alive.iloc[i] = False
        if bear_alive.iloc[i] and close.iloc[i] > ss_top.iloc[i]:
            bear_alive.iloc[i] = False
    
    df["fvg_bull_alive"] = bull_alive
    df["fvg_bear_alive"] = bear_alive
    bull_alive_prev = REF(bull_alive.astype(float)).fillna(0).astype(bool)
    bear_alive_prev = REF(bear_alive.astype(float)).fillna(0).astype(bool)
    df["fvg_kill_bull"] = bull_alive_prev & (close < bb_bot)
    df["fvg_kill_bear"] = bear_alive_prev & (close > ss_top)


def _compute_kdj(df: pd.DataFrame):
    """Part 4: KDJ + Volume signals."""
    K_P1 = 9
    K_P2 = 3
    K_P3 = 3
    
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]
    
    # RSV
    hh = HHV(high, K_P1)
    ll = LLV(low, K_P1)
    den = (hh - ll).replace(0, 0.000001)
    rsv = (close - ll) / den * 100
    
    # KDJ
    k_val = SMA(rsv, K_P2, 1)
    d_val = SMA(k_val, K_P3, 1)
    j_val = 3 * k_val - 2 * d_val
    
    df["kdj_k"] = k_val
    df["kdj_d"] = d_val
    df["kdj_j"] = j_val
    
    # Zones (based on previous bar)
    up_zone = (REF(k_val) >= 75) & (REF(d_val) >= 75) & (REF(j_val) >= 75)
    dn_zone = (REF(k_val) <= 25) & (REF(d_val) <= 25) & (REF(j_val) <= 25)
    
    # Cross signals
    s1_k = CROSS(d_val, k_val) & up_zone    # death cross in OB
    g1_k = CROSS(k_val, d_val) & up_zone    # golden cross in OB (invalidates sell)
    j1_k = CROSS(k_val, d_val) & dn_zone    # golden cross in OS (buy)
    d1_k = CROSS(d_val, k_val) & dn_zone    # death cross in OS (invalidates buy)
    
    # BACKSET cancellation (simplified: mark signal as invalid if later invalidated)
    # For real-time, we only care about the latest bar, so we check if the signal
    # was subsequently cancelled
    kill_s = pd.Series(False, index=df.index)
    kill_j = pd.Series(False, index=df.index)
    
    last_s1 = -1
    last_j1 = -1
    for i in range(len(df)):
        if s1_k.iloc[i]:
            last_s1 = i
        if g1_k.iloc[i] and last_s1 >= 0:
            # Invalidate all s1 signals back to last_s1
            kill_s.iloc[last_s1:i + 1] = True
        
        if j1_k.iloc[i]:
            last_j1 = i
        if d1_k.iloc[i] and last_j1 >= 0:
            kill_j.iloc[last_j1:i + 1] = True
    
    cond_s = s1_k & ~kill_s
    cond_j = j1_k & ~kill_j
    
    # Volume confirmation
    vol5 = MA(vol, 5)
    is_vol_up = vol > vol5 * 1.2
    
    df["KDJ_STRONG_BUY"] = cond_j & is_vol_up
    df["KDJ_WEAK_BUY"] = cond_j & ~is_vol_up
    df["KDJ_STRONG_SELL"] = cond_s & is_vol_up
    df["KDJ_WEAK_SELL"] = cond_s & ~is_vol_up


# ─── Signal extraction ─────────────────────────────────────────

# Define which signals this indicator can produce
ALERT_SIGNALS = {
    "RTE_OS_REV": {
        "column": "RTE_OS_REV",
        "direction": "BUY",
        "emoji": "🟢",
        "description": "RTE Bottom Reversal — leaving oversold zone",
    },
    "RTE_OB_REV": {
        "column": "RTE_OB_REV",
        "direction": "SELL",
        "emoji": "🔴",
        "description": "RTE Top Reversal — leaving overbought zone",
    },
    "KDJ_STRONG_BUY": {
        "column": "KDJ_STRONG_BUY",
        "direction": "BUY",
        "emoji": "📈",
        "description": "KDJ Golden Cross in oversold zone + volume surge",
    },
    "KDJ_STRONG_SELL": {
        "column": "KDJ_STRONG_SELL",
        "direction": "SELL",
        "emoji": "📉",
        "description": "KDJ Death Cross in overbought zone + volume surge",
    },
}

# Signals Daniel wants alerts for
ACTIVE_ALERTS = ["RTE_OS_REV", "RTE_OB_REV", "KDJ_STRONG_BUY", "KDJ_STRONG_SELL"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """
    Check the latest bar for active alert signals.
    Returns list of triggered signals with context.
    """
    signals = []
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
                # VXLD context
                "hma_state": (
                    "🚀 RED (bullish momentum)" if last.get("vxld_is_red", False)
                    else "❄️ GREEN (bearish momentum)" if last.get("vxld_is_green", False)
                    else "🟡 YELLOW (consolidation)"
                ),
                "trend": "BULL" if last.get("vxld_bull_trend", False) else "BEAR",
                "stop_long": last.get("vxld_stop_long", None),
                "stop_short": last.get("vxld_stop_short", None),
                # RTE context
                "rte_sr": round(last.get("rte_sr", 0), 2),
                "rte_lr": round(last.get("rte_lr", 0), 2),
            }
            signals.append(context)
    
    return signals
