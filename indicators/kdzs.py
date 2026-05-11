"""
KDZS Indicator — KDJ Sub-Chart with MACD-Colored D Line.

KDJ(P1, P2, P3) with zone-based signal confirmation and BACKSET cancellation.
D line colored by MACD(5, 13, 6) momentum direction.

Alert signals:
  - KDZS_BUY:  Confirmed golden cross in oversold zone (not cancelled)
  - KDZS_SELL: Confirmed death cross in overbought zone (not cancelled)
"""
import numpy as np
import pandas as pd
from core.ta import HHV, LLV, EMA, SMA, REF, CROSS, BARSLAST, BACKSET


# KDJ parameters (standard 9/3/3 — adjust if Daniel uses different)
PARAM_P1 = 9
PARAM_P2 = 3
PARAM_P3 = 3


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all KDZS signals on OHLCV DataFrame."""
    out = df.copy()
    
    close = out["close"]
    high = out["high"]
    low = out["low"]
    
    # ─── KDJ ───
    hh = HHV(high, PARAM_P1)
    ll = LLV(low, PARAM_P1)
    den = (hh - ll).replace(0, 0.000001)
    rsv = (close - ll) / den * 100
    
    k = SMA(rsv, PARAM_P2, 1)
    d = SMA(k, PARAM_P3, 1)
    j = 3 * k - 2 * d
    
    out["kdzs_k"] = k
    out["kdzs_d"] = d
    out["kdzs_j"] = j
    
    # ─── MACD momentum for D-line coloring ───
    diff = EMA(close, 5) - EMA(close, 13)
    dea = EMA(diff, 6)
    
    diff_prev = REF(diff).fillna(0)
    macd_bullish = (diff >= dea) | ((diff < dea) & (diff_prev >= dea))
    macd_bearish = (diff < dea) | ((diff >= dea) & (diff_prev < dea))
    
    out["kdzs_macd_bull"] = macd_bullish
    out["kdzs_diff"] = diff
    out["kdzs_dea"] = dea
    
    # ─── Basic crosses ───
    out["kdzs_golden_cross"] = CROSS(k, d)
    out["kdzs_death_cross"] = CROSS(d, k)
    
    # ─── Zone-based signals with BACKSET cancellation ───
    k_prev = REF(k).fillna(50)
    d_prev = REF(d).fillna(50)
    j_prev = REF(j).fillna(50)
    
    up_zone = (k_prev >= 75) & (d_prev >= 75) & (j_prev >= 75)
    dn_zone = (k_prev <= 25) & (d_prev <= 25) & (j_prev <= 25)
    
    s1 = CROSS(d, k) & up_zone    # death cross in overbought
    g1 = CROSS(k, d) & up_zone    # golden cross in overbought (invalidates sell)
    j1 = CROSS(k, d) & dn_zone    # golden cross in oversold
    d1 = CROSS(d, k) & dn_zone    # death cross in oversold (invalidates buy)
    
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
    
    out["KDZS_SELL"] = s1 & ~kill_s
    out["KDZS_BUY"] = j1 & ~kill_j
    
    # Context
    out["kdzs_up_zone"] = up_zone
    out["kdzs_dn_zone"] = dn_zone
    
    return out


# ─── Signal definitions ─────────────────────────────────────────

ALERT_SIGNALS = {
    "KDZS_BUY": {
        "column": "KDZS_BUY",
        "direction": "BUY",
        "emoji": "📗",
        "description": "KDJ Golden Cross in oversold zone (confirmed, not cancelled)",
    },
    "KDZS_SELL": {
        "column": "KDZS_SELL",
        "direction": "SELL",
        "emoji": "📕",
        "description": "KDJ Death Cross in overbought zone (confirmed, not cancelled)",
    },
}

ACTIVE_ALERTS = ["KDZS_BUY", "KDZS_SELL"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check last COMPLETED bar for signals (iloc[-2], not the in-progress bar)."""
    signals = []
    if len(df) < 2:
        return signals
    last = df.iloc[-2]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            k_val = round(last.get("kdzs_k", 0), 1)
            d_val = round(last.get("kdzs_d", 0), 1)
            j_val = round(last.get("kdzs_j", 0), 1)
            macd_dir = "Bullish" if last.get("kdzs_macd_bull", False) else "Bearish"
            
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
                "hma_state": f"K:{k_val} D:{d_val} J:{j_val} | MACD: {macd_dir}",
                "trend": "BULL" if last.get("kdzs_macd_bull", False) else "BEAR",
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

