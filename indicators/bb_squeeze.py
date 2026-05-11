"""
Bollinger Band Squeeze Detector — Volatility filter.

Not a standalone signal generator. Used to boost conviction of other signals
when they fire during or right after a volatility squeeze.

A squeeze occurs when Bollinger Bands narrow inside Keltner Channels,
indicating compressed volatility about to explode.
"""
import numpy as np
import pandas as pd
from core.ta import MA, EMA, STD


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add squeeze columns to DataFrame."""
    out = df.copy()
    
    close = out["close"]
    high = out["high"]
    low = out["low"]
    
    # ─── Bollinger Bands (20, 2) ───
    bb_ma = MA(close, 20)
    bb_std = STD(close, 20).fillna(0)
    bb_upper = bb_ma + 2 * bb_std
    bb_lower = bb_ma - 2 * bb_std
    bb_width = (bb_upper - bb_lower) / bb_ma.replace(0, 0.000001) * 100
    
    # ─── Keltner Channels (20, 1.5) ───
    # ATR calculation
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = MA(tr, 20)
    
    kc_ma = EMA(close, 20)
    kc_upper = kc_ma + 1.5 * atr
    kc_lower = kc_ma - 1.5 * atr
    
    # ─── Squeeze detection ───
    # Squeeze ON: BB inside KC
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    
    # Squeeze just released (was squeezed, now not)
    squeeze_prev = squeeze_on.shift(1).fillna(False)
    squeeze_fire = squeeze_prev & ~squeeze_on
    
    # Squeeze duration (how many bars in squeeze)
    squeeze_duration = pd.Series(0, index=out.index)
    count = 0
    for i in range(len(out)):
        if squeeze_on.iloc[i]:
            count += 1
            squeeze_duration.iloc[i] = count
        else:
            count = 0
    
    # Direction of breakout after squeeze
    momentum = close - bb_ma
    
    # BB width percentile (how tight are bands relative to recent history)
    bb_pctl = bb_width.rolling(100, min_periods=20).apply(
        lambda x: (x.iloc[-1] <= x).sum() / len(x) * 100 if len(x) > 0 else 50
    )
    
    out["bb_squeeze_on"] = squeeze_on
    out["bb_squeeze_fire"] = squeeze_fire
    out["bb_squeeze_duration"] = squeeze_duration
    out["bb_width"] = bb_width
    out["bb_width_pctl"] = bb_pctl
    out["bb_momentum"] = momentum
    out["bb_momentum_up"] = momentum > 0
    
    return out


def get_squeeze_context(df: pd.DataFrame) -> dict:
    """Get squeeze state for the latest bar."""
    last = df.iloc[-1]
    
    return {
        "squeeze_on": bool(last.get("bb_squeeze_on", False)),
        "squeeze_fire": bool(last.get("bb_squeeze_fire", False)),
        "squeeze_duration": int(last.get("bb_squeeze_duration", 0)),
        "bb_width_pctl": round(last.get("bb_width_pctl", 50), 1),
        "momentum_up": bool(last.get("bb_momentum_up", False)),
    }


def format_squeeze_context(ctx: dict) -> str:
    """Format squeeze context for alert messages."""
    if ctx["squeeze_fire"]:
        direction = "⬆️ bullish" if ctx["momentum_up"] else "⬇️ bearish"
        return f"💥 SQUEEZE BREAKOUT ({direction}, was squeezed {ctx['squeeze_duration']} bars)"
    elif ctx["squeeze_on"]:
        return f"🔒 In squeeze ({ctx['squeeze_duration']} bars, width {ctx['bb_width_pctl']}th pctl)"
    elif ctx["bb_width_pctl"] < 20:
        return f"⚠️ Low volatility ({ctx['bb_width_pctl']}th pctl — squeeze forming)"
    else:
        return ""
