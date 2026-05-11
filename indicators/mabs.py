"""
MABS Indicator — MACD Divergence Engine.

Multi-wave MACD divergence detection with strength filters.

Subsystems:
  - Bottom divergence: price lower low + DIFF higher low (below zero)
  - Top divergence: price higher high + DIFF lower high (above zero)
  - Direct and skip (3-wave) divergence patterns
  - Confirmation/cancellation logic
  - Strength: trend (MA30) + volume + momentum filters

Alert signals:
  - MABS_BOTTOM_STRONG: Strong bottom divergence (trend up + vol + momentum)
  - MABS_BOTTOM_WEAK: Bottom divergence without full strength
  - MABS_SELL_STRONG: Strong top divergence (trend down + vol down + momentum)
  - MABS_SELL_WEAK: Top divergence without full strength
  - MABS_GOLDEN_CROSS: DIFF crosses above DEA
  - MABS_DEATH_CROSS: DEA crosses above DIFF
"""
import numpy as np
import pandas as pd
from core.ta import HHV, LLV, MA, EMA, SMA, REF, CROSS, BARSLAST, BACKSET, COUNT


# Default MACD parameters (adjust if Daniel uses different ones)
PARAM_S = 12   # Short EMA
PARAM_P = 26   # Long EMA
PARAM_M = 9    # Signal EMA


def _dynamic_ref(series: pd.Series, shift_series: pd.Series) -> pd.Series:
    """Dynamic REF: for each bar i, look back shift_series[i] bars."""
    result = np.full(len(series), np.nan)
    vals = series.values
    shifts = shift_series.values
    for i in range(len(series)):
        s = shifts[i]
        if not np.isnan(s) and s >= 0:
            idx = i - int(s)
            if 0 <= idx < len(series):
                result[i] = vals[idx]
    return pd.Series(result, index=series.index)


def _dynamic_llv(series: pd.Series, window_series: pd.Series) -> pd.Series:
    """Dynamic LLV: for each bar, compute min over last window[i] bars."""
    result = np.full(len(series), np.nan)
    vals = series.values
    wins = window_series.values
    for i in range(len(series)):
        w = wins[i]
        if not np.isnan(w) and w >= 1:
            w = int(min(w, i + 1))
            start = max(0, i - w + 1)
            result[i] = np.nanmin(vals[start:i + 1])
    return pd.Series(result, index=series.index)


