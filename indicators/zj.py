"""
ZJ Indicator — Capital Flow + Order Flow + Modified KDJ.

Three subsystems:
  1. BLUE/BLACK — Institutional entry/exit at 33-period extremes
  2. Attack Flow (攻击流量) — Directional volume pressure
  3. Modified KDJ(39) — Fast-smoothed PINK J-line with 85/15 crosses

Alert signals:
  - ZJ_OB_EXIT: PINK crosses below 85 (overbought exit/sell)
  - ZJ_OS_ENTRY: PINK crosses above 15 (oversold entry/buy)
"""
import numpy as np
import pandas as pd
from core.ta import (HHV, LLV, MA, EMA, SMA, REF, CROSS,
                     COUNT, DMA, POW)


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all ZJ signals on OHLCV DataFrame."""
    out = df.copy()
    
    close = out["close"]
    high = out["high"]
    low = out["low"]
    open_ = out["open"]
    vol = out["volume"]
    
    # Use turnover if available, otherwise approximate
    if "turnover" in out.columns:
        vola = out["turnover"]
    else:
        vola = vol * close
    
    _compute_blue_black(out, close, high, low, open_)
    _compute_attack_flow(out, close, high, low, open_, vol, vola)
    _compute_pink_kdj(out, close, high, low)
    _compute_signals(out, vola)
    
    return out


def _compute_blue_black(df, close, high, low, open_):
    """Part 1: BLUE/BLACK — Institutional entry/exit."""
    # Typical price of previous bar
    var1 = REF((low + open_ + close + high) / 4).fillna(close)
    
    # Bottom detection
    var2_num = SMA((low - var1).abs(), 13, 1)
    var2_den = SMA((low - var1).clip(lower=0), 10, 1).replace(0, 0.000001)
    var2 = var2_num / var2_den
    var3 = EMA(var2, 10)
    var4 = LLV(low, 33)
    
    var5_input = pd.Series(np.where(low <= var4, var3, 0), index=df.index)
    var5 = EMA(var5_input, 3)
    var6 = POW(var5, 0.3)
    
    # Top detection
    var21_num = SMA((high - var1).abs(), 13, 1)
    # MIN(HIGH-VAR1, 0) → clip to max 0 (negative or zero)
    var21_den = SMA((high - var1).clip(upper=0), 10, 1).replace(0, -0.000001)
    var21 = var21_num / var21_den
    var31 = EMA(var21, 10)
    var41 = HHV(high, 33)
    
    var51_input = pd.Series(np.where(high >= var41, -var31, 0), index=df.index)
    var51 = EMA(var51_input, 3)
    var61 = POW(var51, 0.3)
    
    # Normalization
    max_val = pd.concat([var6, var61], axis=1).max(axis=1).max()
    radio1 = 200 / max(max_val, 0.000001)
    
    # BLUE: institutional buying at lows
    var5_prev = REF(var5).fillna(0)
    blue = pd.Series(np.where(var5 > var5_prev, var6 * radio1, 0), index=df.index)
    
    # BLACK: institutional selling at highs
    var61_prev = REF(var61).fillna(0)
    black = pd.Series(np.where(var61 > var61_prev, -var61 * radio1, 0), index=df.index)
    
    df["zj_blue"] = blue
    df["zj_black"] = black
    df["zj_var5"] = var5
    df["zj_var6"] = var6
    df["zj_var61"] = var61


def _compute_attack_flow(df, close, high, low, open_, vol, vola):
    """Part 2: Attack Flow (攻击流量)."""
    # QJJ: volume per adjusted price range
    range_adj = (high - low) * 2 - (close - open_).abs()
    range_adj_safe = range_adj.replace(0, 0.000001)
    qjj = vola / range_adj_safe
    
    # XVL: directional volume
    xvl = pd.Series(np.where(close == open_, 0, (close - open_) * qjj), index=df.index)
    
    # HSL: normalized
    hsl = (xvl / 20) / 1.15
    
    # Attack flow: weighted average
    hsl_1 = REF(hsl).fillna(0)
    hsl_2 = REF(hsl, 2).fillna(0)
    attack_flow = hsl * 0.55 + hsl_1 * 0.33 + hsl_2 * 0.22
    
    # Smoothed
    lljx = EMA(attack_flow, 3)
    
    # Normalize for display
    vola_max = vola.max()
    radio = 10000 / max(vola_max, 0.000001)
    
    df["zj_attack_flow"] = attack_flow
    df["zj_lljx"] = lljx
    df["zj_hsl"] = hsl
    df["zj_flow_radio"] = radio
    
    # Flow states
    df["zj_red"] = lljx > 0      # buying pressure
    df["zj_green"] = (lljx < 0) | (hsl < 0)  # selling pressure
    
    # Dynamic MA of attack flow
    df["zj_lightblue"] = DMA(attack_flow, 0.222228)


def _compute_pink_kdj(df, close, high, low):
    """Part 3: Modified KDJ(39) with fast smoothing."""
    hh = HHV(high, 39)
    ll = LLV(low, 39)
    den = (hh - ll).replace(0, 0.000001)
    rsv = (close - ll) / den * 100
    
    k = SMA(rsv, 2, 1)
    d = SMA(k, 2, 1)
    j = 3 * k - 2 * d
    pink = SMA(j, 2, 1)
    
    df["zj_k"] = k
    df["zj_d"] = d
    df["zj_j"] = j
    df["zj_pink"] = pink


def _compute_signals(df, vola):
    """Part 4: Signal detection."""
    pink = df["zj_pink"]
    blue = df["zj_blue"]
    black = df["zj_black"]
    
    # 85/15 crosses
    pink_85 = pd.Series(85.0, index=df.index)
    pink_15 = pd.Series(15.0, index=df.index)
    
    df["ZJ_OB_EXIT"] = CROSS(pink_85, pink)    # PINK drops below 85 → sell
    df["ZJ_OS_ENTRY"] = CROSS(pink, pink_15)    # PINK rises above 15 → buy
    
    # Institutional flow context
    df["zj_has_blue"] = COUNT(blue > 0, 5) > 0
    df["zj_has_black"] = COUNT(black < 0, 5) > 0


# ─── Signal definitions ─────────────────────────────────────────

ALERT_SIGNALS = {
    "ZJ_OB_EXIT": {
        "column": "ZJ_OB_EXIT",
        "direction": "SELL",
        "emoji": "🔻",
        "description": "ZJ Overbought Exit — PINK J-line crosses below 85",
    },
    "ZJ_OS_ENTRY": {
        "column": "ZJ_OS_ENTRY",
        "direction": "BUY",
        "emoji": "🔺",
        "description": "ZJ Oversold Entry — PINK J-line crosses above 15",
    },
}

ACTIVE_ALERTS = ["ZJ_OB_EXIT", "ZJ_OS_ENTRY"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check last COMPLETED bar for signals (iloc[-2], not the in-progress bar)."""
    signals = []
    if len(df) < 2:
        return signals
    last = df.iloc[-2]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            pink_val = round(last.get("zj_pink", 0), 1)
            has_blue = last.get("zj_has_blue", False)
            has_black = last.get("zj_has_black", False)
            flow = "Buying" if last.get("zj_red", False) else "Selling" if last.get("zj_green", False) else "Neutral"
            
            inst_flow = []
            if has_blue:
                inst_flow.append("🔵 Institutional buying (recent)")
            if has_black:
                inst_flow.append("⬛ Institutional selling (recent)")
            
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
                "hma_state": f"PINK: {pink_val} | Flow: {flow} | {' '.join(inst_flow) if inst_flow else 'No inst. activity'}",
                "trend": "BULL" if last.get("zj_red", False) else "BEAR",
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

