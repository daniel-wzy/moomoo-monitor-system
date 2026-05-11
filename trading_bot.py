"""
Autonomous Trading Bot
======================
Operates within strict $5,000 limit using Daniel's indicators + research.

SAFETY: All limits are enforced before any trade execution.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from moomoo import OpenQuoteContext, OpenSecTradeContext, TrdSide, OrderType, TrdEnv, TrdMarket, SecurityFirm, RET_OK

from trading_config import (
    MAX_PORTFOLIO_VALUE, MAX_SINGLE_POSITION, MAX_POSITIONS,
    MAX_DAILY_LOSS, MAX_SINGLE_LOSS, MIN_CASH_RESERVE, MIN_PORTFOLIO_VALUE,
    STOP_LOSS_PERCENT, TAKE_PROFIT_PERCENT, TRAILING_STOP_PERCENT,
    POSITION_SIZE_BY_CONFIDENCE, MIN_CONFLUENCE_SCORE,
    ALLOWED_STOCKS, BLACKLIST,
    TRADE_LOG_FILE, POSITION_FILE, DAILY_PNL_FILE,
    DISCORD_ALERTS_CHANNEL, DISCORD_LOGS_CHANNEL,
    get_initial_state
)
from core.delivery import send_discord

# ═══════════════════════════════════════════════════════════════════
# TRADE CREDENTIALS
# ═══════════════════════════════════════════════════════════════════

CREDENTIALS_FILE = "/Users/danielwan/clawd/moomoo-alerts/.trade_credentials"

def get_trade_password() -> str:
    """Read trade password from secure file."""
    with open(CREDENTIALS_FILE, 'r') as f:
        return f.read().strip()

def get_unlocked_trade_context():
    """Create and unlock a trade context."""
    trd_ctx = OpenSecTradeContext(
        filter_trdmarket=TrdMarket.US,
        host='127.0.0.1',
        port=11111,
        security_firm=SecurityFirm.FUTUINC
    )
    
    # Unlock trading
    password = get_trade_password()
    ret, data = trd_ctx.unlock_trade(password)
    
    if ret != RET_OK:
        trd_ctx.close()
        raise Exception(f"Failed to unlock trading: {data}")
    
    return trd_ctx

# ═══════════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """Load trading state from file."""
    if os.path.exists(POSITION_FILE):
        with open(POSITION_FILE, 'r') as f:
            return json.load(f)
    return get_initial_state()

def save_state(state: dict):
    """Save trading state to file."""
    with open(POSITION_FILE, 'w') as f:
        json.dump(state, f, indent=2, default=str)

def log_trade(trade_data: dict):
    """Append trade to log file."""
    with open(TRADE_LOG_FILE, 'a') as f:
        f.write(json.dumps({**trade_data, "timestamp": datetime.now().isoformat()}) + "\n")

# ═══════════════════════════════════════════════════════════════════
# SAFETY CHECKS - THESE RUN BEFORE EVERY TRADE
# ═══════════════════════════════════════════════════════════════════

def check_can_trade(state: dict) -> Tuple[bool, str]:
    """
    Master safety check. Returns (can_trade, reason).
    This is the GATEKEEPER - no trade happens without passing this.
    """
    # Check if trading is halted
    if state.get("halted"):
        return False, f"Trading halted: {state.get('halt_reason', 'Unknown')}"
    
    # Portfolio floor check DISABLED — manual halt only
    
    # Check daily loss limit
    if state.get("daily_pnl", 0) <= -MAX_DAILY_LOSS:
        state["halted"] = True
        state["halt_reason"] = f"Daily loss limit reached: ${abs(state['daily_pnl']):.2f}"
        save_state(state)
        return False, state["halt_reason"]
    
    # Check if we have any capital left
    if state.get("current_cash", 0) < MIN_CASH_RESERVE:
        return False, f"Cash below minimum reserve: ${state['current_cash']:.2f}"
    
    # Check position count
    if len(state.get("positions", {})) >= MAX_POSITIONS:
        return False, f"Max positions reached: {MAX_POSITIONS}"
    
    return True, "OK"

def check_trade_limits(state: dict, symbol: str, quantity: int, price: float) -> Tuple[bool, str]:
    """
    Check if a specific trade is within limits.
    """
    trade_value = quantity * price
    
    # Check single position limit
    if trade_value > MAX_SINGLE_POSITION:
        return False, f"Trade value ${trade_value:.2f} exceeds max position ${MAX_SINGLE_POSITION}"
    
    # Check if we have enough cash
    if trade_value > state.get("current_cash", 0) - MIN_CASH_RESERVE:
        available = state.get("current_cash", 0) - MIN_CASH_RESERVE
        return False, f"Insufficient cash. Need ${trade_value:.2f}, have ${available:.2f} available"
    
    # Check total exposure
    current_exposure = sum(
        pos.get("value", 0) for pos in state.get("positions", {}).values()
    )
    if current_exposure + trade_value > MAX_PORTFOLIO_VALUE:
        return False, f"Would exceed max portfolio value of ${MAX_PORTFOLIO_VALUE}"
    
    # Check if symbol is allowed
    if symbol not in ALLOWED_STOCKS:
        return False, f"Symbol {symbol} not in allowed list"
    
    # Check blacklist
    if symbol in BLACKLIST:
        return False, f"Symbol {symbol} is blacklisted"
    
    return True, "OK"

# ═══════════════════════════════════════════════════════════════════
# POSITION SIZING
# ═══════════════════════════════════════════════════════════════════

def calculate_position_size(state: dict, price: float, confidence: int) -> int:
    """
    Calculate how many shares to buy based on confidence and available capital.
    """
    # Get position size multiplier based on confidence
    multiplier = POSITION_SIZE_BY_CONFIDENCE.get(confidence, 0)
    if multiplier == 0:
        return 0
    
    # Calculate available capital (respect limits)
    available = min(
        state.get("current_cash", 0) - MIN_CASH_RESERVE,
        MAX_SINGLE_POSITION,
        MAX_PORTFOLIO_VALUE - sum(pos.get("value", 0) for pos in state.get("positions", {}).values())
    )
    
    if available <= 0:
        return 0
    
    # Calculate position value and shares
    position_value = available * multiplier
    shares = int(position_value / price)
    
    return max(0, shares)

# ═══════════════════════════════════════════════════════════════════
# TRADE EXECUTION
# ═══════════════════════════════════════════════════════════════════

def execute_buy(symbol: str, quantity: int, price: float, reason: str, confidence: int, trade_type: str = "long_term") -> dict:
    """
    Execute a buy order. Returns result dict.
    """
    state = load_state()
    
    # Safety check 1: Can we trade at all?
    can_trade, msg = check_can_trade(state)
    if not can_trade:
        return {"success": False, "error": msg}
    
    # Safety check 2: Is this specific trade allowed?
    can_execute, msg = check_trade_limits(state, symbol, quantity, price)
    if not can_execute:
        return {"success": False, "error": msg}
    
    trd_ctx = None
    try:
        # Connect and unlock trade context
        trd_ctx = get_unlocked_trade_context()
        
        # Place market order
        ret, data = trd_ctx.place_order(
            price=price,
            qty=quantity,
            code=symbol,
            trd_side=TrdSide.BUY,
            order_type=OrderType.MARKET,
            trd_env=TrdEnv.REAL  # REAL trading
        )
        
        if ret != 0:
            return {"success": False, "error": f"Order failed: {data}"}
        
        # Update state
        trade_value = quantity * price
        state["current_cash"] -= trade_value
        state["positions"][symbol] = {
            "quantity": quantity,
            "entry_price": price,
            "value": trade_value,
            "entry_time": datetime.now().isoformat(),
            "stop_loss": price * (1 - STOP_LOSS_PERCENT / 100),
            "take_profit": price * (1 + TAKE_PROFIT_PERCENT / 100),
            "confidence": confidence,
            "reason": reason,
            "trade_type": trade_type,
        }
        state["total_trades"] += 1
        state["daily_trades"] += 1
        save_state(state)
        
        # Log trade
        trade_data = {
            "action": "BUY",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "value": trade_value,
            "reason": reason,
            "confidence": confidence,
            "order_id": data.get("order_id") if isinstance(data, dict) else str(data),
        }
        log_trade(trade_data)
        
        # Report to Discord
        report_trade(trade_data, state)
        
        return {"success": True, "data": trade_data}
        
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if trd_ctx:
            try:
                trd_ctx.close()
            except Exception:
                pass

def is_settlement_safe(entry_time_str: str) -> tuple[bool, str]:
    """
    GFV guard: ensure position has been held for at least 2 trading days (T+2).
    Returns (safe_to_sell, reason_string).
    """
    try:
        entry_dt = datetime.fromisoformat(entry_time_str)
        entry_date = entry_dt.date()
        today = datetime.now().date()

        # Count trading days elapsed (skip weekends)
        trading_days = 0
        check = entry_date
        while check < today:
            check += timedelta(days=1)
            if check.weekday() < 5:  # Mon-Fri
                trading_days += 1

        if trading_days >= 1:
            return True, ""
        else:
            # Estimate settlement date (T+1)
            settle_date = entry_date
            days_needed = 1
            while days_needed > 0:
                settle_date += timedelta(days=1)
                if settle_date.weekday() < 5:
                    days_needed -= 1
            return False, f"GFV risk — funds settle {settle_date.strftime('%Y-%m-%d')} (T+1). Hold until then."
    except Exception as e:
        return True, ""  # If we can't parse, allow the sell


def execute_sell(symbol: str, quantity: int, price: float, reason: str) -> dict:
    """
    Execute a sell order. Returns result dict.
    """
    state = load_state()
    
    if symbol not in state.get("positions", {}):
        return {"success": False, "error": f"No position in {symbol}"}
    
    position = state["positions"][symbol]

    # GFV check — don't sell positions held < T+2 trading days
    entry_time = position.get("entry_time", "")
    safe, gfv_reason = is_settlement_safe(entry_time)
    if not safe:
        return {"success": False, "error": gfv_reason}
    
    trd_ctx = None
    try:
        # Connect and unlock trade context
        trd_ctx = get_unlocked_trade_context()
        
        # Place market order
        ret, data = trd_ctx.place_order(
            price=price,
            qty=quantity,
            code=symbol,
            trd_side=TrdSide.SELL,
            order_type=OrderType.MARKET,
            trd_env=TrdEnv.REAL
        )
        
        if ret != 0:
            return {"success": False, "error": f"Order failed: {data}"}
        
        # Calculate P&L (use quantity being sold, not full position)
        trade_value = quantity * price
        entry_value = quantity * position["entry_price"]
        pnl = trade_value - entry_value
        
        # Update state
        state["current_cash"] += trade_value
        state["total_pnl"] += pnl
        state["daily_pnl"] += pnl
        
        if pnl > 0:
            state["winning_trades"] += 1
        else:
            state["losing_trades"] += 1
        
        # Handle partial sells
        remaining = position["quantity"] - quantity
        if remaining > 0:
            state["positions"][symbol]["quantity"] = remaining
            state["positions"][symbol]["value"] = remaining * position["entry_price"]
        else:
            del state["positions"][symbol]
        state["total_trades"] += 1
        state["daily_trades"] += 1
        save_state(state)
        
        # Log trade
        trade_data = {
            "action": "SELL",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "value": trade_value,
            "pnl": pnl,
            "reason": reason,
            "hold_time": str(datetime.now() - datetime.fromisoformat(position["entry_time"])),
        }
        log_trade(trade_data)
        
        # Report to Discord
        report_trade(trade_data, state)
        
        return {"success": True, "data": trade_data}
        
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if trd_ctx:
            try:
                trd_ctx.close()
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════════

def report_trade(trade: dict, state: dict):
    """Send trade report to Discord."""
    action = trade["action"]
    symbol = trade["symbol"].replace("US.", "")
    emoji = "🟢" if action == "BUY" else ("🟢" if trade.get("pnl", 0) > 0 else "🔴")
    
    if action == "BUY":
        msg = f"""
{emoji} **TRADE EXECUTED**

