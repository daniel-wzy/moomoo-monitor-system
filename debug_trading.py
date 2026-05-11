#!/usr/bin/env python3
"""Debug script to trace trading decisions."""

from trading_decisions import get_latest_signals, should_buy
from trading_bot import load_state
from trading_config import ALLOWED_STOCKS

state = load_state()
signals = get_latest_signals()

print(f"State: capital=${state.get('capital', 0)}, positions={len(state.get('positions', {}))}")
print(f"Checking {len(signals)} signals against {len(ALLOWED_STOCKS)} allowed stocks\n")

for symbol in ALLOWED_STOCKS:
    if symbol in signals:
        signal = signals[symbol]
        should_enter, reason = should_buy(signal, state)
        status = "✅ BUY" if should_enter else "❌ SKIP"
        print(f"{symbol}: type={signal['signal_type']} conf={signal['confluence']} -> {status}: {reason}")
    else:
        print(f"{symbol}: No signal")