def _dynamic_hhv(series: pd.Series, window_series: pd.Series) -> pd.Series:
    """Dynamic HHV: for each bar, compute max over last window[i] bars."""
    result = np.full(len(series), np.nan)
    vals = series.values
    wins = window_series.values
    for i in range(len(series)):
        w = wins[i]
        if not np.isnan(w) and w >= 1:
            w = int(min(w, i + 1))
            start = max(0, i - w + 1)
            result[i] = np.nanmax(vals[start:i + 1])
    return pd.Series(result, index=series.index)


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all MABS signals on OHLCV DataFrame."""
    out = df.copy()
    
    close = out["close"]
    vol = out["volume"]
    
    # ─── Core MACD ───
    diff = EMA(close, PARAM_S) - EMA(close, PARAM_P)
    dea = EMA(diff, PARAM_M)
    macd = 2 * (diff - dea)
    
    out["mabs_diff"] = diff
    out["mabs_dea"] = dea
    out["mabs_macd"] = macd
    
    # ─── Wave tracking ───
    macd_prev = REF(macd).fillna(0)
    
    # N1: bars since MACD crossed below 0 (start of negative wave)
    cross_below = (macd_prev >= 0) & (macd < 0)
    n1 = BARSLAST(cross_below)
    
    # MM1: bars since MACD crossed above 0 (start of positive wave)
    cross_above = (macd_prev <= 0) & (macd > 0)
    mm1 = BARSLAST(cross_above)
    
    # ─── Bottom divergence tracking ───
    # CC1: lowest close during current negative wave
    cc1 = _dynamic_llv(close, n1 + 1)
    # CC2: CC1 from previous negative wave
    cc2 = _dynamic_ref(cc1, mm1 + 1)
    # CC3: CC1 from two waves back
    cc3 = _dynamic_ref(cc2, mm1 + 1)
    
    # DIFL1: lowest DIFF during current negative wave
    difl1 = _dynamic_llv(diff, n1 + 1)
    difl2 = _dynamic_ref(difl1, mm1 + 1)
    difl3 = _dynamic_ref(difl2, mm1 + 1)
    
    # ─── Top divergence tracking ───
    # CH1: highest close during current positive wave
    ch1 = _dynamic_hhv(close, mm1 + 1)
    ch2 = _dynamic_ref(ch1, n1 + 1)
    ch3 = _dynamic_ref(ch2, n1 + 1)
    
    # DIFH1: highest DIFF during current positive wave
    difh1 = _dynamic_hhv(diff, mm1 + 1)
    difh2 = _dynamic_ref(difh1, n1 + 1)
    difh3 = _dynamic_ref(difh2, n1 + 1)
    
    # ─── Bottom divergence signals ───
    diff_prev = REF(diff).fillna(0)
    
    # AAA: Direct bottom divergence
    aaa = ((cc1 < cc2) & (difl1 > difl2) & 
           (macd_prev < 0) & (diff < 0))
    
    # BBB: Skip (3-wave) bottom divergence
    bbb = ((cc1 < cc3) & (difl1 < difl2) & (difl1 > difl3) & 
           (macd_prev < 0) & (diff < 0))
    
    ccc = (aaa | bbb) & (diff < 0)
    
    # LLL: First bar of CCC
    ccc_prev = REF(ccc.astype(float)).fillna(0).astype(bool)
    lll = ~ccc_prev & ccc
    
    # JJJ: Confirmed divergence (DIFF magnitude increasing)
    jjj = ccc_prev & (diff_prev.abs() >= diff.abs() * 1.01)
    
    # DXDX: First bar of JJJ
    jjj_prev = REF(jjj.astype(float)).fillna(0).astype(bool)
    dxdx = ~jjj_prev & jjj
    
    # Extended patterns (simplified)
    # XXX: Cancellation
    aaa_prev = REF(aaa.astype(float)).fillna(0).astype(bool)
    bbb_prev = REF(bbb.astype(float)).fillna(0).astype(bool)
    xxx = ((aaa_prev & (difl1 <= difl2) & (diff < dea)) |
           (bbb_prev & (difl1 <= difl3) & (diff < dea)))
    
    # DJGXX: Extended divergence pattern
    jjj_at_mm1 = _dynamic_ref(jjj.astype(float), mm1 + 1).fillna(0).astype(bool)
    jjj_at_mm1m = _dynamic_ref(jjj.astype(float), mm1).fillna(0).astype(bool)
    lll_prev = REF(lll.astype(float)).fillna(0).astype(bool)
    jjj_count24 = COUNT(jjj, 24)
    
    djgxx = (((close < cc2) | (close < cc1)) & 
             (jjj_at_mm1 | jjj_at_mm1m) & 
             ~lll_prev & (jjj_count24 >= 1))
    
    djgxx_prev = REF(djgxx.astype(float)).fillna(0).astype(bool)
    djgxx_count2 = COUNT(djgxx_prev, 2)
    djxx = ~(djgxx_count2 >= 1) & djgxx
    
    dxx = (xxx | djxx) & ~ccc
    
    # ─── Top divergence signals ───
    # ZJDBL: Direct top divergence
    zjdbl = ((ch1 > ch2) & (difh1 < difh2) & 
             (macd_prev > 0) & (diff > 0))
    
    # GXDBL: Skip top divergence
    gxdbl = ((ch1 > ch3) & (difh1 > difh2) & (difh1 < difh3) & 
             (macd_prev > 0) & (diff > 0))
    
    dbbl = (zjdbl | gxdbl) & (diff > 0)
    
    # DBL: First bar
    dbbl_prev = REF(dbbl.astype(float)).fillna(0).astype(bool)
    dbl = ~dbbl_prev & dbbl & (diff > dea)
    
    # DBJG: Confirmed top divergence
    dbjg = dbbl_prev & (diff_prev >= diff * 1.01)
    
    # DBJGXC: First bar of DBJG
    dbjg_prev_not = REF((~dbjg).astype(float)).fillna(1).astype(bool)
    dbjgxc = dbjg_prev_not & dbjg
    
    # Extended top patterns
    dbjg_at_n1 = _dynamic_ref(dbjg.astype(float), n1 + 1).fillna(0).astype(bool)
    dbjg_at_n1m = _dynamic_ref(dbjg.astype(float), n1).fillna(0).astype(bool)
    dbl_prev = REF(dbl.astype(float)).fillna(0).astype(bool)
    dbjg_count23 = COUNT(dbjg, 23)
    
    zzzzz = (((close > ch2) | (close > ch1)) &
             (dbjg_at_n1 | dbjg_at_n1m) &
             ~dbl_prev & (dbjg_count23 >= 1))
    
    zzzzz_prev = REF(zzzzz.astype(float)).fillna(0).astype(bool)
    zzzzz_count2 = COUNT(zzzzz_prev, 2)
    yyyyy = ~(zzzzz_count2 >= 1) & zzzzz
    
    # DBLXS: Top divergence cancellation
    zjdbl_prev = REF(zjdbl.astype(float)).fillna(0).astype(bool)
    gxdbl_prev = REF(gxdbl.astype(float)).fillna(0).astype(bool)
    dblxs = ((zjdbl_prev & (difh1 >= difh2) & (diff > dea)) |
             (gxdbl_prev & (difh1 >= difh3) & (diff > dea)))
    
    wwwww = (dblxs | yyyyy) & ~dbbl
    
    # ─── Strength filters ───
    ma30 = MA(close, 30)
    trendup = close > ma30
    trenddown = close < ma30
    vol5 = MA(vol, 5)
    volup = vol > vol5
    voldown = vol < vol5
    
    strongbottom = (diff - diff_prev) > diff_prev.abs() * 0.03
    strongtop = (diff_prev - diff) > diff_prev.abs() * 0.03
    
    # ─── Final signals ───
    out["MABS_BOTTOM_STRONG"] = dxdx & trendup & volup & strongbottom
    out["MABS_BOTTOM_WEAK"] = dxdx & ~(trendup & volup & strongbottom)
    out["MABS_SELL_STRONG"] = dbjgxc & trenddown & voldown & strongtop
    out["MABS_SELL_WEAK"] = dbjgxc & ~(trenddown & voldown & strongtop)
    out["MABS_GOLDEN_CROSS"] = CROSS(diff, dea)
    out["MABS_DEATH_CROSS"] = CROSS(dea, diff)
    
    # Context
    out["mabs_trendup"] = trendup
    out["mabs_volup"] = volup
    out["mabs_bottom_div"] = ccc
    out["mabs_top_div"] = dbbl
    
    return out


# ─── Signal definitions ─────────────────────────────────────────

ALERT_SIGNALS = {
    "MABS_BOTTOM_STRONG": {
        "column": "MABS_BOTTOM_STRONG",
        "direction": "BUY",
        "emoji": "🟣",
        "description": "MABS Strong Bottom — MACD divergence + uptrend + volume + momentum",
    },
    "MABS_BOTTOM_WEAK": {
        "column": "MABS_BOTTOM_WEAK",
        "direction": "BUY",
        "emoji": "📈",
        "description": "MABS Bottom Divergence — MACD bullish divergence confirmed",
    },
    "MABS_SELL_STRONG": {
        "column": "MABS_SELL_STRONG",
        "direction": "SELL",
        "emoji": "🟣",
        "description": "MABS Strong Sell — MACD divergence + downtrend + vol down + momentum",
    },
    "MABS_SELL_WEAK": {
        "column": "MABS_SELL_WEAK",
        "direction": "SELL",
        "emoji": "📉",
        "description": "MABS Sell Divergence — MACD bearish divergence confirmed",
    },
    "MABS_GOLDEN_CROSS": {
        "column": "MABS_GOLDEN_CROSS",
        "direction": "BUY",
        "emoji": "✨",
        "description": "MABS Golden Cross — DIFF crosses above DEA",
    },
    "MABS_DEATH_CROSS": {
        "column": "MABS_DEATH_CROSS",
        "direction": "SELL",
        "emoji": "💀",
        "description": "MABS Death Cross — DEA crosses above DIFF",
    },
}

# Active by default: strong signals
ACTIVE_ALERTS = ["MABS_BOTTOM_STRONG", "MABS_SELL_STRONG"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check last COMPLETED bar for signals (iloc[-2], not the in-progress bar)."""
    signals = []
    if len(df) < 2:
        return signals
    last = df.iloc[-2]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            diff_val = round(last.get("mabs_diff", 0), 4)
            dea_val = round(last.get("mabs_dea", 0), 4)
            macd_val = round(last.get("mabs_macd", 0), 4)
            
            details = []
            if last.get("mabs_bottom_div", False):
                details.append("Bottom divergence active")
            if last.get("mabs_top_div", False):
                details.append("Top divergence active")
            if last.get("mabs_trendup", False):
                details.append("Uptrend (>MA30)")
            else:
                details.append("Downtrend (<MA30)")
            if last.get("mabs_volup", False):
                details.append("Volume above avg")
            
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
                "hma_state": f"DIFF: {diff_val} | DEA: {dea_val} | MACD: {macd_val}",
                "trend": "BULL" if last.get("mabs_trendup", False) else "BEAR",
                "stop_long": None,
                "stop_short": None,
                "details": ", ".join(details),
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