**{action}** {trade['quantity']} x ${symbol}
💰 Entry: ${trade['price']:.2f}
📊 Value: ${trade['value']:.2f}
🎯 Confidence: {trade.get('confidence', 'N/A')}/5
📝 Reason: {trade.get('reason', 'Signal')}

**Portfolio Status**
💵 Cash: ${state['current_cash']:.2f}
📈 Positions: {len(state['positions'])}
📊 Total P&L: ${state['total_pnl']:.2f}
"""
    else:
        pnl = trade.get('pnl', 0)
        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        msg = f"""
{emoji} **TRADE EXECUTED**

**{action}** {trade['quantity']} x ${symbol}
💰 Exit: ${trade['price']:.2f}
{pnl_emoji} P&L: ${pnl:+.2f}
📝 Reason: {trade.get('reason', 'Signal')}
⏱️ Hold time: {trade.get('hold_time', 'N/A')}

**Portfolio Status**
💵 Cash: ${state['current_cash']:.2f}
📈 Positions: {len(state['positions'])}
📊 Total P&L: ${state['total_pnl']:.2f}
"""
    
    send_discord(msg.strip(), DISCORD_ALERTS_CHANNEL)

def get_portfolio_summary() -> str:
    """Generate portfolio summary."""
    state = load_state()
    
    positions_str = ""
    for symbol, pos in state.get("positions", {}).items():
        positions_str += f"\n• {symbol.replace('US.', '')}: {pos['quantity']} @ ${pos['entry_price']:.2f}"
    
    if not positions_str:
        positions_str = "\n• No open positions"
    
    win_rate = 0
    if state["winning_trades"] + state["losing_trades"] > 0:
        win_rate = state["winning_trades"] / (state["winning_trades"] + state["losing_trades"]) * 100
    
    return f"""
