"""
Confluence Engine — Multi-indicator and multi-timeframe signal scoring.

Tracks signals across all indicators and timeframes to surface
high-conviction trades when multiple systems agree.

Scoring:
  1 indicator  = Normal alert
  2 indicators = Elevated (⚡)
  3+ indicators = High conviction (🔥)

Timeframe alignment:
  Same direction on 1 TF  = Standard
  Same direction on 2 TFs = Strong (📊)
  Same direction on 3 TFs = Maximum conviction (🎯)
"""
import json
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict

CONFLUENCE_STATE = os.path.join(os.path.dirname(__file__), "..", "confluence_state.json")

# How long signals stay in the buffer (seconds)
SIGNAL_WINDOW = {
    "30m": 3600,       # 1 hour — 2 candles
    "4h": 28800,       # 8 hours — 2 candles
    "daily": 172800,   # 2 days — 2 candles
}


class ConfluenceEngine:
    """Tracks and scores signal confluence across indicators and timeframes."""
    
    def __init__(self):
        self.signal_buffer = []  # list of recent signals
        self._load_state()
    
    def _load_state(self):
        if os.path.exists(CONFLUENCE_STATE):
            try:
                with open(CONFLUENCE_STATE) as f:
                    data = json.load(f)
                    self.signal_buffer = data.get("buffer", [])
            except (json.JSONDecodeError, FileNotFoundError):
                self.signal_buffer = []
        self._prune_buffer()
    
    def _save_state(self):
        with open(CONFLUENCE_STATE, "w") as f:
            json.dump({"buffer": self.signal_buffer}, f, indent=2)
    
    def _prune_buffer(self):
        """Remove expired signals from buffer."""
        now = time.time()
        max_window = max(SIGNAL_WINDOW.values())
        self.signal_buffer = [
            s for s in self.signal_buffer
            if now - s.get("timestamp", 0) < max_window
        ]
    
    def record_signal(self, ticker: str, timeframe: str, indicator: str,
                      signal_name: str, direction: str, close: float):
        """Record a new signal into the buffer."""
        entry = {
            "ticker": ticker,
            "timeframe": timeframe,
            "indicator": indicator,
            "signal": signal_name,
            "direction": direction,
            "close": close,
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self.signal_buffer.append(entry)
        self._prune_buffer()
        self._save_state()
    
    def score_confluence(self, ticker: str, timeframe: str, direction: str) -> dict:
        """
        Score confluence for a given ticker/timeframe/direction.
        
        Returns:
            {
                "indicator_count": int,     # how many indicators agree on this TF
                "indicators": [str],        # which indicators
                "timeframe_count": int,     # how many TFs have same direction
                "timeframes": [str],        # which TFs
                "conviction": str,          # "normal", "elevated", "high", "maximum"
                "emoji": str,               # visual indicator
                "score": int,               # 1-10 composite score
            }
        """
        now = time.time()
        
        # Find matching signals on same ticker + timeframe + direction
        window = SIGNAL_WINDOW.get(timeframe, 3600)
        same_tf = [
            s for s in self.signal_buffer
            if (s["ticker"] == ticker and
                s["timeframe"] == timeframe and
                s["direction"] == direction and
                now - s["timestamp"] < window)
        ]
        
        # Unique indicators on this TF
        indicators = list(set(s["indicator"] for s in same_tf))
        ind_count = len(indicators)
        
        # Find matching signals across ALL timeframes
        all_tf = [
            s for s in self.signal_buffer
            if (s["ticker"] == ticker and
                s["direction"] == direction and
                now - s["timestamp"] < max(SIGNAL_WINDOW.values()))
        ]
        
        # Unique timeframes with same direction
        timeframes = list(set(s["timeframe"] for s in all_tf))
        tf_count = len(timeframes)
        
        # Composite score (1-10)
        score = min(10, ind_count + (tf_count - 1) * 2)
        
        # Conviction level
        if ind_count >= 3 and tf_count >= 2:
            conviction = "maximum"
            emoji = "🎯"
        elif ind_count >= 3 or (ind_count >= 2 and tf_count >= 2):
            conviction = "high"
            emoji = "🔥"
        elif ind_count >= 2 or tf_count >= 2:
            conviction = "elevated"
            emoji = "⚡"
        else:
            conviction = "normal"
            emoji = ""
        
        return {
            "indicator_count": ind_count,
            "indicators": sorted(indicators),
            "timeframe_count": tf_count,
            "timeframes": sorted(timeframes),
            "conviction": conviction,
            "emoji": emoji,
            "score": score,
        }
    
    def format_confluence_alert(self, ticker: str, timeframe: str,
                                 direction: str, base_message: str) -> str:
        """
        Enhance an alert message with confluence scoring.
        Returns enhanced message if confluence detected, else original.
        """
        result = self.score_confluence(ticker, timeframe, direction)
        
        if result["conviction"] == "normal":
            return base_message
        
        # Build confluence header
        header = f"\n{result['emoji']} **CONFLUENCE: {result['conviction'].upper()}** (Score: {result['score']}/10)"
        
        details = []
        if result["indicator_count"] > 1:
            details.append(
                f"📊 {result['indicator_count']} indicators agree: "
                f"{', '.join(result['indicators'])}"
            )
        if result["timeframe_count"] > 1:
            details.append(
                f"⏱️ {result['timeframe_count']} timeframes align: "
                f"{', '.join(result['timeframes'])}"
            )
        
        return base_message + header + "\n" + "\n".join(details)
    
    def get_active_confluences(self) -> list[dict]:
        """
        Get all currently active high-conviction confluences.
        Useful for summary reports.
        """
        self._prune_buffer()
        now = time.time()
        
        # Group by ticker + direction
        groups = defaultdict(list)
        for s in self.signal_buffer:
            key = (s["ticker"], s["direction"])
            groups[key].append(s)
        
        confluences = []
        for (ticker, direction), signals in groups.items():
            indicators = list(set(s["indicator"] for s in signals))
            timeframes = list(set(s["timeframe"] for s in signals))
            
            if len(indicators) >= 2 or len(timeframes) >= 2:
                score = min(10, len(indicators) + (len(timeframes) - 1) * 2)
                
                if len(indicators) >= 3 and len(timeframes) >= 2:
                    conviction = "maximum"
                    emoji = "🎯"
                elif len(indicators) >= 3 or (len(indicators) >= 2 and len(timeframes) >= 2):
                    conviction = "high"
                    emoji = "🔥"
                else:
                    conviction = "elevated"
                    emoji = "⚡"
                
                confluences.append({
                    "ticker": ticker,
                    "direction": direction,
                    "indicators": sorted(indicators),
                    "timeframes": sorted(timeframes),
                    "conviction": conviction,
                    "emoji": emoji,
                    "score": score,
                    "signal_count": len(signals),
                    "latest": max(s["time_str"] for s in signals),
                })
        
        # Sort by score descending
        confluences.sort(key=lambda x: x["score"], reverse=True)
        return confluences
    
    def format_summary(self) -> str:
        """Generate a summary of all active confluences."""
        confluences = self.get_active_confluences()
        
        if not confluences:
            return "No active confluences."
        
        lines = ["**Active Confluences:**\n"]
        for c in confluences:
            lines.append(
                f"{c['emoji']} **{c['ticker']}** {c['direction']} "
                f"(Score: {c['score']}/10)\n"
                f"  Indicators: {', '.join(c['indicators'])}\n"
                f"  Timeframes: {', '.join(c['timeframes'])}\n"
                f"  Latest: {c['latest']}"
            )
        
        return "\n\n".join(lines)
