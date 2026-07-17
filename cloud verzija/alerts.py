"""In-memory tracker deciding when a repeat alert is due for a device."""

from datetime import datetime, timedelta
from typing import Dict


class AlertTracker:
    """Tracks the last time a repeat-alert was sent per hostname."""

    def __init__(self) -> None:
        self._last_sent: Dict[str, datetime] = {}

    def should_alert(self, hostname: str, now: datetime, repeat_minutes: int) -> bool:
        last = self._last_sent.get(hostname)
        if last is None:
            return True
        return (now - last) >= timedelta(minutes=repeat_minutes)

    def record_sent(self, hostname: str, now: datetime) -> None:
        self._last_sent[hostname] = now

    def reset(self, hostname: str) -> None:
        """Forget hostname's alert history (call when it recovers to UP)."""
        self._last_sent.pop(hostname, None)