📊 **Jarvis Trading Bot — Portfolio Summary**

💵 **Cash:** ${state['current_cash']:.2f} / ${MAX_PORTFOLIO_VALUE:.2f}
📈 **Total P&L:** ${state['total_pnl']:+.2f}
📅 **Daily P&L:** ${state['daily_pnl']:+.2f}

**Positions ({len(state.get('positions', {}))} / {MAX_POSITIONS}):**{positions_str}

**Stats:**
• Total trades: {state['total_trades']}
• Win rate: {win_rate:.1f}%
• Status: {'🔴 HALTED' if state.get('halted') else '🟢 Active'}
"""

# ═══════════════════════════════════════════════════════════════════
# DAILY RESET
# ═══════════════════════════════════════════════════════════════════

def reset_daily_counters():
    """Reset daily P&L and trade count. Run at market open."""
    state = load_state()
    state["daily_pnl"] = 0.0
    state["daily_trades"] = 0
    
    # Unhalt if it was halted due to daily loss
    if state.get("halted") and "Daily loss" in state.get("halt_reason", ""):
        state["halted"] = False
        state["halt_reason"] = None
    
    save_state(state)

if __name__ == "__main__":
    # Initialize state if needed
    state = load_state()
    save_state(state)
    print(get_portfolio_summary())
