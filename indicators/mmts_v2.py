"""
MMTS V2 — Multi-Timeframe ZIG-ZAG Reversal System

Uses ZIG-ZAG at 4 sensitivity levels (6%, 22%, 51%, 72%) to detect
swing turning points. Also incorporates trough detection.

Signals:
  - 买点 (BUY): Trough detected OR ZIG turning up
  - 卖点 (SELL): ZIG turning down
  - Signal strength = count of ZIG levels agreeing (1-4)
"""
import numpy as np
import pandas as pd
from core.ta import HHV, LLV, EMA, SMA, REF, ZIG, TROUGHBARS


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all MMTS signals."""
    out = df.copy()
    
    close = out["close"]
    high = out["high"]
    low = out["low"]
    
    # ═══════════════════════════════════════════════════════════════
    # Context Variables
    # ═══════════════════════════════════════════════════════════════
    var3 = LLV(high, 240)
    out["mmts_var4"] = 100 * (close - var3) / var3.replace(0, 0.000001)
    
    # Position within 528-period range
    ll_528 = LLV(low, 528)
    hh_528 = HHV(high, 528)
    den_528 = (hh_528 - ll_528).replace(0, 0.000001)
    out["mmts_range_pos"] = (close - ll_528) / den_528 * 100
    
    # Momentum: EMA(2) - EMA(150)
    out["mmts_momentum"] = EMA(close, 2) - EMA(close, 150)
    
    # ═══════════════════════════════════════════════════════════════
    # Trough Detection (买1)
    # ═══════════════════════════════════════════════════════════════
    troughbars_16 = TROUGHBARS(close, high, low, 3, 16, 1)
    var10 = ((troughbars_16 == 0) & (high > low + 0.04)).astype(int) * 4
    out["TROUGH_BUY"] = var10 > 0
    
    # ═══════════════════════════════════════════════════════════════
    # Multi-Sensitivity ZIG-ZAG Reversals
    # ═══════════════════════════════════════════════════════════════
    zig_levels = [6, 22, 51, 72]
    
    buy_count = pd.Series(0, index=out.index, dtype=int)
    sell_count = pd.Series(0, index=out.index, dtype=int)
    
    for pct in zig_levels:
        zig_vals = ZIG(close, high, low, 3, pct)
        zig_1 = REF(zig_vals, 1).fillna(0)
        zig_2 = REF(zig_vals, 2).fillna(0)
        zig_3 = REF(zig_vals, 3).fillna(0)
        
        # Turning up (buy): current > prev, prev <= prev2, prev2 <= prev3
        turning_up = (zig_vals > zig_1) & (zig_1 <= zig_2) & (zig_2 <= zig_3)
        # Turning down (sell): current < prev, prev >= prev2, prev2 >= prev3
        turning_down = (zig_vals < zig_1) & (zig_1 >= zig_2) & (zig_2 >= zig_3)
        
        buy_count += turning_up.astype(int)
        sell_count += turning_down.astype(int)
        
        out[f"mmts_zig_{pct}_up"] = turning_up
        out[f"mmts_zig_{pct}_down"] = turning_down
    
    # ═══════════════════════════════════════════════════════════════
    # Final Signals
    # ═══════════════════════════════════════════════════════════════
    buy1 = var10 > 0  # Trough detection
    buy2 = buy_count > 0  # ZIG turning up
    sell1 = sell_count > 0  # ZIG turning down
    
    out["MMTS_BUY"] = buy1 | buy2
    out["MMTS_SELL"] = sell1
    
    # Signal strength
    out["mmts_buy_strength"] = buy_count + (var10 > 0).astype(int)
    out["mmts_sell_strength"] = sell_count
    
    # Trough + ZIG both triggering = double confirmation
    out["MMTS_BUY_DOUBLE"] = buy1 & buy2
    
    return out


# ═══════════════════════════════════════════════════════════════════
# Signal Definitions
# ═══════════════════════════════════════════════════════════════════

ALERT_SIGNALS = {
    "MMTS_BUY": {
        "column": "MMTS_BUY",
        "direction": "BUY",
        "emoji": "😊",
        "description": "MMTS Buy Point - structure turning up",
    },
    "MMTS_SELL": {
        "column": "MMTS_SELL",
        "direction": "SELL",
        "emoji": "😢",
        "description": "MMTS Sell Point - structure turning down",
    },
    "MMTS_BUY_DOUBLE": {
        "column": "MMTS_BUY_DOUBLE",
        "direction": "BUY",
        "emoji": "😊😊",
        "description": "MMTS Double Buy - trough + ZIG both confirm",
    },
}

ACTIVE_ALERTS = ["MMTS_BUY", "MMTS_SELL", "MMTS_BUY_DOUBLE"]


def get_signals(df: pd.DataFrame) -> list[dict]:
    """Check last COMPLETED bar for signals (iloc[-2], not the in-progress bar)."""
    signals = []
    if len(df) < 2:
        return signals
    last = df.iloc[-2]
    
    for name in ACTIVE_ALERTS:
        sig = ALERT_SIGNALS[name]
        if last.get(sig["column"], False):
            buy_str = int(last.get("mmts_buy_strength", 0))
            sell_str = int(last.get("mmts_sell_strength", 0))
            strength = buy_str if sig["direction"] == "BUY" else sell_str
            
            # Calculate points based on strength
            if name == "MMTS_BUY_DOUBLE":
                points = 4  # Trough + ZIG
            elif strength >= 3:
                points = 3  # Multi-ZIG agreement
            elif strength >= 2:
                points = 2
            else:
                points = 1
            
            # Which ZIG levels triggered
            zig_details = []
            for pct in [6, 22, 51, 72]:
                col = f"mmts_zig_{pct}_{'up' if sig['direction'] == 'BUY' else 'down'}"
                if last.get(col, False):
                    zig_details.append(f"ZIG({pct}%)")
            if last.get("TROUGH_BUY", False):
                zig_details.append("Trough")
            
            context = {
                "signal": name,
                "direction": sig["direction"],
                "emoji": sig["emoji"],
                "description": sig["description"],
                "close": last["close"],
                "volume": last["volume"],
                "strength": strength,
                "points": points,
                "zig_details": zig_details,
                "momentum": round(last.get("mmts_momentum", 0), 4),
                "range_pos": round(last.get("mmts_range_pos", 0), 1),
            }
            signals.append(context)
    
    return signals
