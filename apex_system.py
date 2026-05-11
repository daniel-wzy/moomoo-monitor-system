"""
APEX 2.0 Trading System — Autonomous Position Entry/eXit

Major upgrades:
- Multi-timeframe analysis (10min, 30min, 1hr, 4hr, daily)
- Trade type classification (short-term vs long-term)
- Requires DIFFERENT indicators for confluence (not just different timeframes)
- Enhanced signal scoring (double smiley + diamond + 抄底 patterns)
- Signal expiration based on trade type
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════════
# Constants & Configuration
# ═══════════════════════════════════════════════════════════════════

class SignalStrength(Enum):
    ULTRA = "ultra"          # 10+ points
    VERY_STRONG = "very_strong"  # 8-9 points
    STRONG = "strong"        # 6-7 points
    MEDIUM = "medium"        # 4-5 points
    WEAK = "weak"            # 2-3 points
    SKIP = "skip"            # 0-1 points


class TradeType(Enum):
    SHORT_TERM = "short_term"   # 10min, 30min, 1hr signals
    LONG_TERM = "long_term"     # 4hr, daily signals
    HIGH_CONVICTION = "high_conviction"  # Both aligned


# Timeframe classifications
SHORT_TERM_TIMEFRAMES = {"10m", "10min", "30m", "30min", "1h", "1hr", "60m"}
LONG_TERM_TIMEFRAMES = {"4h", "4hr", "240m", "daily", "d", "1d"}
ALL_TIMEFRAMES = SHORT_TERM_TIMEFRAMES | LONG_TERM_TIMEFRAMES

# Signal expiration (hours)
SIGNAL_EXPIRY = {
    TradeType.SHORT_TERM: 4,      # Short-term signals expire in 4 hours
    TradeType.LONG_TERM: 24,      # Long-term signals expire in 24 hours
    TradeType.HIGH_CONVICTION: 24,
}

# Trade parameters by type
TRADE_PARAMS = {
    TradeType.SHORT_TERM: {
        "take_profit": 0.05,    # 5%
        "stop_loss": 0.03,      # 3%
        "max_hold_hours": 48,   # 2 days max
        "trail_breakeven_at": 0.03,  # Trail to breakeven when up 3%
        "trail_lock_at": 0.05,       # Lock in profit when up 5%
        "trail_lock_pct": 0.02,      # Lock in 2% profit
    },
    TradeType.LONG_TERM: {
        "take_profit": 0.15,    # 15%
        "stop_loss": 0.05,      # 5%
        "max_hold_hours": None, # No time limit
        "trail_breakeven_at": 0.05,  # Trail to breakeven when up 5%
        "trail_lock_at": 0.10,       # Lock in profit when up 10%
        "trail_lock_pct": 0.05,      # Lock in 5% profit
    },
    TradeType.HIGH_CONVICTION: {
        "take_profit": 0.20,    # 20% - let it run
        "stop_loss": 0.05,      # 5%
        "max_hold_hours": None,
        "trail_breakeven_at": 0.05,
        "trail_lock_at": 0.10,
        "trail_lock_pct": 0.05,
    },
}


def calculate_trailing_stop(entry_price: float, current_price: float, 
                           original_stop: float, trade_type: TradeType) -> Tuple[float, str]:
    """
    Calculate trailing stop loss based on current profit.
    
    Returns:
        (new_stop_price, reason)
    """
    params = TRADE_PARAMS.get(trade_type, TRADE_PARAMS[TradeType.LONG_TERM])
    
    pnl_pct = (current_price - entry_price) / entry_price
    
    # Level 2: Lock in profit (e.g., up 10% → stop at +5%)
    if pnl_pct >= params.get("trail_lock_at", 0.10):
        new_stop = entry_price * (1 + params.get("trail_lock_pct", 0.05))
        if new_stop > original_stop:
            return new_stop, f"Trailing stop: Lock {params['trail_lock_pct']*100:.0f}% profit (was up {pnl_pct*100:.1f}%)"
    
    # Level 1: Move to breakeven (e.g., up 5% → stop at entry)
    elif pnl_pct >= params.get("trail_breakeven_at", 0.05):
        new_stop = entry_price * 1.001  # Slightly above entry to cover fees
        if new_stop > original_stop:
            return new_stop, f"Trailing stop: Breakeven (was up {pnl_pct*100:.1f}%)"
    
    # No change
    return original_stop, ""


# ═══════════════════════════════════════════════════════════════════
# Signal Point Values (Updated for patterns)
# ═══════════════════════════════════════════════════════════════════

SIGNAL_POINTS = {
    # LDZN (Main Chart 1)
    # Correct mapping confirmed by Daniel:
    # 🔴 Red diamond   = STRONG BUY
    # 🔵 Blue diamond  = WEAK SELL
    # 🟢 Green diamond = STRONG SELL
    # 🟡 Yellow diamond = WEAK BUY
    "RED_DIAMOND": 3,           # Strong buy
    "BLUE_DIAMOND": 1,          # Weak SELL (was wrongly mapped as buy!)
    "GREEN_DIAMOND": 3,         # Strong sell
    "YELLOW_DIAMOND": 1,        # Weak buy
    "RTE_OS_REV": 2,            # Oversold reversal
    "RTE_OB_REV": 2,            # Overbought reversal
    
    # MMTS (Main Chart 2)
    "MMTS_BUY": 2,              # Single smiley
    "MMTS_SELL": 2,             # Single sad face
    "MMTS_BUY_DOUBLE": 4,       # Double smiley 😊😊
    "MMTS_SELL_DOUBLE": 4,      # Double sad face
    
    # LDDX (Main Chart 3) — same color mapping as LDZN
    "LDDX_RED_DIAMOND": 3,      # Strong buy
    "LDDX_BLUE_DIAMOND": 1,     # Weak SELL
    "LDDX_GREEN_DIAMOND": 3,    # Strong sell
    "LDDX_YELLOW_DIAMOND": 1,   # Weak buy
    "LDDX_DUIXIAN": 2,          # 兑现 - take profit
    "LDDX_ZHANG": 1,            # [ 涨 ]
    "LDDX_DIE": 1,              # [ 跌 ]
    
    # MABS (Sub Chart 1)
    "MABS_CHADI_STRONG": 4,     # 抄底 magenta - STRONG BOTTOM
    "MABS_CHADI_WEAK": 2,       # 抄底 red
    "MABS_MAICHU_STRONG": 4,    # 卖出 magenta - STRONG TOP
    "MABS_MAICHU_WEAK": 2,      # 卖出 green
    
    # RSCDJC (Sub Chart 2)
    "RSCDJC_BOT_DIV": 2,        # ▲ bottom divergence
    "RSCDJC_TOP_DIV": 2,        # ▼ top divergence
    "RSCDJC_JIAN": 1,           # 减 warning
    "RSCDJC_STAR_JIAN": 4,      # ★减 CONFIRMED TOP - critical sell
    
    # KDZS (Sub Chart 3)
    "KDZS_BLUE_DIAMOND": 1,     # Buy in oversold
    "KDZS_YELLOW_DIAMOND": 1,   # Sell in overbought
    "KDZS_RED_BAR": 1,          # Bullish momentum
    "KDZS_GREEN_BAR": 1,        # Bearish momentum
    
    # ZJ (Volume)
    "ZJ_RED_BAR": 1,            # Bullish volume
    "ZJ_GREEN_BAR": 1,          # Bearish volume
}

# Pattern combinations (bonus points)
PATTERN_COMBOS = {
    # Double smiley + Red diamond = STRONG BUY (5 base + 2 combo bonus)
    ("MMTS_BUY_DOUBLE", "RED_DIAMOND"): 2,
    ("MMTS_BUY_DOUBLE", "LDDX_RED_DIAMOND"): 2,
    
    # Double smiley + Red diamond + 抄底 = ULTRA BUY (+3 more)
    ("MMTS_BUY_DOUBLE", "RED_DIAMOND", "MABS_CHADI_STRONG"): 3,
    ("MMTS_BUY_DOUBLE", "LDDX_RED_DIAMOND", "MABS_CHADI_STRONG"): 3,
    
    # Double sad + Green diamond = STRONG SELL
    ("MMTS_SELL_DOUBLE", "GREEN_DIAMOND"): 2,
    ("MMTS_SELL_DOUBLE", "LDDX_GREEN_DIAMOND"): 2,
    
    # Double sad + Green diamond + 卖出 = ULTRA SELL
    ("MMTS_SELL_DOUBLE", "GREEN_DIAMOND", "MABS_MAICHU_STRONG"): 3,
}

# Context bonuses
CONTEXT_POINTS = {
    "HMA_RED": 1,       # HMA bullish
    "HMA_GREEN": -1,    # HMA bearish
    "ABOVE_180": 1,     # Above 180 MA
    "BELOW_180": -1,    # Below 180 MA
    "D_LINE_RED": 1,    # KDZS D-line bullish
    "D_LINE_GREEN": -1, # KDZS D-line bearish
}

# Volume confirmation thresholds
VOLUME_HIGH_MULTIPLIER = 1.5   # Volume > 1.5x average = high volume
VOLUME_LOW_MULTIPLIER = 0.7    # Volume < 0.7x average = low volume
VOLUME_HIGH_BONUS = 2          # Bonus points for high volume
VOLUME_LOW_PENALTY = -1        # Penalty for low volume

# Unverified indicators — signals detected but not confirmed to match Moomoo visuals
# LDDX excluded until validated
UNVERIFIED_INDICATORS = {"LDDX"}

# MMTS is only reliable on daily timeframe (ZIG-ZAG repaints on intraday)
MMTS_ALLOWED_TIMEFRAMES = {"daily", "d", "1d"}

SIGNAL_LOOKBACK_HOURS = 8  # Only use signals from current trading day (~8h window)


def get_volume_confirmation(current_volume: float, avg_volume: float) -> Tuple[int, str]:
    """
    Check if volume confirms the signal.
    
    Returns:
        (points_adjustment, description)
    """
    if avg_volume <= 0:
        return 0, ""
    
    volume_ratio = current_volume / avg_volume
    
    if volume_ratio >= VOLUME_HIGH_MULTIPLIER:
        return VOLUME_HIGH_BONUS, f"📊 High volume ({volume_ratio:.1f}x avg) +{VOLUME_HIGH_BONUS}pts"
    elif volume_ratio <= VOLUME_LOW_MULTIPLIER:
        return VOLUME_LOW_PENALTY, f"📉 Low volume ({volume_ratio:.1f}x avg) {VOLUME_LOW_PENALTY}pts"
    else:
        return 0, f"📊 Normal volume ({volume_ratio:.1f}x avg)"

# Relative strength thresholds
RS_OUTPERFORM_THRESHOLD = 1.0
RS_UNDERPERFORM_THRESHOLD = -1.0
MARKET_DOWN_THRESHOLD = -0.5

# Time of day filter (Eastern Time)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0
NO_ENTRY_OPEN_MINUTES = 60    # No new entries first 60 min (wait until 10:30 AM)
NO_ENTRY_CLOSE_MINUTES = 45   # No new entries last 45 min (3:15-4:00)


def is_safe_trading_time() -> Tuple[bool, str]:
    """
    Check if current time is safe for new entries.
    
    Avoids:
    - First 15 minutes after open (9:30-9:45)
    - Last 15 minutes before close (3:45-4:00)
    
    Exits are always allowed.
    
    Returns:
        (is_safe, reason)
    """
    from datetime import datetime, timezone, timedelta
    
    # Get current time in Eastern
    # UTC-5 for EST, UTC-4 for EDT
    # Approximate: use UTC-4 during trading hours (most of year is DST)
    utc_now = datetime.now(timezone.utc)
    eastern_offset = timedelta(hours=-4)  # EDT
    now = utc_now + eastern_offset
    
    current_hour = now.hour
    current_minute = now.minute
    
    # Convert to minutes since midnight
    current_mins = current_hour * 60 + current_minute
    market_open_mins = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE  # 9:30 = 570
    market_close_mins = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE  # 16:00 = 960
    
    # Before market open
    if current_mins < market_open_mins:
        return False, "Market not open yet"
    
    # After market close
    if current_mins >= market_close_mins:
        return False, "Market closed"
    
    # First 15 minutes (9:30-9:45)
    no_entry_open_end = market_open_mins + NO_ENTRY_OPEN_MINUTES  # 585
    if current_mins < no_entry_open_end:
        mins_left = no_entry_open_end - current_mins
        return False, f"⏰ Volatile open period — wait {mins_left} min"
    
    # Last 15 minutes (3:45-4:00)
    no_entry_close_start = market_close_mins - NO_ENTRY_CLOSE_MINUTES  # 945
    if current_mins >= no_entry_close_start:
        return False, f"⏰ Volatile close period — no new entries"
    
    return True, "Safe trading window"


# ═══════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Signal:
    """Represents a trading signal."""
    indicator: str
    signal_type: str
    direction: str          # BUY or SELL
    strength: str           # STRONG, MEDIUM, WEAK
    points: int
    timestamp: datetime
    timeframe: str
    details: dict = field(default_factory=dict)
    
    @property
    def is_short_term(self) -> bool:
        return self.timeframe.lower() in SHORT_TERM_TIMEFRAMES
    
    @property
    def is_long_term(self) -> bool:
        return self.timeframe.lower() in LONG_TERM_TIMEFRAMES


@dataclass
class ConfluenceScore:
    """Aggregated confluence score for a symbol."""
    symbol: str
    buy_score: int
    sell_score: int
    buy_signals: List[Signal]
    sell_signals: List[Signal]
    context: dict
    trade_type: TradeType = TradeType.LONG_TERM
    unique_buy_indicators: Set[str] = field(default_factory=set)
    unique_sell_indicators: Set[str] = field(default_factory=set)
    pattern_bonus: int = 0
    
    @property
    def net_score(self) -> int:
        return self.buy_score - self.sell_score
    
    @property
    def direction(self) -> str:
        if self.net_score > 0:
            return "BUY"
        elif self.net_score < 0:
            return "SELL"
        return "NEUTRAL"
    
    @property
    def strength(self) -> SignalStrength:
        score = abs(self.net_score)
        if score >= 10:
            return SignalStrength.ULTRA
        elif score >= 8:
            return SignalStrength.VERY_STRONG
        elif score >= 6:
            return SignalStrength.STRONG
        elif score >= 4:
            return SignalStrength.MEDIUM
        elif score >= 2:
            return SignalStrength.WEAK
        return SignalStrength.SKIP
    
    @property
    def position_size_pct(self) -> int:
        """Recommended position size as % of available capital."""
        strength = self.strength
        # Higher conviction for high_conviction trades
        multiplier = 1.2 if self.trade_type == TradeType.HIGH_CONVICTION else 1.0
        
        if strength == SignalStrength.ULTRA:
            base = 35
        elif strength == SignalStrength.VERY_STRONG:
            base = 30
        elif strength == SignalStrength.STRONG:
            base = 25
        elif strength == SignalStrength.MEDIUM:
            base = 20
        elif strength == SignalStrength.WEAK:
            base = 15
        else:
            base = 0
        
        return min(40, int(base * multiplier))  # Cap at 40%


# ═══════════════════════════════════════════════════════════════════
# Signal History & Persistence
# ═══════════════════════════════════════════════════════════════════

SIGNAL_HISTORY_FILE = "/Users/danielwan/clawd/moomoo-alerts/signal_history.json"


def load_signal_history() -> dict:
    """Load signal history from file."""
    if os.path.exists(SIGNAL_HISTORY_FILE):
        try:
            with open(SIGNAL_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_signal_history(history: dict):
    """Save signal history to file."""
    with open(SIGNAL_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, default=str)


def track_signal(symbol: str, signal_type: str, timeframe: str, bar_time: str):
    """Track when a signal first appeared."""
    history = load_signal_history()
    key = f"{symbol}_{signal_type}_{timeframe}"
    
    if key not in history:
        history[key] = {
            "first_seen": bar_time,
            "last_seen": bar_time,
            "bar_count": 1,
        }
    else:
        history[key]["last_seen"] = bar_time
        history[key]["bar_count"] += 1
    
    save_signal_history(history)
    return history[key]["bar_count"]


def clear_stale_signals(max_age_hours: int = 24):
    """Clear signals older than max_age_hours."""
    history = load_signal_history()
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    
    to_remove = []
    for key, data in history.items():
        try:
            last_seen = datetime.fromisoformat(data["last_seen"])
            if last_seen < cutoff:
                to_remove.append(key)
        except:
            to_remove.append(key)
    
    for key in to_remove:
        del history[key]
    
    save_signal_history(history)


# ═══════════════════════════════════════════════════════════════════
# Trade Type Classification
# ═══════════════════════════════════════════════════════════════════

def classify_trade_type(signals: List[Signal]) -> TradeType:
    """
    Classify trade type based on where signals are coming from.
    
    - If both short and long term agree: HIGH_CONVICTION
    - If only short term signals: SHORT_TERM
    - If only long term signals: LONG_TERM
    """
    buy_signals = [s for s in signals if s.direction == "BUY"]
    
    has_short_term = any(s.is_short_term for s in buy_signals)
    has_long_term = any(s.is_long_term for s in buy_signals)
    
    if has_short_term and has_long_term:
        return TradeType.HIGH_CONVICTION
    elif has_short_term:
        return TradeType.SHORT_TERM
    else:
        return TradeType.LONG_TERM


def get_signal_expiry_hours(trade_type: TradeType) -> int:
    """Get signal expiry in hours based on trade type."""
    return SIGNAL_EXPIRY.get(trade_type, 24)


# ═══════════════════════════════════════════════════════════════════
# Confluence Calculator (UPDATED)
# ═══════════════════════════════════════════════════════════════════

def check_pattern_combos(signal_types: Set[str], direction: str) -> int:
    """Check for pattern combinations and return bonus points."""
    bonus = 0
    
    for pattern, bonus_pts in PATTERN_COMBOS.items():
        if all(sig in signal_types for sig in pattern):
            # Check direction matches (BUY patterns have BUY signals)
            if direction == "BUY" and any("BUY" in sig or "RED" in sig or "CHADI" in sig for sig in pattern):
                bonus += bonus_pts
            elif direction == "SELL" and any("SELL" in sig or "GREEN" in sig or "MAICHU" in sig for sig in pattern):
                bonus += bonus_pts
    
    return bonus


def calculate_confluence(signals: List[dict], context: dict) -> ConfluenceScore:
    """
    Calculate confluence score from multiple signals.
    
    IMPORTANT: Now requires DIFFERENT indicators for true confluence.
    Same indicator on different timeframes counts as 1 indicator.
    """
    buy_score = 0
    sell_score = 0
    buy_signals = []
    sell_signals = []
    buy_indicators = set()      # Track unique indicators
    sell_indicators = set()
    buy_signal_types = set()    # For pattern matching
    sell_signal_types = set()
    
    for sig in signals:
        signal_type = sig.get("signal", sig.get("signal_type", "UNKNOWN"))
        direction = sig.get("direction", "").upper()
        points = sig.get("points", SIGNAL_POINTS.get(signal_type, 1))
        indicator = sig.get("indicator", "UNKNOWN").upper()
        timeframe = sig.get("timeframe", "daily")
        
        # Skip unverified indicators
        if indicator in UNVERIFIED_INDICATORS:
            continue
        
        # MMTS is only reliable on daily timeframe (ZIG-ZAG repaints badly on intraday)
        if indicator == "MMTS" and timeframe.lower() not in MMTS_ALLOWED_TIMEFRAMES:
            continue
        
        # Parse timestamp
        ts_str = sig.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
        except:
            ts = datetime.now()
        
        signal_obj = Signal(
            indicator=indicator,
            signal_type=signal_type,
            direction=direction,
            strength="STRONG" if points >= 3 else "MEDIUM" if points >= 2 else "WEAK",
            points=points,
            timestamp=ts,
            timeframe=timeframe,
            details=sig,
        )
        
        # Correct direction based on signal type regardless of what the alert log says
        # Blue diamond = weak SELL, Yellow diamond = weak BUY
        FORCED_SELL_SIGNALS = {"BLUE_DIAMOND", "LDDX_BLUE_DIAMOND"}
        FORCED_BUY_SIGNALS = {"YELLOW_DIAMOND", "LDDX_YELLOW_DIAMOND"}
        if signal_type in FORCED_SELL_SIGNALS:
            direction = "SELL"
        elif signal_type in FORCED_BUY_SIGNALS:
            direction = "BUY"
        
        if direction == "BUY":
            buy_score += points
            buy_signals.append(signal_obj)
            buy_indicators.add(indicator)
            buy_signal_types.add(signal_type)
        elif direction == "SELL":
            sell_score += points
            sell_signals.append(signal_obj)
            sell_indicators.add(indicator)
            sell_signal_types.add(signal_type)
    
    # Check for pattern combos and add bonus
    buy_pattern_bonus = check_pattern_combos(buy_signal_types, "BUY")
    sell_pattern_bonus = check_pattern_combos(sell_signal_types, "SELL")
    
    buy_score += buy_pattern_bonus
    sell_score += sell_pattern_bonus
    
    # Apply context bonuses/penalties
    if context.get("HMA_RED"):
        buy_score += CONTEXT_POINTS["HMA_RED"]
    elif context.get("HMA_GREEN"):
        buy_score += CONTEXT_POINTS["HMA_GREEN"]
        sell_score += abs(CONTEXT_POINTS["HMA_GREEN"])
    
    if context.get("ABOVE_180"):
        buy_score += CONTEXT_POINTS["ABOVE_180"]
    elif context.get("BELOW_180"):
        buy_score += CONTEXT_POINTS["BELOW_180"]
        sell_score += abs(CONTEXT_POINTS["BELOW_180"])
    
    if context.get("D_LINE_RED"):
        buy_score += CONTEXT_POINTS["D_LINE_RED"]
    elif context.get("D_LINE_GREEN"):
        sell_score += abs(CONTEXT_POINTS["D_LINE_GREEN"])
    
    # Classify trade type
    all_signals = [Signal(
        indicator=s.get("indicator", ""),
        signal_type=s.get("signal_type", ""),
        direction=s.get("direction", ""),
        strength="",
        points=0,
        timestamp=datetime.now(),
        timeframe=s.get("timeframe", "daily"),
        details={}
    ) for s in signals if s.get("direction", "").upper() == "BUY"]
    
    trade_type = classify_trade_type(all_signals) if all_signals else TradeType.LONG_TERM
    
    return ConfluenceScore(
        symbol=context.get("symbol", "UNKNOWN"),
        buy_score=buy_score,
        sell_score=sell_score,
        buy_signals=buy_signals,
        sell_signals=sell_signals,
        context=context,
        trade_type=trade_type,
        unique_buy_indicators=buy_indicators,
        unique_sell_indicators=sell_indicators,
        pattern_bonus=buy_pattern_bonus + sell_pattern_bonus,
    )


# ═══════════════════════════════════════════════════════════════════
# Entry/Exit Decision Logic (UPDATED)
# ═══════════════════════════════════════════════════════════════════

def should_enter(confluence: ConfluenceScore, min_score: int = 5, min_indicators: int = 2) -> Tuple[bool, str]:
    """
    Determine if we should enter a position.
    
    CRITICAL: Requires signals from DIFFERENT indicators.
    MMTS 4h + MMTS daily = 1 indicator (NOT enough)
    MMTS 4h + LDZN daily = 2 indicators (OK)
    
    Returns:
        (should_enter, reason)
    """
    if confluence.direction != "BUY":
        return False, "Not a buy signal"
    
    if confluence.buy_score < min_score:
        return False, f"Score {confluence.buy_score} below minimum {min_score}"
    
    # CHECK FOR DIFFERENT INDICATORS (critical change)
    unique_indicators = len(confluence.unique_buy_indicators)
    if unique_indicators < min_indicators:
        # Exception: ULTRA strong single signal (抄底 magenta with diamond)
        has_ultra_pattern = confluence.pattern_bonus >= 3
        if not has_ultra_pattern:
            return False, f"Need {min_indicators}+ different indicators, have {unique_indicators}: {confluence.unique_buy_indicators}"
    
    # Check for conflicting signals
    if confluence.sell_score > 0:
        if confluence.sell_score >= confluence.buy_score * 0.5:
            return False, f"Too many conflicting sell signals (sell={confluence.sell_score}, buy={confluence.buy_score})"
    
    # Check context alignment (only for long-term trades)
    if confluence.trade_type == TradeType.LONG_TERM:
        if confluence.context.get("HMA_GREEN") and confluence.context.get("BELOW_180"):
            return False, "Context bearish for long-term trade (HMA green + below 180 MA)"
    
    # Build reason
    indicators_str = ", ".join(sorted(confluence.unique_buy_indicators))
    trade_type_str = confluence.trade_type.value.replace("_", " ").title()
    reason = f"{trade_type_str} trade | Score {confluence.buy_score} | {unique_indicators} indicators: {indicators_str}"
    
    if confluence.pattern_bonus > 0:
        reason += f" | Pattern bonus +{confluence.pattern_bonus}"
    
    return True, reason


def should_exit(confluence: ConfluenceScore, current_pnl_pct: float = 0, 
                relative_strength: dict = None, entry_time: datetime = None,
                trade_type: TradeType = None) -> Tuple[bool, str, str]:
    """
    Determine if we should exit a position.
    
    Returns:
        (should_exit, reason, exit_type)
        exit_type: "FULL", "PARTIAL_70", "PARTIAL_50", "PARTIAL_30"
    """
    trade_type = trade_type or confluence.trade_type
    params = TRADE_PARAMS.get(trade_type, TRADE_PARAMS[TradeType.LONG_TERM])
    
    # Check time-based exit for short-term trades
    if entry_time and params.get("max_hold_hours"):
        hours_held = (datetime.now() - entry_time).total_seconds() / 3600
        if hours_held > params["max_hold_hours"]:
            return True, f"Max hold time exceeded ({hours_held:.1f}h > {params['max_hold_hours']}h)", "FULL"
    
    # ═══════════════════════════════════════════════════════════════════
    # CRITICAL: Green Diamond Exit (don't wait for stop loss!)
    # Green diamond = strong reversal signal, exit immediately if confirmed
    # ═══════════════════════════════════════════════════════════════════
    green_diamond = any(s.signal_type in ("GREEN_DIAMOND", "LDDX_GREEN_DIAMOND") 
                       for s in confluence.sell_signals)
    if green_diamond:
        # Check for confirmation (volume or other sell signals)
        has_volume_confirm = any(s.indicator in ("ZJ", "VOLUME") for s in confluence.sell_signals)
        has_other_sell = len(confluence.unique_sell_indicators) >= 2
        
        if has_volume_confirm or has_other_sell:
            return True, f"🟢◆ Green diamond + confirmation — EXIT NOW", "FULL"
        elif confluence.sell_score >= 4:
            # Strong enough even without explicit confirmation
            return True, f"🟢◆ Green diamond (score={confluence.sell_score}) — EXIT", "FULL"
    
    # Check relative strength filter (but green diamond overrides this above)
    if relative_strength and relative_strength.get("ignore_sell_signals"):
        star_jian = any(s.signal_type == "RSCDJC_STAR_JIAN" for s in confluence.sell_signals)
        if star_jian:
            return True, "★减 confirmed top (overrides RS filter)", "FULL"
        return False, f"Hold — {relative_strength['description']}", "NONE"
    
    # Critical exit: ★减
    star_jian = any(s.signal_type == "RSCDJC_STAR_JIAN" for s in confluence.sell_signals)
    if star_jian:
        return True, "★减 confirmed top", "FULL"
    
    # Triple sell from different indicators
    if len(confluence.unique_sell_indicators) >= 3:
        return True, f"Triple indicator sell ({confluence.unique_sell_indicators})", "FULL"
    
    # 兑现 signal
    duixian = any(s.signal_type == "LDDX_DUIXIAN" for s in confluence.sell_signals)
    if duixian:
        return True, "兑现 take profit signal", "PARTIAL_70"
    
    # Double indicator sell
    if len(confluence.unique_sell_indicators) >= 2:
        return True, f"Double indicator sell ({confluence.unique_sell_indicators})", "PARTIAL_50"
    
    # Strong sell signal
    if confluence.sell_score >= 4:
        return True, f"Strong sell signal (score={confluence.sell_score})", "PARTIAL_30"
    
    return False, "Hold", "NONE"


# ═══════════════════════════════════════════════════════════════════
# Relative Strength
# ═══════════════════════════════════════════════════════════════════

def get_relative_strength(stock_change_pct: float, market_change_pct: float) -> dict:
    """Calculate relative strength of a stock vs market."""
    rs_value = stock_change_pct - market_change_pct
    market_is_down = market_change_pct < MARKET_DOWN_THRESHOLD
    
    is_outperforming = rs_value > RS_OUTPERFORM_THRESHOLD
    is_underperforming = rs_value < RS_UNDERPERFORM_THRESHOLD
    
    stock_is_green = stock_change_pct >= 0
    ignore_sell_signals = market_is_down and stock_is_green
    
    if ignore_sell_signals:
        desc = f"🟢 GREEN on RED day (RS: {rs_value:+.1f}%) — HOLD"
    elif is_outperforming:
        desc = f"💪 Outperforming (RS: {rs_value:+.1f}%)"
    elif is_underperforming:
        desc = f"📉 Underperforming (RS: {rs_value:+.1f}%)"
    else:
        desc = f"➖ In line with market (RS: {rs_value:+.1f}%)"
    
    return {
        "rs_value": rs_value,
        "is_outperforming": is_outperforming,
        "is_underperforming": is_underperforming,
        "market_is_down": market_is_down,
        "stock_is_green": stock_is_green,
        "ignore_sell_signals": ignore_sell_signals,
        "description": desc,
    }


# ═══════════════════════════════════════════════════════════════════
# Signal Aggregation from Alert Log
# ═══════════════════════════════════════════════════════════════════

# Per-timeframe signal freshness limits (same-session requirement)
# Signals older than this are stale and ignored for entry decisions
SIGNAL_MAX_AGE_HOURS = {
    "30m":   6,    # 30m signals: max 6 hours (same session, afternoon signals valid next morning)
    "1h":    8,    # 1h signals: max 8 hours
    "4h":    24,   # 4h signals: up to 1 day
    "daily": 48,   # daily signals: up to 2 days
}


def get_recent_signals(symbol: str, hours: int = 24, trade_type: TradeType = None) -> List[dict]:
    """
    Get recent signals for a symbol from the alert log.
    
    Applies per-timeframe freshness limits — short-term signals (30m/1h)
    must be from the current session. This prevents acting on yesterday's
    signals the following morning.
    """
    alert_log = "/Users/danielwan/clawd/moomoo-alerts/alert_log.jsonl"
    signals = []
    
    if not os.path.exists(alert_log):
        return signals
    
    # Use trade-type specific expiry as the outer bound
    if trade_type:
        hours = get_signal_expiry_hours(trade_type)
    
    cutoff = datetime.now() - timedelta(hours=hours)
    
    with open(alert_log, 'r') as f:
        for line in f:
            try:
                alert = json.loads(line.strip())
                
                ts = datetime.fromisoformat(alert.get("timestamp", "2000-01-01"))
                if ts < cutoff:
                    continue
                
                ticker = alert.get("ticker") or alert.get("symbol")
                if not ticker:
                    continue
                if not ticker.startswith("US."):
                    ticker = f"US.{ticker}"
                if ticker != symbol:
                    continue

                # Per-timeframe freshness check — short-term signals must be recent
                # Prevents acting on yesterday's 30m/1h signals the next morning
                timeframe = alert.get("timeframe", "daily")
                max_age_hours = SIGNAL_MAX_AGE_HOURS.get(timeframe, hours)
                tf_cutoff = datetime.now() - timedelta(hours=max_age_hours)
                if ts < tf_cutoff:
                    continue

                # Skip signals from the first 15 min of the session (open noise)
                # These come from the incomplete opening bar and are unreliable
                try:
                    from zoneinfo import ZoneInfo
                    _EST = ZoneInfo("America/New_York")
                    # Ensure we compare in Eastern time, not UTC
                    if ts.tzinfo is None:
                        ts_et = ts  # alert_log stores local (ET) naive datetimes
                    else:
                        ts_et = ts.astimezone(_EST)
                    sig_mins = ts_et.hour * 60 + ts_et.minute
                    if sig_mins < (9 * 60 + 45):  # Before 9:45 AM ET
                        continue
                except:
                    pass
                
                indicator = alert.get("indicator", "UNKNOWN")
                direction = (alert.get("direction") or "").upper()
                timeframe = alert.get("timeframe", "daily")
                
                signal_type = map_alert_to_signal_type(indicator, direction, alert)
                points = SIGNAL_POINTS.get(signal_type, 1)
                
                signals.append({
                    "signal": signal_type,
                    "signal_type": signal_type,
                    "indicator": indicator,
                    "direction": direction,
                    "timeframe": timeframe,
                    "timestamp": alert.get("timestamp"),
                    "points": points,
                    "close": alert.get("close", 0),
                    "volume": alert.get("volume", 0),
                    "raw": alert,
                })
            except:
                continue
    
    return signals


def map_alert_to_signal_type(indicator: str, direction: str, alert: dict) -> str:
    """Map raw alert to our signal type."""
    indicator = indicator.upper()
    direction = direction.upper()
    message = alert.get("message", "").upper()
    
    # Check for double patterns first
    if "😊😊" in message or "DOUBLE" in message:
        if direction == "BUY":
            return "MMTS_BUY_DOUBLE"
        else:
            return "MMTS_SELL_DOUBLE"
    
    # Check for 抄底 (bottom fishing)
    if "抄底" in message or "CHADI" in message:
        if "MAGENTA" in message or "STRONG" in message:
            return "MABS_CHADI_STRONG"
        return "MABS_CHADI_WEAK"
    
    # Check for 卖出 (sell signal)
    if "卖出" in message or "MAICHU" in message:
        if "MAGENTA" in message or "STRONG" in message:
            return "MABS_MAICHU_STRONG"
        return "MABS_MAICHU_WEAK"
    
    # Check for ★减 (star jian - confirmed top)
    if "★减" in message or "STAR" in message:
        return "RSCDJC_STAR_JIAN"
    
    # LDZN / LDDX diamonds
    if "RED" in message and "DIAMOND" in message:
        return "RED_DIAMOND" if "LDZN" in indicator else "LDDX_RED_DIAMOND"
    if "BLUE" in message and "DIAMOND" in message:
        return "BLUE_DIAMOND" if "LDZN" in indicator else "LDDX_BLUE_DIAMOND"
    if "GREEN" in message and "DIAMOND" in message:
        return "GREEN_DIAMOND" if "LDZN" in indicator else "LDDX_GREEN_DIAMOND"
    if "YELLOW" in message and "DIAMOND" in message:
        return "YELLOW_DIAMOND" if "LDZN" in indicator else "LDDX_YELLOW_DIAMOND"
    
    # 兑现
    if "兑现" in message or "DUIXIAN" in message:
        return "LDDX_DUIXIAN"
    
    # MMTS
    if indicator == "MMTS":
        return "MMTS_BUY" if direction == "BUY" else "MMTS_SELL"
    
    # RTE
    if "RTE" in indicator:
        return "RTE_OS_REV" if direction == "BUY" else "RTE_OB_REV"
    
    # ZJ
    if indicator == "ZJ":
        return "ZJ_RED_BAR" if direction == "BUY" else "ZJ_GREEN_BAR"
    
    # KDZS
    if indicator == "KDZS":
        return "KDZS_BLUE_DIAMOND" if direction == "BUY" else "KDZS_YELLOW_DIAMOND"
    
    return f"{indicator}_{direction}"


# ═══════════════════════════════════════════════════════════════════
# Main Analysis Function
# ═══════════════════════════════════════════════════════════════════

def analyze_symbol(symbol: str, hours: int = 24) -> dict:
    """
    Full analysis of a symbol using APEX 2.0 system.
    """
    signals = get_recent_signals(symbol, hours=hours)
    
    context = {
        "symbol": symbol,
        "HMA_RED": False,
        "HMA_GREEN": False,
        "HMA_YELLOW": True,
        "ABOVE_180": True,
        "BELOW_180": False,
        "D_LINE_RED": False,
        "D_LINE_GREEN": False,
    }
    
    confluence = calculate_confluence(signals, context)
    
    if confluence.direction == "BUY":
        should, reason = should_enter(confluence)
        if should:
            recommendation = "BUY"
        else:
            recommendation = "WAIT"
    elif confluence.direction == "SELL":
        should, reason, exit_type = should_exit(confluence, 0)
        if should:
            recommendation = f"SELL ({exit_type})"
        else:
            recommendation = "HOLD"
            reason = "No strong exit signals"
    else:
        recommendation = "NEUTRAL"
        reason = "No clear direction"
    
    return {
        "symbol": symbol,
        "confluence": confluence,
        "recommendation": recommendation,
        "reason": reason,
        "trade_type": confluence.trade_type.value,
        "position_size": confluence.position_size_pct,
        "buy_score": confluence.buy_score,
        "sell_score": confluence.sell_score,
        "unique_buy_indicators": list(confluence.unique_buy_indicators),
        "unique_sell_indicators": list(confluence.unique_sell_indicators),
        "pattern_bonus": confluence.pattern_bonus,
        "signals": signals,
        "context": context,
        "trade_params": TRADE_PARAMS.get(confluence.trade_type, {}),
    }


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "US.NVDA"
    
    result = analyze_symbol(symbol)
    print(f"\n═══ APEX 2.0 Analysis: {result['symbol']} ═══")
    print(f"Recommendation: {result['recommendation']}")
    print(f"Trade Type: {result['trade_type']}")
    print(f"Reason: {result['reason']}")
    print(f"Position Size: {result['position_size']}%")
    print(f"Buy Score: {result['buy_score']} | Sell Score: {result['sell_score']}")
    print(f"Buy Indicators: {result['unique_buy_indicators']}")
    print(f"Sell Indicators: {result['unique_sell_indicators']}")
    if result['pattern_bonus']:
        print(f"Pattern Bonus: +{result['pattern_bonus']}")
    print(f"Signals: {len(result['signals'])}")
    print(f"Trade Params: {result['trade_params']}")
