"""
Autonomous Trading Bot Configuration
=====================================
FULL PORTFOLIO ACCESS - Updated 2026-03-04

This bot operates with full autonomy within these hard limits.
All trades are logged and reported to Discord.
Loss limit: $2,000 max drawdown from starting value.
"""

from datetime import datetime

# ═══════════════════════════════════════════════════════════════════
# HARD LIMITS - THESE ARE NON-NEGOTIABLE
# ═══════════════════════════════════════════════════════════════════

MAX_PORTFOLIO_VALUE = 20000.00    # Absolute maximum exposure
MAX_SINGLE_POSITION = 6000.00     # Max value per single stock (~35% of portfolio)
MAX_POSITIONS = 5                  # Maximum concurrent positions (concentrated bets)
MIN_POSITION_SIZE = 2000.00       # Don't enter if position would be smaller than this
MAX_DAILY_LOSS = 99999.00         # Disabled - only hard stop is MIN_PORTFOLIO_VALUE
MAX_SINGLE_LOSS = 300.00          # Max loss per position before stop-loss triggers
MIN_CASH_RESERVE = 1000.00        # Always keep $1k cash buffer
MIN_PORTFOLIO_VALUE = 15347.60    # STOP TRADING if portfolio drops below this ($2k loss limit)

# ═══════════════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

STOP_LOSS_PERCENT = 5.0           # Auto-sell if position drops 5%
TAKE_PROFIT_PERCENT = 15.0        # Consider taking profit at 15% gain
TRAILING_STOP_PERCENT = 3.0       # Trailing stop once in profit

# Position sizing based on signal strength
POSITION_SIZE_BY_CONFIDENCE = {
    5: 0.25,    # 5/5 confidence: use 25% of available capital
    4: 0.20,    # 4/5 confidence: use 20%
    3: 0.15,    # 3/5 confidence: use 15%
    2: 0.00,    # 2/5 or below: don't trade
    1: 0.00,
}

# ═══════════════════════════════════════════════════════════════════
# TRADING RULES
# ═══════════════════════════════════════════════════════════════════

# Only trade these hours (EST)
TRADING_HOURS_START = 9           # 9:30 AM market open
TRADING_HOURS_END = 16            # 4:00 PM market close

# Minimum signal requirements
MIN_CONFLUENCE_SCORE = 3          # Need at least 3/5 indicator agreement
MIN_INDICATORS_ALIGNED = 2        # At least 2 indicators must agree

# Indicators to use (from existing system)
INDICATORS = [
    "MMTS",      # Main trend signal
    "LDZN",      # Low-point zone
    "KDZS",      # Key decision zone signal
    "ZIG",       # Zigzag reversal
    "RSI",       # Overbought/oversold
    "MACD",      # Momentum
]

# Stock universe - only trade these
# Full watchlist - if we scan it, we can trade it
# Quality control is via signal confluence, not stock restrictions
ALLOWED_STOCKS = [
    # Mega Cap Tech
    "US.NVDA", "US.AAPL", "US.MSFT", "US.GOOG", "US.GOOGL", "US.AMZN",
    "US.META", "US.TSLA",
    # Semiconductors
    "US.AMD", "US.AVGO", "US.TSM", "US.ARM", "US.MU", "US.QCOM",
    "US.INTC", "US.MRVL",
    # AI / Cloud / Software
    "US.PLTR", "US.CRWD", "US.ANET", "US.SNOW", "US.MDB", "US.NET",
    "US.DDOG", "US.SHOP", "US.DUOL", "US.PATH",
    # Fintech / Crypto
    "US.COIN", "US.SOFI", "US.MSTR", "US.PYPL", "US.HOOD",  # US.SQ removed (Block ticker changed)
    # Energy / Nuclear / AI Power
    "US.OKLO", "US.CEG", "US.BW", "US.EOSE", "US.VST",
    # Photonics / Optical
    "US.COHR", "US.AAOI",
    # Pharma / Healthcare
    "US.LLY", "US.NVO",
    # Consumer / Media
    "US.NFLX", "US.DIS", "US.UBER", "US.ABNB",
    # High Beta / Small Cap
    "US.RKLB", "US.ALOY", "US.BBAI", "US.CIFR", "US.HIMS", "US.MP",
    "US.OPEN", "US.OTEX", "US.ROKU", "US.SNAP",
]

# Blacklist - never trade these (too volatile/risky)
BLACKLIST = [
    "US.GME",
    "US.AMC", 
    "US.BBBY",
]

# ═══════════════════════════════════════════════════════════════════
# LOGGING & REPORTING
# ═══════════════════════════════════════════════════════════════════

# Discord channels for reporting
DISCORD_ALERTS_CHANNEL = "channel:1476517108996374659"      # #alerts
DISCORD_LOGS_CHANNEL = "channel:1476517085273264170"        # #logs
DISCORD_BUFFETT_CHANNEL = "channel:PENDING"                 # #buffett - will be updated

# Log files
TRADE_LOG_FILE = "/Users/danielwan/clawd/moomoo-alerts/trade_log.jsonl"
POSITION_FILE = "/Users/danielwan/clawd/moomoo-alerts/positions.json"
DAILY_PNL_FILE = "/Users/danielwan/clawd/moomoo-alerts/daily_pnl.json"

# ═══════════════════════════════════════════════════════════════════
# STATE TRACKING
# ═══════════════════════════════════════════════════════════════════

def get_initial_state():
    """Initial state for the trading bot."""
    return {
        "enabled": True,
        "started_at": datetime.now().isoformat(),
        "initial_capital": MAX_PORTFOLIO_VALUE,
        "current_cash": MAX_PORTFOLIO_VALUE,
        "positions": {},
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": 0.0,
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "halted": False,
        "halt_reason": None,
    }
