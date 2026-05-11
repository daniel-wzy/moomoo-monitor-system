#!/usr/bin/env python3
"""
4-Hour Bar Scanner
==================
Runs every 4 hours during market hours (10 AM, 2 PM ET).
Fetches current 4h candle data and runs all indicators.
Fires alerts exactly as the main monitor does.

Called by cron — not by monitor_push.py.
"""

import sys
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.data import get_quote_context, fetch_candles
from core.alerts import dispatch_alert, format_alert
from core.confluence import ConfluenceEngine
from indicators import ldzn, mmts, lddx, mabs, rscdjc, kdzs, zj
from indicators import bb_squeeze
from config import WATCHLIST

EST = ZoneInfo("America/New_York")

INDICATORS = {
    "LDZN":   ldzn,
    "MMTS":   mmts,
    "LDDX":   lddx,
    "MABS":   mabs,
    "RSCDJC": rscdjc,
    "KDZS":   kdzs,
    "ZJ":     zj,
}

FIRED_ALERTS = set()


def now_est():
    return datetime.now(EST)


def scan_4h(watchlist: list):
    t = now_est()
    today = t.date()

    print(f"\n{'═'*60}")
    print(f"📊 4H SCAN — {t.strftime('%Y-%m-%d %H:%M')} ET")
    print(f"{'═'*60}")

    confluence = ConfluenceEngine()
    ctx = get_quote_context()

    alerts_fired = 0

    for ticker in watchlist:
        try:
            df = fetch_candles(ctx, ticker, "4h")
            if df is None or len(df) < 20:
                continue

            # The last completed 4h bar is iloc[-2] (current bar still forming)
            # unless it's after market close, in which case iloc[-1] is complete
            completed = df.iloc[-2]
            import pandas as pd
            bar_date = pd.to_datetime(completed["time"]).date()

            # Skip if bar is from a previous session
            if bar_date != today:
                # Check if it's today but 4h block started yesterday
                # 4h blocks: 9:30-13:30, 13:30-16:00
                # Allow bars from today's session
                pass

            bar_time = str(completed["time"])

            try:
                df_sq = bb_squeeze.compute(df)
                squeeze_ctx = bb_squeeze.get_squeeze_context(df_sq)
                squeeze_text = bb_squeeze.format_squeeze_context(squeeze_ctx)
            except Exception:
                squeeze_ctx = {}
                squeeze_text = ""

            found_signals = []
            for ind_name, ind_module in INDICATORS.items():
                try:
                    result = ind_module.compute(df)
                    signals = ind_module.get_signals(result)  # uses iloc[-2]
                except Exception as e:
                    continue

                for sig in signals:
                    key = (ticker, "4h", ind_name, sig["signal"], bar_time)
                    if key not in FIRED_ALERTS:
                        FIRED_ALERTS.add(key)

                        confluence.record_signal(
                            ticker, "4h", ind_name,
                            sig["signal"], sig["direction"], sig["close"]
                        )

                        conf = confluence.score_confluence(ticker, "4h", sig["direction"])
                        sig["confluence"] = conf
                        sig["squeeze"] = squeeze_ctx

                        base_msg = format_alert(ticker, "4h", ind_name, sig)
                        if squeeze_text:
                            base_msg += f"\n{squeeze_text}"

                        enhanced_msg = confluence.format_confluence_alert(
                            ticker, "4h", sig["direction"], base_msg
                        )
                        sig["enhanced_message"] = enhanced_msg

                        dispatch_alert(ticker, "4h", ind_name, sig)
                        found_signals.append(f"{ind_name}:{sig['signal']}({sig['direction']})")
                        alerts_fired += 1

            if found_signals:
                print(f"  🔔 {ticker.replace('US.','')} @ 4h: {', '.join(found_signals)}")

            time.sleep(0.5)  # Rate limit

        except Exception as e:
            print(f"  ❌ {ticker}: {e}")

    ctx.close()
    print(f"\n✅ 4H scan complete — {alerts_fired} alert(s) fired across {len(watchlist)} tickers")


if __name__ == "__main__":
    watchlist = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST
    scan_4h(watchlist)
