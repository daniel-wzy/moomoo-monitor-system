"""
Trading Decision Engine — APEX Edition
=======================================
APEX: Autonomous Position Entry/eXit

Key features:
- Confluence scoring (2+ indicators must agree)
- Point-based position sizing
- Relative strength filter (ignore sells when green on red day)
- Signal persistence tracking
- Diamond signal detection (🔴🔵🟢🟡)
"""

import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from moomoo import OpenQuoteContext

from trading_config import (
    ALLOWED_STOCKS, MIN_CASH_RESERVE,
    STOP_LOSS_PERCENT, TAKE_PROFIT_PERCENT,
    POSITION_FILE, MAX_POSITIONS, MIN_POSITION_SIZE
)
from trading_bot import (
    load_state, execute_buy, execute_sell, 
    calculate_position_size, get_portfolio_summary
)
from core.delivery import send_discord
from trading_config import DISCORD_ALERTS_CHANNEL, DISCORD_LOGS_CHANNEL

# Import APEX 2.0 system
from apex_system import (
    calculate_confluence, should_enter, should_exit,
    get_recent_signals, get_relative_strength,
    track_signal, clear_stale_signals,
    SIGNAL_POINTS, SignalStrength, TradeType,
    TRADE_PARAMS, classify_trade_type,
    calculate_trailing_stop, get_volume_confirmation, is_safe_trading_time
)

# ═══════════════════════════════════════════════════════════════════
# APEX CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

MIN_BUY_SCORE = 6           # Minimum confluence score for random stocks
MIN_INDICATORS = 2          # Need at least 2 DIFFERENT indicators (not just timeframes)
REENTRY_COOLDOWN_MIN = 30   # Minutes to wait before re-entering same stock

# ── BULL DAY MODE ────────────────────────────────────────────────
# Activated when SPY is up >0.5% after 10 AM
# Uses shorter timeframes (30m/1h) and lower confluence bar
BULL_MODE_SPY_THRESHOLD = 0.5   # SPY % gain to trigger bull mode
BULL_MODE_MIN_SCORE = 3         # Lower confluence bar (single strong signal)
BULL_MODE_MIN_INDICATORS = 1    # Only 1 indicator needed
BULL_MODE_PREFERRED_TF = {"30m", "1h"}  # Preferred timeframes in bull mode
BULL_MODE_HOLD_UNTIL = 15       # Don't exit before 3 PM (hold for trend)
BULL_MODE_FORCE_EXIT = 15       # Force exit at 3:45 PM
BULL_MODE_FORCE_EXIT_MIN = 45
# ────────────────────────────────────────────────────────────────

# Researched stocks get lower thresholds
RESEARCHED_MIN_SCORE = 5    # Lower bar for researched stocks
RESEARCHED_MIN_INDICATORS = 2  # Still need 2 different indicators
RESEARCHED_STOCKS_FILE = "/Users/danielwan/clawd/moomoo-alerts/researched_stocks.json"
SUPPORT_ZONES_FILE = "/Users/danielwan/clawd/moomoo-alerts/support_zones.json"
BLACKLIST_FILE = "/Users/danielwan/clawd/moomoo-alerts/blacklist.json"
APPROVALS_CHANNEL = "1476517060346642483"  # #approvals Discord channel


def load_blacklist() -> dict:
    """Load the trading blacklist."""
    try:
        with open(BLACKLIST_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"stocks": {}, "pending_approvals": {}}


def is_blacklisted(symbol: str) -> tuple:
    """
    Check if symbol is on the blacklist.
    Returns (is_blocked, reason, has_pending_approval)
    """
    data = load_blacklist()
    stocks = data.get('stocks', {})
    pending = data.get('pending_approvals', {})

    if symbol not in stocks:
        return False, None, False

    entry = stocks[symbol]
    reason = entry.get('reason', 'Previously lost on this stock')
    has_pending = symbol in pending

    return True, reason, has_pending


def request_blacklist_approval(symbol: str, reason: str, score: int, indicators: list, price: float):
    """
    Send approval request to #approvals Discord channel.
    Saves pending approval state to blacklist.json.
    """
    ticker = symbol.replace('US.', '')
    bl_data = load_blacklist()

    # Save pending approval
    bl_data.setdefault('pending_approvals', {})[symbol] = {
        'requested_at': datetime.now().isoformat(),
        'score': score,
        'indicators': indicators,
        'price': price,
        'entry_reason': reason,
    }
    with open(BLACKLIST_FILE, 'w') as f:
        json.dump(bl_data, f, indent=2)

    # Format the approval request message
    stock_info = bl_data.get('stocks', {}).get(symbol, {})
    total_pnl = stock_info.get('total_pnl', 0)
    losses = stock_info.get('losses', 0)
    ind_str = ', '.join(indicators)

    msg = (
        f"⚠️ **BLACKLIST APPROVAL NEEDED**\n"
        f"Stock: **{ticker}** @ ${price:.2f}\n"
        f"Blacklist reason: {stock_info.get('reason', 'Multiple losses')}\n"
        f"Past losses: {losses}x | Total P&L: ${total_pnl:+.2f}\n\n"
        f"New signal: Score {score} | Indicators: {ind_str}\n"
        f"Entry reason: {reason}\n\n"
        f"Reply **APPROVE {ticker}** or **DENY {ticker}** in this channel."
    )

    try:
        send_discord(msg, target=APPROVALS_CHANNEL)
        print(f"  📨 Approval requested in #approvals for {ticker}")
    except Exception as e:
        print(f"  ❌ Failed to send approval request: {e}")


