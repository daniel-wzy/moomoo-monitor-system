"""
LDZN V2 — V型律动 Main Chart Indicator

4 Systems:
  1. RTE - Dual-timeframe stochastic reversals
  2. VXLD - HMA momentum + risk control
  3. FVG - Fair Value Gap detection  
  4. KDJ + Volume - Diamond signals (RED/BLUE/GREEN/YELLOW)

Diamond Signals:
  - RED (25): Strong BUY - KDJ golden cross in oversold + VOLUME
  - BLUE (26): Weak BUY - KDJ golden cross in oversold, no volume
  - GREEN (27): Strong SELL - KDJ death cross in overbought + VOLUME
  - YELLOW (24): Weak SELL - KDJ death cross in overbought, no volume
"""
import numpy as np
import pandas as pd
from core.ta import HHV, LLV, MA, EMA, WMA, SMA, REF, CROSS, BARSLAST, COUNT


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all LDZN signals."""
    out = df.copy()
    
    close = out["close"]
    high = out["high"]
    low = out["low"]
    vol = out["volume"]
    
    # ═══════════════════════════════════════════════════════════════
    # PART 1: RTE Core System
    # ═══════════════════════════════════════════════════════════════
    RTE_SHORT, RTE_LONG, RTE_TH = 21, 112, 20
    RTE_SF, RTE_LF = 7, 3
    
    # Short range stochastic
    hh_s = HHV(close, RTE_SHORT)
    ll_s = LLV(close, RTE_SHORT)
    den_s = (hh_s - ll_s).replace(0, 0.000001)
    sr_raw = 100 * (close - hh_s) / den_s
    sr = EMA(sr_raw, RTE_SF)
    
    # Long range stochastic
    hh_l = HHV(close, RTE_LONG)
    ll_l = LLV(close, RTE_LONG)
    den_l = (hh_l - ll_l).replace(0, 0.000001)
    lr_raw = 100 * (close - hh_l) / den_l
    lr = EMA(lr_raw, RTE_LF)
    
    # Overbought/Oversold states
    rte_ob = (sr >= -RTE_TH) & (lr >= -RTE_TH)
    rte_os = (sr <= -100 + RTE_TH) & (lr <= -100 + RTE_TH)
    
    # Reversals
    ob_prev = REF(rte_ob.astype(float)).fillna(0).astype(bool)
    os_prev = REF(rte_os.astype(float)).fillna(0).astype(bool)
    out["RTE_OB_REV"] = ob_prev & ~rte_ob  # Leaving overbought → SELL
    out["RTE_OS_REV"] = os_prev & ~rte_os  # Leaving oversold → BUY
    out["rte_ob"] = rte_ob
    out["rte_os"] = rte_os
    
    # ═══════════════════════════════════════════════════════════════
    # PART 2: VXLD PRO - HMA Momentum
    # ═══════════════════════════════════════════════════════════════
    HMA_P = 18
    SLOPE_T = 0.02
    
    hma_half = WMA(close, HMA_P // 2)
    hma_full = WMA(close, HMA_P)
    hma_diff = 2 * hma_half - hma_full
    hma_sqrt = max(1, int(np.sqrt(HMA_P)))
    hma_main = WMA(hma_diff, hma_sqrt)
    
    hma_prev = REF(hma_main).fillna(hma_main)
    hma_slope = (hma_main - hma_prev) / hma_prev.replace(0, 0.000001) * 100
    
    out["HMA_RED"] = hma_slope > SLOPE_T      # Bullish momentum
    out["HMA_GREEN"] = hma_slope < -SLOPE_T   # Bearish momentum
    out["HMA_YELLOW"] = hma_slope.abs() <= SLOPE_T  # Consolidation
    out["hma_slope"] = hma_slope
    
    # Trend filter
    ma_filter = EMA(close, 60)
    out["BULL_TREND"] = close > ma_filter
    out["BEAR_TREND"] = close < ma_filter
    
    # Smart stops
    out["STOP_LONG"] = REF(LLV(low, 5))
    out["STOP_SHORT"] = REF(HHV(high, 5))
    
    # 180 MA Bull/Bear line
    ma_180 = MA(close, 180)
    out["ABOVE_180"] = close >= ma_180
    out["BELOW_180"] = close < ma_180
    out["ma_180"] = ma_180
    
    # ═══════════════════════════════════════════════════════════════
    # PART 3: KDJ + Volume - Diamond Signals
    # ═══════════════════════════════════════════════════════════════
    K_P1, K_P2, K_P3 = 9, 3, 3
    
    hh_k = HHV(high, K_P1)
    ll_k = LLV(low, K_P1)
    den_k = (hh_k - ll_k).replace(0, 0.000001)
    rsv = (close - ll_k) / den_k * 100
    
    k_val = SMA(rsv, K_P2, 1)
    d_val = SMA(k_val, K_P3, 1)
    j_val = 3 * k_val - 2 * d_val
    
    out["kdj_k"] = k_val
    out["kdj_d"] = d_val
    out["kdj_j"] = j_val
    
    # Zone detection (previous bar)
    k_prev = REF(k_val).fillna(50)
    d_prev = REF(d_val).fillna(50)
    j_prev = REF(j_val).fillna(50)
    
    up_zone = (k_prev >= 75) & (d_prev >= 75) & (j_prev >= 75)
    dn_zone = (k_prev <= 25) & (d_prev <= 25) & (j_prev <= 25)
    
    # Cross signals
    golden_cross = CROSS(k_val, d_val)  # K crosses above D
    death_cross = CROSS(d_val, k_val)   # D crosses above K
    
    s1 = death_cross & up_zone   # Death cross in overbought
    g1 = golden_cross & up_zone  # Golden cross in overbought (invalidates s1)
    j1 = golden_cross & dn_zone  # Golden cross in oversold
    d1 = death_cross & dn_zone   # Death cross in oversold (invalidates j1)
    
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
    
    cond_s = s1 & ~kill_s  # Confirmed sell
    cond_j = j1 & ~kill_j  # Confirmed buy
    
    # Volume confirmation
    vol5 = MA(vol, 5)
    is_vol_up = vol > vol5 * 1.2
    
    # Diamond signals
    out["RED_DIAMOND"] = cond_j & is_vol_up      # Strong BUY (icon 25)
    out["BLUE_DIAMOND"] = cond_j & ~is_vol_up    # Weak BUY (icon 26)
    out["GREEN_DIAMOND"] = cond_s & is_vol_up    # Strong SELL (icon 27)
    out["YELLOW_DIAMOND"] = cond_s & ~is_vol_up  # Weak SELL (icon 24)
    
    return out


# ═══════════════════════════════════════════════════════════════════
# Signal Definitions
# ═══════════════════════════════════════════════════════════════════

ALERT_SIGNALS = {
    "RED_DIAMOND": {
        "column": "RED_DIAMOND",
        "direction": "BUY",
        "strength": "STRONG",
        "emoji": "🔴",
        "points": 3,
        "description": "KDJ Golden Cross in Oversold + VOLUME",
    },
    "BLUE_DIAMOND": {
        "column": "BLUE_DIAMOND",
        "direction": "BUY",
        "strength": "WEAK",
        "emoji": "🔵",
        "points": 1,
        "description": "KDJ Golden Cross in Oversold (no volume)",
    },
    "GREEN_DIAMOND": {
        "column": "GREEN_DIAMOND",
        "direction": "SELL",
        "strength": "STRONG",
        "emoji": "🟢",
        "points": 3,
        "description": "KDJ Death Cross in Overbought + VOLUME",
    },
    "YELLOW_DIAMOND": {
        "column": "YELLOW_DIAMOND",
        "direction": "SELL",
        "strength": "WEAK",
        "emoji": "🟡",
        "points": 1,
        "description": "KDJ Death Cross in Overbought (no volume)",
    },
    "RTE_OS_REV": {
        "column": "RTE_OS_REV",
        "direction": "BUY",
        "strength": "MEDIUM",
        "emoji": "↑",
        "points": 2,
        "description": "RTE Bottom Reversal - leaving oversold",
    },
    "RTE_OB_REV": {
        "column": "RTE_OB_REV",
        "direction": "SELL",
        "strength": "MEDIUM",
        "emoji": "↓",
        "points": 2,
        "description": "RTE Top Reversal - leaving overbought",
    },
}

ACTIVE_ALERTS = ["RED_DIAMOND", "BLUE_DIAMOND", "GREEN_DIAMOND", "YELLOW_DIAMOND", "RTE_OS_REV", "RTE_OB_REV"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check last COMPLETED bar for signals (iloc[-2], not the in-progress bar)."""
    signals = []
    if len(df) < 2:
        return signals
    last = df.iloc[-2]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            hma_state = "RED" if last.get("HMA_RED", False) else "GREEN" if last.get("HMA_GREEN", False) else "YELLOW"
            context = {
                "signal": name,
                "direction": sig["direction"],
                "strength": sig["strength"],
                "emoji": sig["emoji"],
                "points": sig["points"],
                "description": sig["description"],
                "close": last["close"],
                "volume": last["volume"],
                "hma_state": hma_state,
                "trend": "UP" if last.get("ABOVE_180", False) else "DOWN",
                "above_180": last.get("ABOVE_180", False),
                "kdj_k": round(last.get("kdj_k", 0), 1),
                "kdj_d": round(last.get("kdj_d", 0), 1),
                "kdj_j": round(last.get("kdj_j", 0), 1),
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

