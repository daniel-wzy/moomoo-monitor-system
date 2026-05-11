"""
MMTS Indicator — Multi-Timeframe ZIG-ZAG Reversal System.

Uses ZIG-ZAG at 4 sensitivity levels (6%, 22%, 51%, 72%) to detect
swing turning points. Also incorporates trough detection via ZIG(3,16).

Alert signals:
  - MMTS_BUY:  Buy point — trough detected OR any ZIG turning up
  - MMTS_SELL: Sell point — any ZIG turning down
"""
import numpy as np
import pandas as pd
from core.ta import (HHV, LLV, EMA, SMA, REF, ZIG, TROUGHBARS)


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all MMTS signals on OHLCV DataFrame.
    
    Expects columns: open, close, high, low, volume
    Returns DataFrame with all computed columns appended.
    """
    out = df.copy()
    
    close = out["close"]
    high = out["high"]
    low = out["low"]
    
    # --- Context variables (for enriching alerts) ---
    # VAR3: 240-period highest high
    var3 = LLV(high, 240)
    # VAR4: % distance from 240-period high
    out["mmts_var4"] = 100 * (close - var3) / var3.replace(0, 0.000001)
    
    # VAR5: Position within 528-period range (HL)
    ll_528 = LLV(low, 528)
    hh_528 = HHV(high, 528)
    den_528 = (hh_528 - ll_528).replace(0, 0.000001)
    out["mmts_range_pos"] = (close - ll_528) / den_528 * 100
    
    # VAR6: Position within 530-period range (close)
    ll_530 = LLV(close, 530)
    hh_530 = HHV(close, 530)
    den_530 = (hh_530 - ll_530).replace(0, 0.000001)
    out["mmts_close_pos"] = (close - ll_530) / den_530 * 100
    
    # VAR8: Custom RSI-like (34/7)
    close_diff = close - REF(close)
    close_diff_filled = close_diff.fillna(0)
    gain = close_diff_filled.clip(lower=0)
    abs_diff = close_diff_filled.abs()
    var8_num = SMA(gain, 34, 1)
    var8_den = SMA(abs_diff, 7, 1).replace(0, 0.000001)
    out["mmts_rsi_34_7"] = var8_num / var8_den * 100
    
    # VAR9: Custom RSI-like (13/13)
    var9_num = SMA(gain, 13, 1)
    var9_den = SMA(abs_diff, 13, 1).replace(0, 0.000001)
    out["mmts_rsi_13"] = var9_num / var9_den * 100
    
    # VAR19: EMA momentum
    out["mmts_momentum"] = EMA(close, 2) - EMA(close, 150)
    
    # --- VAR10: Trough detection via ZIG(3,16) ---
    troughbars_16 = TROUGHBARS(close, high, low, 3, 16, 1)
    var10 = ((troughbars_16 == 0) & (high > low + 0.04)).astype(int) * 4
    out["mmts_trough_buy"] = var10 > 0
    
    # --- VAR11-VAR18: ZIG reversals at 4 levels ---
    zig_levels = [6, 22, 51, 72]
    
    buy2_sum = pd.Series(0, index=out.index, dtype=int)
    sell1_sum = pd.Series(0, index=out.index, dtype=int)
    
    for pct in zig_levels:
        zig_vals = ZIG(close, high, low, 3, pct)
        zig_1 = REF(zig_vals, 1).fillna(0)
        zig_2 = REF(zig_vals, 2).fillna(0)
        zig_3 = REF(zig_vals, 3).fillna(0)
        
        # Turning up (buy): current > prev, prev <= prev2, prev2 <= prev3
        turning_up = (zig_vals > zig_1) & (zig_1 <= zig_2) & (zig_2 <= zig_3)
        # Turning down (sell): current < prev, prev >= prev2, prev2 >= prev3
        turning_down = (zig_vals < zig_1) & (zig_1 >= zig_2) & (zig_2 >= zig_3)
        
        buy2_sum += turning_up.astype(int)
        sell1_sum += turning_down.astype(int)
        
        out[f"mmts_zig_{pct}_up"] = turning_up
        out[f"mmts_zig_{pct}_down"] = turning_down
    
    # --- Final signals ---
    buy1 = var10 > 0
    buy2 = buy2_sum > 0
    sell1 = sell1_sum > 0
    
    out["MMTS_BUY"] = buy1 | buy2
    out["MMTS_SELL"] = sell1
    
    # Context: how many ZIG levels agree
    out["mmts_buy_strength"] = buy2_sum + (var10 > 0).astype(int)
    out["mmts_sell_strength"] = sell1_sum
    
    return out


# ─── Signal definitions ─────────────────────────────────────────

ALERT_SIGNALS = {
    "MMTS_BUY": {
        "column": "MMTS_BUY",
        "direction": "BUY",
        "emoji": "📈",
        "description": "MMTS Buy — ZIG-ZAG reversal turning up",
    },
    "MMTS_SELL": {
        "column": "MMTS_SELL",
        "direction": "SELL",
        "emoji": "📉",
        "description": "MMTS Sell — ZIG-ZAG reversal turning down",
    },
}

ACTIVE_ALERTS = ["MMTS_BUY", "MMTS_SELL"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check the latest bar for active alert signals."""
    signals = []
    last = df.iloc[-1]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            # Build context
            buy_str = int(last.get("mmts_buy_strength", 0))
            sell_str = int(last.get("mmts_sell_strength", 0))
            strength = buy_str if sig["direction"] == "BUY" else sell_str
            
            # Which ZIG levels triggered
            zig_details = []
            for pct in [6, 22, 51, 72]:
                col = f"mmts_zig_{pct}_{'up' if sig['direction'] == 'BUY' else 'down'}"
                if last.get(col, False):
                    zig_details.append(f"ZIG({pct}%)")
            if sig["direction"] == "BUY" and last.get("mmts_trough_buy", False):
                zig_details.append("Trough(16%)")
            
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
                "strength": strength,
                "zig_details": ", ".join(zig_details) if zig_details else "—",
                "range_position": round(last.get("mmts_range_pos", 0), 1),
                "momentum": round(last.get("mmts_momentum", 0), 4),
                "rsi_13": round(last.get("mmts_rsi_13", 0), 1),
                # Compatibility fields for alert formatter
                "hma_state": f"Strength: {strength}/5 ({', '.join(zig_details)})",
                "trend": "BULL" if last.get("mmts_momentum", 0) > 0 else "BEAR",
                "stop_long": None,
                "stop_short": None,
            }
            signals.append(context)
    
    return signals
