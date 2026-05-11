"""
Alert dispatcher — formats, logs, and delivers signals via Telegram.
"""
import json
import os
from datetime import datetime
from core.delivery import send_alert

ALERT_LOG = os.path.join(os.path.dirname(__file__), "..", "alert_log.jsonl")
PENDING_ALERTS = os.path.join(os.path.dirname(__file__), "..", "pending_alerts.json")


def format_alert(ticker: str, timeframe: str, indicator: str, signal: dict) -> str:
    """Format a signal into a human-readable alert message."""
    direction = signal["direction"]
    emoji = signal["emoji"]
    desc = signal["description"]
    close = signal["close"]
    hma_state = signal["hma_state"]
    trend = signal["trend"]

    msg = (
        f"{emoji} {direction} SIGNAL — {ticker} ({timeframe})\n"
        f"📊 Indicator: {indicator}\n"
        f"💡 {desc}\n"
        f"💰 Price: ${close:.2f}\n"
        f"📈 Momentum: {hma_state}\n"
        f"🔀 Trend: {trend}\n"
    )

    if direction == "BUY" and signal.get("stop_long"):
        msg += f"🛑 Stop Loss: ${signal['stop_long']:.2f}\n"
    elif direction == "SELL" and signal.get("stop_short"):
        msg += f"🛑 Resistance: ${signal['stop_short']:.2f}\n"

    msg += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    return msg


def dispatch_alert(ticker: str, timeframe: str, indicator: str, signal: dict):
    """
    Log alert + send to Telegram via Clawdbot.
    """
    alert = {
        "ticker": ticker,
        "timeframe": timeframe,
        "indicator": indicator,
        "signal_name": signal["signal"],
        "direction": signal["direction"],
        "close": signal["close"],
        "hma_state": signal["hma_state"],
        "trend": signal["trend"],
        "message": format_alert(ticker, timeframe, indicator, signal),
        "timestamp": datetime.now().isoformat(),
        "delivered": False,
    }

    # Append to log
    with open(ALERT_LOG, "a") as f:
        f.write(json.dumps(alert) + "\n")

    # Write to pending
    pending = []
    if os.path.exists(PENDING_ALERTS):
        try:
            with open(PENDING_ALERTS) as f:
                pending = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pending = []

    pending.append(alert)

    with open(PENDING_ALERTS, "w") as f:
        json.dump(pending, f, indent=2)

    # Print to console
    print(f"\n{'='*60}")
    print(alert["message"])
    print(f"{'='*60}\n")

    # ── Deliver to Telegram + iMessage ──────────────────────
    # Use enhanced message if available (includes confluence + squeeze)
    msg = signal.get("enhanced_message", alert["message"])
    status = send_alert(msg)
    alert["delivered"] = status

    return alert