def get_support_zone(symbol: str) -> dict:
    """Get support zone levels for a symbol if available."""
    try:
        with open(SUPPORT_ZONES_FILE, 'r') as f:
            data = json.load(f)
        return data.get('zones', {}).get(symbol, {})
    except:
        return {}


def get_zone_min_score(symbol: str, current_price: float) -> tuple:
    """
    Check if price is at a support zone and return adjusted min score.
    Returns (min_score, zone_description)
    """
    zone = get_support_zone(symbol)
    if not zone:
        return None, None

    s1 = zone.get('s1_watch')
    s2 = zone.get('s2_core')
    s3 = zone.get('s3_tail')

    try:
        with open(SUPPORT_ZONES_FILE) as f:
            cfg = json.load(f)
        s3_score = cfg.get('min_score_at_s3', 3)
        s2_score = cfg.get('min_score_at_s2', 4)
        s1_score = cfg.get('min_score_at_s1', 5)
    except:
        s3_score, s2_score, s1_score = 3, 4, 5

    if s3 and current_price <= s3 * 1.01:
        return s3_score, f"🔴 S3 Tail zone (${s3}) — min score {s3_score}"
    elif s2 and current_price <= s2 * 1.01:
        return s2_score, f"🟠 S2 Core zone (${s2}) — min score {s2_score}"
    elif s1 and current_price <= s1 * 1.01:
        return s1_score, f"🟡 S1 Watch zone (${s1}) — min score {s1_score}"

    return None, None
MIN_PERSISTENCE_BARS = 1    # Signal must persist at least 1 bar

# Research validation flag (can be enabled/disabled)
ENABLE_RESEARCH_CHECK = True
RESEARCH_CACHE_FILE = "/Users/danielwan/clawd/moomoo-alerts/research_cache.json"
RESEARCH_CACHE_HOURS = 4  # How long to cache research results


def load_research_cache() -> dict:
    """Load cached research results."""
    try:
        with open(RESEARCH_CACHE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}


