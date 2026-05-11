"""
Alert delivery via OpenClaw Gateway → Telegram + iMessage + Discord.

Sends alerts by invoking the message tool through the Gateway HTTP API.
"""
import json
import urllib.request
import urllib.error
import os

# OpenClaw Gateway config
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 18789
# Hardcoded to avoid stale env var issues
GATEWAY_TOKEN = "f331d99b46afbcea1065612af55d9256a3976d42725694a3"

# Daniel's contact info
TELEGRAM_TARGET = "8332147151"
IMESSAGE_TARGET = "+18573357988"

# Discord channel IDs
DISCORD_ALERTS_CHANNEL = "channel:1476517108996374659"
DISCORD_MARKET_BRIEF_CHANNEL = "channel:1476517132153127045"
DISCORD_BUFFETT_CHANNEL = "channel:1476788586740191337"

# Additional summary recipients (iMessage only)
SUMMARY_RECIPIENTS = [
    "+14376043080",
]

GATEWAY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/tools/invoke"


def _send_via_gateway(channel: str, target: str, message: str) -> bool:
    """Send a message through Clawdbot Gateway."""
    payload = {
        "tool": "message",
        "args": {
            "action": "send",
            "target": target,
            "channel": channel,
            "message": message,
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GATEWAY_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GATEWAY_TOKEN}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"  📨 {channel.capitalize()} delivered!")
                return True
            else:
                print(f"  ⚠️  {channel.capitalize()} response: {result}")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ❌ {channel.capitalize()} delivery failed ({e.code}): {body}")
        return False
    except Exception as e:
        print(f"  ❌ {channel.capitalize()} delivery error: {e}")
        return False


def send_telegram(message: str) -> bool:
    """Send a message to Daniel via Telegram."""
    return _send_via_gateway("telegram", TELEGRAM_TARGET, message)


def send_imessage(message: str) -> bool:
    """Send a message to Daniel via iMessage."""
    return _send_via_gateway("imessage", IMESSAGE_TARGET, message)


def send_discord(message: str, target: str = DISCORD_ALERTS_CHANNEL) -> bool:
    """Send a message to Discord."""
    return _send_via_gateway("discord", target, message)


def send_alert(message: str) -> dict:
    """Send a trade alert to Discord #alerts. Returns delivery status."""
    return {
        "discord": send_discord(message, DISCORD_ALERTS_CHANNEL),
    }


def send_summary(title: str, body: str) -> dict:
    """Send a formatted summary report — Discord #market-brief + iMessage to all recipients."""
    message = f"{title}\n\n{body}"
    results = {
        "discord": send_discord(message, DISCORD_MARKET_BRIEF_CHANNEL),
        "imessage_daniel": send_imessage(message),
    }
    for recipient in SUMMARY_RECIPIENTS:
        ok = _send_via_gateway("imessage", recipient, message)
        results[f"imessage_{recipient}"] = ok
    return results
