"""
RSCDJC Indicator — RSI Divergence + Big Money Flow + Absolute Top Detection.

Three subsystems:
  1. RSI(14) divergence with volume spike highlighting
  2. Big Money Flow (主力资金) — institutional money flow via multi-period HHV/LLV
  3. Absolute Top Signal — multi-factor peak detection with SAR/RSI/money confirmation

Alert signals:
  - RSCDJC_BOT_DIV: Bullish RSI divergence (bottom)
  - RSCDJC_TOP_DIV: Bearish RSI divergence (top)
  - RSCDJC_TOP_PULSE: Initial top warning (减)
  - RSCDJC_TOP_FINAL: Confirmed absolute top (★减)
"""
import numpy as np
import pandas as pd
from core.ta import (HHV, LLV, MA, EMA, SMA, REF, CROSS,
                     BARSLAST, BACKSET, FILTER, SAR, STD)


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all RSCDJC signals on OHLCV DataFrame."""
    out = df.copy()
    
    close = out["close"]
    high = out["high"]
    low = out["low"]
    vol = out["volume"]
    
    _compute_rsi_divergence(out, close, high, low, vol)
    _compute_big_money(out, close, high, low)
    _compute_absolute_top(out, close, high, low)
    
    return out


def _compute_rsi_divergence(df, close, high, low, vol):
    """Part 1: RSI Divergence."""
    # RSI(14)
    lc = REF(close).fillna(close)
    diff = close - lc
    temp1 = diff.clip(lower=0)
    temp2 = diff.abs()
    rsi = SMA(temp1, 14, 1) / SMA(temp2, 14, 1).replace(0, 0.000001) * 100
    df["rscdjc_rsi"] = rsi
    
    # Volume spike
    vol_ma5 = MA(vol, 5).replace(0, 0.000001)
    volr = vol / vol_ma5
    is_vol_spike = volr > 1.3
    df["rscdjc_vol_spike"] = is_vol_spike
    
    # RSI zones
    df["rscdjc_rsi_ob"] = rsi >= 70
    df["rscdjc_rsi_os"] = rsi <= 30
    
    N = 3
    
    # --- Top divergence (bearish) ---
    rsi_n = REF(rsi, N).fillna(0)
    a1 = rsi_n == HHV(rsi, 2 * N + 1)
    b1 = BACKSET(a1, N + 1)
    c1 = FILTER(b1.astype(bool), N)
    period_top = BARSLAST(REF(c1.astype(float)).fillna(0).astype(bool))
    
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
    
    # --- Bottom divergence (bullish) ---
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
    
    df["RSCDJC_TOP_DIV"] = FILTER(top_div, 5)
    df["RSCDJC_BOT_DIV"] = FILTER(bot_div, 5)
    
    # Volume-confirmed divergence
    df["rscdjc_top_div_vol"] = df["RSCDJC_TOP_DIV"] & is_vol_spike
    df["rscdjc_bot_div_vol"] = df["RSCDJC_BOT_DIV"] & is_vol_spike


def _compute_big_money(df, close, high, low):
    """Part 2: Big Money Flow (主力资金)."""
    # Multi-period HHV/LLV
    var1 = EMA(HHV(high, 500), 21)
    var2 = EMA(HHV(high, 250), 21)
    var3 = EMA(HHV(high, 90), 21)
    var4 = EMA(LLV(low, 500), 21)
    var5 = EMA(LLV(low, 250), 21)
    var6 = EMA(LLV(low, 90), 21)
    
    var7 = EMA((var4*0.96 + var5*0.96 + var6*0.96 + var1*0.558 + var2*0.558 + var3*0.558) / 6, 21)
    var8 = EMA((var4*1.25 + var5*1.23 + var6*1.2 + var1*0.55 + var2*0.55 + var3*0.65) / 6, 21)
    var9 = EMA((var4*1.3 + var5*1.3 + var6*1.3 + var1*0.68 + var2*0.68 + var3*0.68) / 6, 21)
    
    vara = EMA((var7*3 + var8*2 + var9) / 6 * 1.738, 21)
    
    varb = REF(low).fillna(low)
    varc_num = SMA((low - varb).abs(), 3, 1)
    varc_den = SMA((low - varb).clip(lower=0), 3, 1).replace(0, 0.000001)
    varc = varc_num / varc_den * 100
    
    # Money flow calculation
    vard_input = pd.Series(np.where(close * 1.35 <= vara, varc * 10, varc / 10), index=df.index)
    vard = EMA(vard_input, 3)
    
    vare = LLV(low, 30)
    varf = HHV(vard, 30)
    
    b_input = pd.Series(np.where(low <= vare, (vard + varf * 2) / 2, 0), index=df.index)
    big_money = EMA(b_input, 3) / 618
    
    df["rscdjc_big_money"] = big_money
    
    # SAR trend
    sarv = SAR(high, low, close, n=4, step=2, maxp=20)
    df["rscdjc_sar"] = sarv
    df["rscdjc_sar_up"] = close > sarv
    df["rscdjc_sar_dn"] = close < sarv


def _compute_absolute_top(df, close, high, low):
    """Part 3: Absolute Top Detection."""
    rsi = df["rscdjc_rsi"]
    big_money = df["rscdjc_big_money"]
    sarv = df["rscdjc_sar"]
    
    BLEN = 3
    HWPER = 55
    EPS = 0.001
    PBIAS_TH1 = 10
    PBIAS_TH2 = 12
    BAMP_TH = 1.6
    BBIAS_TH = 25
    
    # Price bias from MA(181)
    base181 = MA(close, 181)
    p_bias = 100 * (close - base181) / base181.replace(0, 0.000001)
    
    # Big money stats
    b_base = MA(big_money, BLEN)
    safe = b_base.replace(0, 0.0000001)
    b_ratio = big_money / safe
    b_bias = 100 * (big_money - safe) / safe
    
    # Volatility-adjusted threshold
    ret = close / REF(close).fillna(close) - 1
    rv = STD(ret, 34).fillna(0)
    epsa = EPS + rv
    
    # New price high + new big money high
    newph = close >= HHV(close, HWPER) * (1 - epsa)
    newbh = big_money >= HHV(big_money, HWPER) * (1 - epsa)
    
    # Top conditions
    top_raw1 = (newph & newbh & (rsi > 70) & (p_bias >= PBIAS_TH1) &
                ((b_ratio >= BAMP_TH) | (b_bias >= BBIAS_TH)))
    top_raw2 = newph & (rsi > 80) & (p_bias >= PBIAS_TH2)
    
    top_pulse = FILTER((top_raw1 | top_raw2).astype(bool), 3)
    df["RSCDJC_TOP_PULSE"] = top_pulse
    
    # Confirmation via failure events
    EPS2 = 0.003
    bma2 = MA(big_money, BLEN)
    b_turn = CROSS(bma2, big_money)
    
    rsi_prev = REF(rsi).fillna(50)
    rsi_down = (rsi_prev > 70) & (rsi <= 70)
    sar_cross_down = CROSS(sarv, close)
    
    fail_evt = sar_cross_down | b_turn | rsi_down
    
    # Check within 8 bars of TOP_PULSE
    bars_since_pulse = BARSLAST(top_pulse.astype(bool))
    
    # NO_BREAK: high hasn't exceeded the high at pulse time
    top_final = pd.Series(False, index=df.index)
    for i in range(len(df)):
        if fail_evt.iloc[i]:
            bsp = bars_since_pulse.iloc[i]
            if not np.isnan(bsp) and 0 < bsp <= 8:
                pulse_idx = i - int(bsp)
                if pulse_idx >= 0:
                    # Check no breakout since pulse
                    window_high = high.iloc[pulse_idx:i + 1].max()
                    pulse_high = high.iloc[pulse_idx]
                    if window_high <= pulse_high * (1 + EPS2):
                        # Mark from pulse to current as top_final
                        top_final.iloc[pulse_idx] = True
    
    df["RSCDJC_TOP_FINAL"] = top_final
    
    # Context
    df["rscdjc_p_bias"] = p_bias
    df["rscdjc_b_ratio"] = b_ratio


# ─── Signal definitions ─────────────────────────────────────────

ALERT_SIGNALS = {
    "RSCDJC_BOT_DIV": {
        "column": "RSCDJC_BOT_DIV",
        "direction": "BUY",
        "emoji": "🟢",
        "description": "RSI Bullish Divergence — price lower low, RSI higher low",
    },
    "RSCDJC_TOP_DIV": {
        "column": "RSCDJC_TOP_DIV",
        "direction": "SELL",
        "emoji": "🔴",
        "description": "RSI Bearish Divergence — price higher high, RSI lower high",
    },
    "RSCDJC_TOP_PULSE": {
        "column": "RSCDJC_TOP_PULSE",
        "direction": "SELL",
        "emoji": "⚠️",
        "description": "Top Warning (减) — price+money at highs, RSI overbought, high bias",
    },
    "RSCDJC_TOP_FINAL": {
        "column": "RSCDJC_TOP_FINAL",
        "direction": "SELL",
        "emoji": "🚨",
        "description": "Confirmed Top (★减) — top warning + failure event confirmed",
    },
}

ACTIVE_ALERTS = ["RSCDJC_BOT_DIV", "RSCDJC_TOP_DIV", "RSCDJC_TOP_PULSE", "RSCDJC_TOP_FINAL"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check last COMPLETED bar for signals (iloc[-2], not the in-progress bar)."""
    signals = []
    if len(df) < 2:
        return signals
    last = df.iloc[-2]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            rsi_val = round(last.get("rscdjc_rsi", 0), 1)
            p_bias = round(last.get("rscdjc_p_bias", 0), 1)
            bm = round(last.get("rscdjc_big_money", 0), 4)
            
            details = []
            if last.get("rscdjc_vol_spike", False):
                details.append("Volume spike")
            if last.get("rscdjc_sar_up", False):
                details.append("SAR bullish")
            else:
                details.append("SAR bearish")
            
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
                "hma_state": f"RSI: {rsi_val} | Bias: {p_bias}% | Money: {bm}",
                "trend": "BULL" if last.get("rscdjc_sar_up", False) else "BEAR",
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