def save_research_cache(cache: dict):
    """Save research cache."""
    with open(RESEARCH_CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def quick_research_check(symbol: str) -> Tuple[bool, str]:
    """
    Quick research validation before entering a trade.
    
    Checks:
    1. Is this a researched stock (from daily report)?
    2. Any recent negative news?
    
    Returns:
        (is_valid, reason)
    """
    ticker = symbol.replace("US.", "")
    
    # Check if it's a researched stock (already vetted)
    if is_researched_stock(symbol):
        return True, f"✅ {ticker} is on researched watchlist"
    
    # Check cache
    cache = load_research_cache()
    cache_key = f"{ticker}_{datetime.now().strftime('%Y-%m-%d')}"
    
    if cache_key in cache:
        cached = cache[cache_key]
        # Check if cache is still valid
        cached_time = datetime.fromisoformat(cached.get("timestamp", "2000-01-01"))
        if (datetime.now() - cached_time).total_seconds() < RESEARCH_CACHE_HOURS * 3600:
            return cached.get("is_valid", True), cached.get("reason", "Cached result")
    
    # For now, allow non-researched stocks if they have strong signals
    # The research integration will be enhanced with web search later
    return True, f"⚠️ {ticker} not on watchlist - relying on technical signals only"


# Load researched stocks
def get_researched_stocks() -> set:
    """Load researched stocks from file."""
    try:
        with open(RESEARCHED_STOCKS_FILE, 'r') as f:
            import json
            data = json.load(f)
            return set(s['symbol'] for s in data.get('stocks', []))
    except:
        return set()

def is_researched_stock(symbol: str) -> bool:
    """Check if a stock is in the researched list."""
    researched = get_researched_stocks()
    return symbol in researched or symbol.replace('US.', '') in [s.replace('US.', '') for s in researched]


# Position sizing by confluence strength (AGGRESSIVE)
V_POSITION_SIZE_MAP = {
    SignalStrength.ULTRA: 0.40,        # 12+ points → 40% of available cash
    SignalStrength.VERY_STRONG: 0.35,  # 9-11 points → 35%
    SignalStrength.STRONG: 0.30,       # 6-8 points → 30%
    SignalStrength.MEDIUM: 0.20,       # 4-5 points → 20%
    SignalStrength.WEAK: 0.15,         # 2-3 points → 15%
    SignalStrength.SKIP: 0.0,          # 0-1 points
}

# ═══════════════════════════════════════════════════════════════════
# MARKET CONTEXT
# ═══════════════════════════════════════════════════════════════════

def is_bull_day_mode() -> Tuple[bool, float]:
    """
    Check if we're in bull day mode.
    
    Conditions:
    - SPY is up > BULL_MODE_SPY_THRESHOLD% 
    - Time is after 10:00 AM (let market settle first)
    
    Returns:
        (is_bull_mode, spy_change_pct)
    """
    now = datetime.now()
    
    # Only activate after 10 AM
    if now.hour < 10:
        return False, 0.0
    
    # Also don't activate after 3:30 PM (too late for new entries)
    if now.hour >= 15 and now.minute >= 30:
        return False, 0.0
    
    try:
        # Reuse get_market_context which already handles SPY correctly
        ctx_data = get_market_context("US.AAPL")  # Any symbol — we only need SPY change
        spy_chg = ctx_data.get("market_change_pct", 0.0)
        return spy_chg >= BULL_MODE_SPY_THRESHOLD, spy_chg
    except Exception as e:
        print(f"Bull mode check failed: {e}")
    
    return False, 0.0


# Cycle-level price cache to avoid hammering the Moomoo API
# Populated once per trading cycle via get_all_prices()
_price_cache: Dict[str, float] = {}
_prev_close_cache: Dict[str, float] = {}


def get_market_context(symbol: str) -> dict:
    """
    Fetch current market context for a symbol.
    Uses cycle-level price cache to avoid per-symbol API calls.
    """
    context = {
        "symbol": symbol,
        "current_price": 0,
        "stock_change_pct": 0,
        "market_change_pct": 0,
        "HMA_RED": False,
        "HMA_GREEN": False,
        "ABOVE_180": True,
    }
    
    # Use cache if available (populated by get_all_prices at cycle start)
    if symbol in _price_cache and symbol in _prev_close_cache:
        price = _price_cache[symbol]
        prev = _prev_close_cache[symbol]
        context["current_price"] = price
        context["stock_change_pct"] = ((price - prev) / prev * 100) if prev > 0 else 0
        
        spy_price = _price_cache.get("US.SPY", 0)
        spy_prev = _prev_close_cache.get("US.SPY", 0)
        context["market_change_pct"] = ((spy_price - spy_prev) / spy_prev * 100) if spy_prev > 0 else 0
        return context
    
    # Fallback: individual API call (only if cache not available)
    ctx = None
    try:
        ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        symbols_to_fetch = [symbol, "US.SPY"]
        ret, data = ctx.get_market_snapshot(symbols_to_fetch)
        
        if ret == 0 and len(data) > 0:
            for _, row in data.iterrows():
                if row["prev_close_price"] > 0:
                    change_pct = ((row["last_price"] - row["prev_close_price"]) / row["prev_close_price"]) * 100
                else:
                    change_pct = 0
                
                if row["code"] == symbol:
                    context["current_price"] = row["last_price"]
                    context["stock_change_pct"] = change_pct
                elif row["code"] == "US.SPY":
                    context["market_change_pct"] = change_pct
            
    except Exception as e:
        print(f"Error fetching context for {symbol}: {e}")
    finally:
        if ctx:
            ctx.close()
    
    return context

def get_all_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch current prices for multiple symbols. Populates cycle-level cache."""
    import time as _time
    global _price_cache, _prev_close_cache
    prices = {}
    
    # Retry up to 3 times with backoff on rate limit
    for attempt in range(3):
        ctx = None
        try:
            ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
            batch_size = 100
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i+batch_size]
                ret, data = ctx.get_market_snapshot(batch)
                if ret == 0:
                    for _, row in data.iterrows():
                        code = row['code']
                        prices[code] = row['last_price']
                        _price_cache[code] = row['last_price']
                        _prev_close_cache[code] = row['prev_close_price']
                else:
                    print(f"Price fetch attempt {attempt+1} failed: {data}")
            if prices:
                break  # Success
        except Exception as e:
            print(f"Price fetch attempt {attempt+1} error: {e}")
        finally:
            if ctx:
                ctx.close()
        if not prices:
            _time.sleep(3)  # Wait before retry
    
    return prices

# ═══════════════════════════════════════════════════════════════════
# APEX DECISION LOGIC
# ═══════════════════════════════════════════════════════════════════

def evaluate_v_entry(symbol: str, state: dict) -> Tuple[bool, str, int, float]:
    """
    Evaluate entry using APEX system.
    
    Returns:
        (should_buy, reason, position_size_pct, score)
    """
    # Check if already in position
    if symbol in state.get("positions", {}):
        return False, "Already in position", 0, 0
    
    # Check if allowed
    if symbol not in ALLOWED_STOCKS:
        return False, "Symbol not in allowed list", 0, 0
    
    # Check position count
    if len(state.get("positions", {})) >= MAX_POSITIONS:
        return False, f"Max positions ({MAX_POSITIONS}) reached", 0, 0
    
    # Check bull day mode
    bull_mode, spy_chg = is_bull_day_mode()
    
    # In bull mode: only look at recent 30m/1h signals (last 2h)
    # In normal mode: look at 24h signals
    signal_hours = 2 if bull_mode else 24
    signals = get_recent_signals(symbol, hours=signal_hours)
    
    if not signals:
        return False, "No recent signals", 0, 0
    
    # Filter to BUY signals only
    buy_signals = [s for s in signals if s["direction"] == "BUY"]
    sell_signals = [s for s in signals if s["direction"] == "SELL"]
    
    if not buy_signals:
        return False, "No buy signals", 0, 0
    
    # In bull mode: prefer 30m/1h signals, skip daily (those are swing trade signals)
    if bull_mode:
        intraday_buys = [s for s in buy_signals if s.get("timeframe", "") in BULL_MODE_PREFERRED_TF]
        if intraday_buys:
            buy_signals = intraday_buys  # Prioritize intraday signals
        # Stock must also be green on the day to enter in bull mode
        ctx = get_market_context(symbol)
        if ctx.get("stock_change_pct", 0) < 0:
            return False, f"Bull mode: {symbol.replace('US.','')} is red on the day ({ctx['stock_change_pct']:.1f}%)", 0, 0

    # ── MACD alignment filter ────────────────────────────────────────
    # If a signal's own metadata says MACD is bearish, don't count it.
    # Filter those signals out before calculating confluence.
    filtered_buy_signals = []
    macd_rejected = 0
    for s in buy_signals:
        hma_state = s.get("raw", s).get("hma_state", "")
        if isinstance(hma_state, str) and "MACD: Bearish" in hma_state:
            macd_rejected += 1
        else:
            filtered_buy_signals.append(s)
    if macd_rejected > 0 and not filtered_buy_signals:
        return False, f"All buy signals rejected — MACD bearish on all ({macd_rejected} signals)", 0, 0
    if filtered_buy_signals:
        buy_signals = filtered_buy_signals
    # ────────────────────────────────────────────────────────────────

    # ── Trend filter: halve points for BEAR trend buys ───────────────
    # A BEAR trend buy needs explicit reversal signal to be valid.
    # Without one, BEAR trend signals contribute half their normal points.
    REVERSAL_SIGNAL_TYPES = {
        "RED_DIAMOND", "LDDX_RED_DIAMOND", "RTE_OS_REV",
        "MABS_CHADI_STRONG", "MABS_CHADI_WEAK", "BLUE_DIAMOND",
        "LDDX_BLUE_DIAMOND", "MMTS_BUY_DOUBLE",
    }
    bear_buy_signals = [
        s for s in buy_signals
        if s.get("raw", s).get("trend", "").upper() in ("DOWN", "BEAR")
    ]
    has_reversal = any(
        s.get("signal_type", s.get("signal", "")) in REVERSAL_SIGNAL_TYPES
        for s in bear_buy_signals
    )
    if bear_buy_signals and not has_reversal:
        # Penalise: rebuild buy_signals with halved points for BEAR trend entries
        adjusted = []
        for s in buy_signals:
            trend = s.get("raw", s).get("trend", "").upper()
            if trend in ("DOWN", "BEAR"):
                s = dict(s)  # shallow copy so we don't mutate the original
                s["points"] = max(1, s.get("points", 1) // 2)
            adjusted.append(s)
        buy_signals = adjusted
    # ────────────────────────────────────────────────────────────────

    # ── Cross-timeframe price sanity check ──────────────────────────
    # If the same stock has wildly different prices across timeframes,
    # Moomoo data is bad (split-adjusted candles, bad ticks, etc).
    # Reject the trade entirely to avoid acting on garbage data.
    tf_prices = {
        s["timeframe"]: s["close"]
        for s in signals
        if s.get("close", 0) > 0
    }
    if len(tf_prices) >= 2:
        price_vals = list(tf_prices.values())
        max_price = max(price_vals)
        min_price = min(price_vals)
        if min_price > 0:
            divergence = (max_price - min_price) / min_price
            if divergence > 0.15:  # >15% spread across timeframes = bad data
                tf_str = ", ".join(f"{tf}=${p:.2f}" for tf, p in tf_prices.items())
                return False, f"Cross-timeframe price divergence {divergence:.0%} — bad data [{tf_str}]", 0, 0
    # ────────────────────────────────────────────────────────────────
    
    # Get context
    context = get_market_context(symbol)

    # Recombine filtered/adjusted buy signals with original sell signals for confluence
    adjusted_signals = buy_signals + sell_signals

    # Calculate confluence
    confluence = calculate_confluence(adjusted_signals, context)
    
    # Check for conflicting signals
    if confluence.sell_score > 0 and confluence.sell_score >= confluence.buy_score * 0.5:
        return False, f"Conflicting signals (buy={confluence.buy_score}, sell={confluence.sell_score})", 0, confluence.buy_score
    
    # Check if this is a researched stock (lower thresholds)
    researched = is_researched_stock(symbol)
    
    # ── Apply appropriate thresholds ────────────────────────────────
    if bull_mode:
        # Bull day mode: lower bar, prioritize momentum
        min_score = BULL_MODE_MIN_SCORE
        min_indicators = BULL_MODE_MIN_INDICATORS
        mode_tag = f" 🐂 Bull Mode (SPY {spy_chg:+.1f}%)"
    elif researched:
        min_score = RESEARCHED_MIN_SCORE
        min_indicators = RESEARCHED_MIN_INDICATORS
        mode_tag = " (researched)"
    else:
        min_score = MIN_BUY_SCORE
        min_indicators = MIN_INDICATORS
        mode_tag = ""

    # Support zone override — lower bar if price is at S1/S2/S3
    ctx_for_zone = get_market_context(symbol)
    current_price_for_zone = ctx_for_zone.get('current_price', 0)
    zone_min_score, zone_desc = get_zone_min_score(symbol, current_price_for_zone)
    if zone_min_score is not None and zone_min_score < min_score:
        min_score = zone_min_score
        mode_tag = f" {zone_desc}"
    # ────────────────────────────────────────────────────────────────
    
    # Check minimum score
    if confluence.buy_score < min_score:
        return False, f"Score {confluence.buy_score} below minimum {min_score}{mode_tag}", 0, confluence.buy_score
    
    # HARD FLOOR: score must always meet minimum regardless of any override
    # This prevents support zone or other overrides from allowing junk entries
    ABSOLUTE_MIN_SCORE = 4  # Never trade below this, period
    if confluence.buy_score < ABSOLUTE_MIN_SCORE:
        return False, f"Score {confluence.buy_score} below absolute minimum {ABSOLUTE_MIN_SCORE}", 0, confluence.buy_score

    # CHECK FOR DIFFERENT INDICATORS (APEX 2.0 critical change)
    # Same indicator on different timeframes does NOT count as multiple indicators
    unique_indicators = len(confluence.unique_buy_indicators)
    if unique_indicators < min_indicators:
        # Exception: ULTRA strong pattern (double smiley + diamond + 抄底)
        # BUT only if score is also strong enough
        has_ultra_pattern = confluence.pattern_bonus >= 3 and confluence.buy_score >= MIN_BUY_SCORE
        if not has_ultra_pattern:
            indicators_str = ", ".join(sorted(confluence.unique_buy_indicators))
            return False, f"Need {min_indicators}+ different indicators{mode_tag}, have {unique_indicators}: [{indicators_str}]", 0, confluence.buy_score
    
    # Get trade type and parameters
    trade_type = confluence.trade_type
    trade_params = TRADE_PARAMS.get(trade_type, TRADE_PARAMS[TradeType.LONG_TERM])
    
    # Determine position size based on strength AND trade type
    base_size = V_POSITION_SIZE_MAP.get(confluence.strength, 0.10)
    # Boost for high conviction trades
    if trade_type == TradeType.HIGH_CONVICTION:
        base_size = min(0.40, base_size * 1.2)
    position_size_pct = int(base_size * 100)
    
    # Build reason with APEX 2.0 details
    indicators_str = ", ".join(sorted(confluence.unique_buy_indicators))
    trade_type_str = trade_type.value.replace("_", " ").title()
    reason = f"APEX 2.0 {confluence.strength.value} | {trade_type_str} | {unique_indicators} indicators: [{indicators_str}] | Score: {confluence.buy_score}"
    
    if confluence.pattern_bonus > 0:
        reason += f" | Pattern +{confluence.pattern_bonus}"
    
    return True, reason, position_size_pct, confluence.buy_score

def evaluate_v_exit(symbol: str, position: dict, context: dict) -> Tuple[bool, str, str]:
    """
    Evaluate exit using APEX system with relative strength filter.
    
    Returns:
        (should_exit, reason, exit_type)
    """
    current_price = context.get("current_price", position.get("entry_price", 0))
    entry_price = position.get("entry_price", current_price)
    
    if entry_price == 0:
        entry_price = current_price if current_price > 0 else 1
    
    pnl_pct = (current_price - entry_price) / entry_price * 100
    
    # Calculate relative strength
    rs = get_relative_strength(
        context.get("stock_change_pct", 0),
        context.get("market_change_pct", 0)
    )
    
    # Get original stop loss
    original_stop = position.get("stop_loss", entry_price * (1 - STOP_LOSS_PERCENT / 100))
    
    # Calculate trailing stop (moves up as profit increases)
    trade_type_str = position.get("trade_type", "long_term")
    try:
        trade_type = TradeType(trade_type_str)
    except:
        trade_type = TradeType.LONG_TERM
    
    trailing_stop, trail_reason = calculate_trailing_stop(
        entry_price, current_price, original_stop, trade_type
    )
    
    # Use the higher of original or trailing stop
    effective_stop = max(original_stop, trailing_stop)
    
    # Check stop loss (trailing or original)
    if current_price <= effective_stop:
        if trailing_stop > original_stop:
            return True, f"🔒 {trail_reason} — Stopped @ ${effective_stop:.2f} (P/L: {pnl_pct:+.1f}%)", "FULL"
        else:
            return True, f"Stop loss @ ${effective_stop:.2f} (P/L: {pnl_pct:+.1f}%)", "FULL"
    
    # Check take profit (always applies)
    take_profit = position.get("take_profit", entry_price * (1 + TAKE_PROFIT_PERCENT / 100))
    if current_price >= take_profit:
        return True, f"Take profit @ ${take_profit:.2f} (P/L: {pnl_pct:+.1f}%)", "FULL"
    
    # Get signals
    signals = get_recent_signals(symbol, hours=8)
    
    if not signals:
        return False, "No signals, hold", "NONE"
    
    # Calculate confluence
    confluence = calculate_confluence(signals, context)
    
    # RELATIVE STRENGTH FILTER
    # If stock is green on a red day, ignore sell signals (except critical)
    if rs.get("ignore_sell_signals"):
        # Only exit on critical signal (★减)
        star_jian = any(s.signal_type == "RSCDJC_STAR_JIAN" for s in confluence.sell_signals)
        if star_jian:
            return True, f"★减 top (overrides RS) | {rs['description']}", "FULL"
        return False, f"Hold — {rs['description']}", "NONE"
    
    # Normal exit logic
    sell_signals = [s for s in signals if s["direction"] == "SELL"]

    # Count unique timeframes with sell signals
    sell_timeframes = set(s.get("timeframe", "") for s in sell_signals)
    SHORT_TF = {"30m", "1h"}
    LONG_TF = {"4h", "daily"}
    short_tf_sells = sell_timeframes & SHORT_TF
    long_tf_sells = sell_timeframes & LONG_TF

    unique_sell_indicators = set(s.get("indicator", "") for s in sell_signals)

    # ★减 confirmed top — always full exit
    star_jian = any("STAR_JIAN" in s.get("signal", "") or "★减" in s.get("indicator", "") for s in sell_signals)
    if star_jian:
        return True, f"★减 confirmed top | P/L: {pnl_pct:+.1f}%", "FULL"

    # ═══════════════════════════════════════════════════════════════
    # EXIT STRENGTH TIERS — based on timeframe alignment
    # ═══════════════════════════════════════════════════════════════

    # TIER 1 — FULL EXIT: short-term AND long-term TF both confirm sell
    # e.g. sad face on 30m AND 4h/daily → strong reversal, exit all
    if short_tf_sells and long_tf_sells and len(unique_sell_indicators) >= 2:
        return True, f"Full exit: {'+'.join(sorted(short_tf_sells))} AND {'+'.join(sorted(long_tf_sells))} sell ({len(unique_sell_indicators)} indicators) | P/L: {pnl_pct:+.1f}%", "FULL"

    # TIER 2 — 75% EXIT: 3+ different indicators all selling on any TF
    # Strong multi-indicator confluence even on single TF
    if len(unique_sell_indicators) >= 3:
        if pnl_pct >= 3.0:
            return True, f"75% exit: {len(unique_sell_indicators)} indicators selling | P/L: {pnl_pct:+.1f}%", "PARTIAL_70"
        else:
            return True, f"Full exit (small P/L): {len(unique_sell_indicators)} indicators selling | P/L: {pnl_pct:+.1f}%", "FULL"

    # TIER 3 — 50% EXIT: 2 indicators on same TF, position in decent profit
    if len(unique_sell_indicators) >= 2 and pnl_pct >= 3.0:
        return True, f"50% exit: {len(unique_sell_indicators)} indicators selling | P/L: {pnl_pct:+.1f}%", "PARTIAL_50"

    # TIER 4 — 25% EXIT: underperforming RS + any sell signal, take partial profits
    if rs.get("is_underperforming") and len(sell_signals) >= 1 and pnl_pct >= 1.0:
        return True, f"25% exit: sell + underperforming ({rs['rs_value']:+.1f}% RS) | P/L: {pnl_pct:+.1f}%", "PARTIAL_30"

    # 兑现 take profit signal — partial
    duixian = any("DUIXIAN" in s.get("signal", "") or "兑现" in s.get("indicator", "") for s in sell_signals)
    if duixian:
        return True, f"兑现 take-profit signal | P/L: {pnl_pct:+.1f}%", "PARTIAL_50"

    return False, "Hold", "NONE"

# ═══════════════════════════════════════════════════════════════════
# MAIN TRADING CYCLE
# ═══════════════════════════════════════════════════════════════════

def run_trading_cycle():
    """
    Main APEX trading decision cycle.
    
    1. Check existing positions for exits (with RS filter)
    2. Look for new entry signals (with confluence scoring)
    3. Execute trades as needed
    """
    state = load_state()
    actions_taken = []
    
    # Skip if halted
    if state.get("halted"):
        return [f"Trading halted: {state.get('halt_reason')}"]
    
    # Clear stale signals
    clear_stale_signals(max_age_hours=48)
    
    # Get all relevant symbols
    position_symbols = list(state.get("positions", {}).keys())
    all_symbols = list(set(position_symbols + ALLOWED_STOCKS))
    
    # Fetch current prices — with retry on rate limit
    import time
    for attempt in range(3):
        prices = get_all_prices(all_symbols + ["US.SPY"])
        if prices:
            break
        time.sleep(2)  # Wait 2s and retry if rate limited
    spy_price_check = prices.get("US.SPY", 0)
    spy_price = prices.get("US.SPY", 0)
    
    # ─── CHECK EXITS (with APEX + RS filter) ─────────────────────
    now = datetime.now()
    bull_mode_active, spy_chg_now = is_bull_day_mode()
    
    # Bull mode force exit at 3:45 PM
    bull_force_exit = (now.hour == BULL_MODE_FORCE_EXIT and now.minute >= BULL_MODE_FORCE_EXIT_MIN) or now.hour > BULL_MODE_FORCE_EXIT
    
    # Block signal-based exits before 10:15 AM (open is too noisy)
    # Stop losses are always allowed regardless of time
    NO_EXIT_BEFORE_MINUTE = 10 * 60 + 15  # 10:15 AM in minutes since midnight
    current_mins = now.hour * 60 + now.minute
    signal_exits_allowed = current_mins >= NO_EXIT_BEFORE_MINUTE

    for symbol, position in list(state.get("positions", {}).items()):
        context = get_market_context(symbol)
        if context["current_price"] == 0:
            context["current_price"] = prices.get(symbol, position.get("entry_price", 0))
        
        # Bull mode: force full exit at 3:45 PM
        is_bull_position = position.get("bull_mode_entry", False)
        if is_bull_position and bull_force_exit:
            should_exit, reason, exit_type = True, "Bull mode: forced exit at 3:45 PM", "FULL"
        # Bull mode: hold until 3 PM, ignore normal sell signals
        elif is_bull_position and now.hour < BULL_MODE_HOLD_UNTIL:
            # Only exit on stop-loss, ignore other signals
            entry = position.get("entry_price", 0)
            stop = entry * (1 - STOP_LOSS_PERCENT / 100)
            if context["current_price"] <= stop:
                should_exit, reason, exit_type = True, f"Stop loss hit (bull hold)", "FULL"
            else:
                should_exit, reason, exit_type = False, "Bull mode: holding until 3 PM", "NONE"
        else:
            should_exit, reason, exit_type = evaluate_v_exit(symbol, position, context)
            # Block signal-based exits before 10:15 AM — only allow stop loss hits
            if should_exit and not signal_exits_allowed:
                entry_price = position.get("entry_price", 0)
                stop = position.get("stop_loss", entry_price * (1 - STOP_LOSS_PERCENT / 100))
                current_price = context.get("current_price", 0)
                if current_price > stop:
                    actions_taken.append(f"HOLD {symbol}: Signal exit blocked before 10:15 AM ({reason})")
                    should_exit = False

        if should_exit:
            # Determine quantity based on exit type
            full_qty = position.get("quantity", position.get("shares", 0))
            if exit_type == "FULL":
                qty = full_qty
            elif exit_type == "PARTIAL_70":
                qty = max(1, int(full_qty * 0.7))
            elif exit_type == "PARTIAL_50":
                qty = max(1, int(full_qty * 0.5))
            elif exit_type == "PARTIAL_30":
                qty = max(1, int(full_qty * 0.3))
            else:
                qty = full_qty
            
            result = execute_sell(
                symbol=symbol,
                quantity=qty,
                price=context["current_price"],
                reason=f"APEX: {reason}"
            )
            
            if result["success"]:
                actions_taken.append(f"SOLD {qty} {symbol} ({exit_type}): {reason}")
            else:
                actions_taken.append(f"SELL FAILED {symbol}: {result['error']}")
    
    # Track what we sold this cycle (don't buy back immediately)
    sold_this_cycle = set()
    for action in actions_taken:
        if action.startswith("SOLD"):
            # Extract symbol from "SOLD X US.SYMBOL..."
            parts = action.split()
            for p in parts:
                if p.startswith("US."):
                    sold_this_cycle.add(p)
                    break
    
    # ─── MARKET REGIME DETECTION ────────────────────────────────
    # Classify market regime and adjust entry rules accordingly.
    # Regime levels:
    #   NORMAL     — trade as usual
    #   CAUTION    — SPY down >1% OR signal-based bearish → HIGH_CONVICTION only
    #   DEFENSIVE  — SPY down >1.5% AND signal-based bearish → halt new entries
    spy_change = 0.0
    spy_snap_ctx = None
    try:
        spy_snap_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        ret_spy, spy_data = spy_snap_ctx.get_market_snapshot(["US.SPY"])
        if ret_spy == 0 and len(spy_data) > 0:
            row = spy_data.iloc[0]
            if row["prev_close_price"] > 0:
                spy_change = ((row["last_price"] - row["prev_close_price"]) / row["prev_close_price"]) * 100
    except Exception as e:
        print(f"Market regime: could not fetch SPY — {e}")
    finally:
        if spy_snap_ctx:
            spy_snap_ctx.close()

    spy_signals = get_recent_signals("US.SPY", hours=4)
    spy_sell_count = sum(1 for s in spy_signals if s.get("direction") == "SELL")
    spy_buy_count  = sum(1 for s in spy_signals if s.get("direction") == "BUY")
    signal_bearish = spy_sell_count >= 2 and spy_buy_count == 0

    # FIXED: Don't rely on broken MMTS signals for regime detection
    # Use SPY price action alone — more reliable
    if spy_change <= -1.5:
        market_regime = "DEFENSIVE"  # SPY down 1.5%+ = halt new entries
    elif spy_change <= -0.8 or signal_bearish:
        market_regime = "CAUTION"    # SPY down 0.8%+ = high conviction only
    else:
        market_regime = "NORMAL"

    if market_regime == "DEFENSIVE":
        actions_taken.append(
            f"ENTRIES HALTED: Defensive regime — SPY {spy_change:+.2f}% + {spy_sell_count} sell signals"
        )
        return actions_taken if actions_taken else [f"No actions (defensive regime: SPY {spy_change:+.2f}%)"]

    # ─── CHECK ENTRIES (with APEX confluence) ───────────────────
    # Time of day filter - no new entries during volatile periods
    is_safe_time, time_reason = is_safe_trading_time()
    if not is_safe_time:
        actions_taken.append(f"ENTRIES PAUSED: {time_reason}")
        # Still check exits above, just skip entries
        if actions_taken and actions_taken[-1].startswith("ENTRIES PAUSED"):
            pass  # Continue to send any exit actions
        return actions_taken if actions_taken else [f"No actions (entries paused: {time_reason})"]
    
    for symbol in ALLOWED_STOCKS:
        # Get fresh state
        state = load_state()
        
        # Skip if already in position
        if symbol in state.get("positions", {}):
            continue
        
        # Skip if we just sold this stock (avoid churning)
        if symbol in sold_this_cycle:
            actions_taken.append(f"SKIP {symbol}: Just sold, avoiding churn")
            continue
        
        # Evaluate using APEX system
        should_buy, reason, position_size_pct, score = evaluate_v_entry(symbol, state)
        
        if not should_buy:
            continue

        # ── CAUTION regime: only HIGH_CONVICTION trades allowed ──
        if market_regime == "CAUTION":
            signals_for_regime = get_recent_signals(symbol, hours=8)
            ctx_for_regime = get_market_context(symbol)
            conf_for_regime = calculate_confluence(signals_for_regime, ctx_for_regime)
            if conf_for_regime.trade_type.value != "high_conviction":
                actions_taken.append(
                    f"SKIP {symbol}: Caution regime — only HIGH_CONVICTION allowed "
                    f"(SPY {spy_change:+.2f}%)"
                )
                continue
        
        # Blacklist check — require manual approval in #approvals
        is_blocked, bl_reason, has_pending = is_blacklisted(symbol)
        if is_blocked:
            if has_pending:
                # Already waiting for approval
                actions_taken.append(f"SKIP {symbol}: Blacklisted — approval pending in #approvals")
            else:
                # Send new approval request
                ctx_bl = get_market_context(symbol)
                bl_price = ctx_bl.get('current_price', prices.get(symbol, 0))
                request_blacklist_approval(symbol, reason, score, [], bl_price)
                actions_taken.append(
                    f"SKIP {symbol}: Blacklisted ({bl_reason}) — approval requested in #approvals"
                )
            continue

        # Research validation check
        if ENABLE_RESEARCH_CHECK:
            research_valid, research_reason = quick_research_check(symbol)
            if not research_valid:
                actions_taken.append(f"SKIP {symbol}: Research check failed — {research_reason}")
                continue
            # Append research status to reason
            reason = f"{reason} | {research_reason}"
        
        # Get current price
        current_price = prices.get(symbol, 0)
        if current_price == 0:
            ctx = get_market_context(symbol)
            current_price = ctx.get("current_price", 0)
        
        if current_price == 0:
            actions_taken.append(f"SKIP {symbol}: Could not get price")
            continue
        
        # Calculate position size using APEX sizing
        available = state.get("current_cash", 0) - MIN_CASH_RESERVE
        position_value = available * (position_size_pct / 100)
        
        # Enforce minimum position size
        if position_value < MIN_POSITION_SIZE:
            actions_taken.append(f"SKIP {symbol}: Position ${position_value:.0f} below minimum ${MIN_POSITION_SIZE:.0f}")
            continue
        
        quantity = int(position_value / current_price)
        
        if quantity <= 0:
            continue
        
        # Map score to old confidence format for backward compatibility
        if score >= 12:
            confidence = 5
        elif score >= 9:
            confidence = 4
        elif score >= 6:
            confidence = 3
        elif score >= 4:
            confidence = 2
        else:
            confidence = 1
        
        # Get trade type from the APEX evaluation
        signals = get_recent_signals(symbol, hours=8)
        context_for_type = get_market_context(symbol)
        confluence_for_type = calculate_confluence(signals, context_for_type)
        trade_type_enum = confluence_for_type.trade_type
        
        # Execute buy with trade type
        result = execute_buy(
            symbol=symbol,
            quantity=quantity,
            price=current_price,
            reason=reason,
            confidence=confidence,
            trade_type=trade_type_enum.value
        )
        
        if result["success"]:
            # Tag bull mode entries in position data
            if bull_mode_active:
                state = load_state()
                if symbol in state.get("positions", {}):
                    state["positions"][symbol]["bull_mode_entry"] = True
                    from trading_bot import save_state
                    save_state(state)
            actions_taken.append(f"BOUGHT {quantity} {symbol} @ ${current_price:.2f} (score={score}): {reason}")
        else:
            actions_taken.append(f"BUY FAILED {symbol}: {result['error']}")
    
    return actions_taken

# ═══════════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════════

def generate_daily_report() -> str:
    """Generate end-of-day trading report."""
    state = load_state()
    
    # Get position values
    position_symbols = list(state.get("positions", {}).keys())
    prices = get_all_prices(position_symbols) if position_symbols else {}
    
    unrealized_pnl = 0
    positions_detail = ""
    
    for symbol, pos in state.get("positions", {}).items():
        entry = pos.get("entry_price", pos.get("cost_price", 0))
        qty = pos.get("quantity", pos.get("shares", 0))
        current_price = prices.get(symbol, entry)
        pos_pnl = (current_price - entry) * qty
        unrealized_pnl += pos_pnl
        
        emoji = "🟢" if pos_pnl > 0 else "🔴"
        positions_detail += f"\n{emoji} {symbol.replace('US.', '')}: {qty} @ ${entry:.2f} → ${current_price:.2f} ({pos_pnl:+.2f})"
    
    if not positions_detail:
        positions_detail = "\nNo open positions"
    
    total_pnl = state.get("total_pnl", 0) + unrealized_pnl
    win_rate = 0
    total_closed = state.get("winning_trades", 0) + state.get("losing_trades", 0)
    if total_closed > 0:
        win_rate = state["winning_trades"] / total_closed * 100
    
    return f"""
📊 **APEX Trading Report**
{datetime.now().strftime('%Y-%m-%d %H:%M')}

**Capital:** ${state.get('current_cash', 0):.2f}
**Total P&L:** ${total_pnl:+.2f}

**Today:**
• Trades: {state.get('daily_trades', 0)}
• Realized P&L: ${state.get('daily_pnl', 0):+.2f}

**All Time:**
• Total Trades: {state.get('total_trades', 0)}
• Win Rate: {win_rate:.1f}%

**Open Positions ({len(state.get('positions', {}))} / {MAX_POSITIONS}):**{positions_detail}

**System:** APEX (Confluence + Relative Strength)
**Status:** {'🔴 HALTED - ' + state.get('halt_reason', '') if state.get('halted') else '🟢 Active'}
"""

# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if "--report" in sys.argv:
        print(generate_daily_report())
    else:
        print("═══ APEX Trading Cycle ═══")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Min Score: {MIN_BUY_SCORE} | Min Indicators: {MIN_INDICATORS}")
        print()
        
        actions = run_trading_cycle()
        
        if actions:
            print("Actions:")
            for action in actions:
                print(f"  • {action}")
        else:
            print("No actions taken.")
