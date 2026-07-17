# MLFF Monitoring v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent uptime/downtime statistics, threshold/UPS Telegram alerts, on-demand Telegram commands (`/live`, `/stat`, `/juce`), and an automatic daily report to `cloud verzija/service.py`, fully unit-tested locally (no network/VM required) before deployment.

**Architecture:** Four new small, single-purpose modules (`stats.py` for SQLite persistence, `alerts.py` for repeat-alert timing, `reports.py` for text formatting, `telegram_poll.py` for command polling) plus a refactor of `service.py`'s main loop into a pure, testable `run_once()` function that computes state changes and returns a list of `Notification` objects — all actual network I/O (sending email/Telegram, fetching the device page) stays in thin wrapper functions around `run_once()`, so the core logic can be tested without any network access.

**Tech Stack:** Python 3, stdlib `sqlite3` + `zoneinfo` (needs `tzdata` package), `pytest` for tests. No new runtime dependencies beyond `tzdata`.

---

## Context for the implementer

- Spec: `docs/superpowers/specs/2026-07-14-mlff-monitoring-v2-design.md` — read it first, it has the full rationale and exact behavior rules for every feature below. This plan implements that spec section-by-section.
- All work happens inside `cloud verzija/` (the headless service). Do **not** touch `app.py` (desktop GUI) except where explicitly noted.
- `cloud verzija/scraper.py`, `cloud verzija/notifier.py`, and `cloud verzija/service.py`'s `MONITOR_URL` default are already up to date (synced with the root versions in a previous session) — you don't need to fix the scraper or URL.
- Existing `Device` dataclass (`cloud verzija/scraper.py`) has fields: `portal_id, hostname, ip, status, duration, last_change`, plus properties `is_up` (bool) and `key` (== `hostname`).
- Existing `EXCLUDED_HOSTNAMES = {"SCPA1046-L-UPS", "SCPA1046-L-IOL"}` in `service.py` — these two hostnames must never appear in stats, alerts, or reports.
- Run all commands from inside `cloud verzija/` unless stated otherwise. Tests go in `cloud verzija/tests/`.
- Run the full suite with: `python -m pytest tests/ -v` (from inside `cloud verzija/`).

---

### Task 1: Add `offset` support to `get_telegram_updates`

Telegram command polling needs to pass an `offset` parameter to `getUpdates` so old messages aren't reprocessed every cycle. Add this to both `notifier.py` copies (root and `cloud verzija/`) since they must stay identical per the existing consolidation rule.

**Files:**
- Modify: `cloud verzija/notifier.py`
- Modify: `notifier.py` (root — apply the identical change after the cloud verzija version is tested)
- Test: `cloud verzija/tests/test_notifier.py`

- [ ] **Step 1: Write the failing tests**

Create `cloud verzija/tests/` directory (if it doesn't exist) and `cloud verzija/tests/__init__.py` (empty file, so pytest treats it as a package — avoids import collisions between test files with the same name across the two `cloud verzija`/root trees).

Create `cloud verzija/tests/test_notifier.py`:

```python
import json
from unittest.mock import MagicMock, patch

from notifier import get_telegram_updates


def _fake_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


def test_get_telegram_updates_includes_offset_in_url():
    resp = _fake_response({"ok": True, "result": []})
    with patch("notifier.urllib.request.urlopen", return_value=resp) as mock_urlopen:
        get_telegram_updates("TOKEN", offset=42)
        called_request = mock_urlopen.call_args[0][0]
        assert "offset=42" in called_request.full_url


def test_get_telegram_updates_without_offset_omits_param():
    resp = _fake_response({"ok": True, "result": []})
    with patch("notifier.urllib.request.urlopen", return_value=resp) as mock_urlopen:
        get_telegram_updates("TOKEN")
        called_request = mock_urlopen.call_args[0][0]
        assert "offset" not in called_request.full_url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_notifier.py -v`
Expected: FAIL — `TypeError: get_telegram_updates() got an unexpected keyword argument 'offset'`

- [ ] **Step 3: Implement the offset parameter**

In `cloud verzija/notifier.py`, replace the `get_telegram_updates` function:

```python
def get_telegram_updates(bot_token: str, offset: int = None) -> list:
    """Return recent updates (messages sent to the bot) – used to find chat_id
    and to poll for on-demand commands. Pass `offset` (last update_id + 1) to
    avoid reprocessing already-seen messages."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    if offset is not None:
        url += f"?offset={offset}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result.get("result", [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_notifier.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Apply the identical change to the root `notifier.py`**

Open `notifier.py` (repo root) and make the exact same change to `get_telegram_updates` as in Step 3.

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/notifier.py" "cloud verzija/tests/__init__.py" "cloud verzija/tests/test_notifier.py" notifier.py
git commit -m "feat: support offset param in get_telegram_updates for command polling"
```

---

### Task 2: `stats.py` — SQLite persistence for uptime/downtime periods

**Files:**
- Create: `cloud verzija/stats.py`
- Test: `cloud verzija/tests/test_stats.py`

- [ ] **Step 1: Write the failing tests**

Create `cloud verzija/tests/test_stats.py`:

```python
import os
import tempfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import stats


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    stats.init_db(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_record_transition_closes_previous_and_opens_new(db_path):
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    stats.open_initial_period(db_path, "HOST-A", "UP", t0)
    t1 = t0 + timedelta(hours=1)
    stats.record_transition(db_path, "HOST-A", "DOWN", t1)

    start, end = stats.local_day_bounds(date(2026, 7, 13), ZoneInfo("UTC"))
    result = stats.day_stats(db_path, start, end, t1 + timedelta(minutes=30))

    assert result["HOST-A"]["outage_count"] == 1
    assert result["HOST-A"]["downtime_seconds"] == pytest.approx(1800, abs=1)


def test_day_stats_clips_period_to_day_boundary(db_path):
    # Device goes DOWN at 23:00 on day 1 and is still down when queried on day 2.
    t0 = datetime(2026, 7, 13, 23, 0, 0)
    stats.open_initial_period(db_path, "HOST-B", "DOWN", t0)

    start, end = stats.local_day_bounds(date(2026, 7, 13), ZoneInfo("UTC"))
    now = datetime(2026, 7, 14, 5, 0, 0)
    result = stats.day_stats(db_path, start, end, now)

    # Only the 1 hour of downtime inside day 1 (23:00-24:00) should count.
    assert result["HOST-B"]["downtime_seconds"] == pytest.approx(3600, abs=1)


def test_day_stats_returns_empty_dict_with_no_data(db_path):
    start, end = stats.local_day_bounds(date(2026, 7, 13), ZoneInfo("UTC"))
    result = stats.day_stats(db_path, start, end, datetime(2026, 7, 13, 12, 0, 0))
    assert result == {}


def test_day_stats_includes_up_only_hosts_with_zero_downtime(db_path):
    t0 = datetime(2026, 7, 13, 0, 0, 0)
    stats.open_initial_period(db_path, "HOST-C", "UP", t0)

    start, end = stats.local_day_bounds(date(2026, 7, 13), ZoneInfo("UTC"))
    result = stats.day_stats(db_path, start, end, datetime(2026, 7, 13, 12, 0, 0))

    assert result["HOST-C"]["downtime_seconds"] == 0
    assert result["HOST-C"]["outage_count"] == 0


def test_mark_and_check_report_sent(db_path):
    d = date(2026, 7, 13)
    assert stats.was_report_sent(db_path, d) is False
    stats.mark_report_sent(db_path, d)
    assert stats.was_report_sent(db_path, d) is True


def test_open_initial_period_is_noop_if_periods_already_exist(db_path):
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    stats.open_initial_period(db_path, "HOST-D", "UP", t0)
    stats.open_initial_period(db_path, "HOST-D", "DOWN", t0 + timedelta(minutes=5))

    start, end = stats.local_day_bounds(date(2026, 7, 13), ZoneInfo("UTC"))
    result = stats.day_stats(db_path, start, end, t0 + timedelta(hours=1))
    # Second call must be ignored - status stays UP, no DOWN period created.
    assert result["HOST-D"]["downtime_seconds"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stats'`

- [ ] **Step 3: Implement `stats.py`**

Create `cloud verzija/stats.py`:

```python
"""SQLite-backed persistence for device uptime/downtime history."""

import sqlite3
from contextlib import closing
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS status_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,
    status TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_status_periods_hostname ON status_periods(hostname);

CREATE TABLE IF NOT EXISTS sent_reports (
    report_date TEXT PRIMARY KEY
);
"""


def init_db(db_path: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def record_transition(db_path: str, hostname: str, new_status: str, ts: datetime) -> None:
    """Close any open period for hostname and open a new one with new_status."""
    ts_iso = ts.isoformat()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "UPDATE status_periods SET end_ts = ? WHERE hostname = ? AND end_ts IS NULL",
            (ts_iso, hostname),
        )
        conn.execute(
            "INSERT INTO status_periods (hostname, status, start_ts, end_ts) VALUES (?, ?, ?, NULL)",
            (hostname, new_status.upper(), ts_iso),
        )
        conn.commit()


def open_initial_period(db_path: str, hostname: str, status: str, ts: datetime) -> None:
    """Open the first period for hostname, only if it has no periods yet
    (called once per device on the first cycle after service startup)."""
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM status_periods WHERE hostname = ?", (hostname,))
        (count,) = cur.fetchone()
        if count == 0:
            conn.execute(
                "INSERT INTO status_periods (hostname, status, start_ts, end_ts) VALUES (?, ?, ?, NULL)",
                (hostname, status.upper(), ts.isoformat()),
            )
            conn.commit()


def day_stats(
    db_path: str, period_start: datetime, period_end: datetime, now: datetime
) -> Dict[str, dict]:
    """Per-hostname stats for [period_start, period_end) (naive UTC datetimes).

    Returns {hostname: {"downtime_seconds": float, "outage_count": int}} for
    every hostname with at least one period overlapping the window (whether
    UP or DOWN - hosts that were UP the whole time appear with zero downtime).
    `now` is used as the effective end of any still-open period.
    """
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT hostname, status, start_ts, end_ts FROM status_periods "
            "WHERE start_ts < ? AND (end_ts IS NULL OR end_ts > ?)",
            (period_end.isoformat(), period_start.isoformat()),
        ).fetchall()

    result: Dict[str, dict] = {}
    for row in rows:
        hostname = row["hostname"]
        start = datetime.fromisoformat(row["start_ts"])
        end = datetime.fromisoformat(row["end_ts"]) if row["end_ts"] else now
        eff_start = max(start, period_start)
        eff_end = min(end, period_end)
        if eff_end <= eff_start:
            continue
        entry = result.setdefault(hostname, {"downtime_seconds": 0.0, "outage_count": 0})
        if row["status"] == "DOWN":
            entry["downtime_seconds"] += (eff_end - eff_start).total_seconds()
            entry["outage_count"] += 1
    return result


def local_day_bounds(local_date: date_cls, tz: ZoneInfo) -> Tuple[datetime, datetime]:
    """Convert a calendar date in `tz` into [start, end) as naive UTC datetimes."""
    start_local = datetime.combine(local_date, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def mark_report_sent(db_path: str, report_date: date_cls) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_reports (report_date) VALUES (?)",
            (report_date.isoformat(),),
        )
        conn.commit()


def was_report_sent(db_path: str, report_date: date_cls) -> bool:
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT 1 FROM sent_reports WHERE report_date = ?", (report_date.isoformat(),)
        )
        return cur.fetchone() is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_stats.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/stats.py" "cloud verzija/tests/test_stats.py"
git commit -m "feat: add stats.py SQLite persistence for uptime/downtime periods"
```

---

### Task 3: `alerts.py` — repeat-alert timing tracker

**Files:**
- Create: `cloud verzija/alerts.py`
- Test: `cloud verzija/tests/test_alerts.py`

- [ ] **Step 1: Write the failing tests**

Create `cloud verzija/tests/test_alerts.py`:

```python
from datetime import datetime, timedelta

from alerts import AlertTracker


def test_should_alert_true_when_never_sent():
    tracker = AlertTracker()
    now = datetime(2026, 7, 13, 10, 0, 0)
    assert tracker.should_alert("HOST-A", now, repeat_minutes=120) is True


def test_should_alert_false_within_repeat_window():
    tracker = AlertTracker()
    now = datetime(2026, 7, 13, 10, 0, 0)
    tracker.record_sent("HOST-A", now)
    later = now + timedelta(minutes=30)
    assert tracker.should_alert("HOST-A", later, repeat_minutes=120) is False


def test_should_alert_true_after_repeat_window():
    tracker = AlertTracker()
    now = datetime(2026, 7, 13, 10, 0, 0)
    tracker.record_sent("HOST-A", now)
    later = now + timedelta(minutes=121)
    assert tracker.should_alert("HOST-A", later, repeat_minutes=120) is True


def test_reset_clears_tracked_alert():
    tracker = AlertTracker()
    now = datetime(2026, 7, 13, 10, 0, 0)
    tracker.record_sent("HOST-A", now)
    tracker.reset("HOST-A")
    assert tracker.should_alert("HOST-A", now, repeat_minutes=120) is True


def test_trackers_are_independent_per_hostname():
    tracker = AlertTracker()
    now = datetime(2026, 7, 13, 10, 0, 0)
    tracker.record_sent("HOST-A", now)
    assert tracker.should_alert("HOST-B", now, repeat_minutes=120) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_alerts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alerts'`

- [ ] **Step 3: Implement `alerts.py`**

Create `cloud verzija/alerts.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_alerts.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/alerts.py" "cloud verzija/tests/test_alerts.py"
git commit -m "feat: add alerts.py repeat-alert timing tracker"
```

---

### Task 4: `reports.py` — text formatting for Telegram messages

**Files:**
- Create: `cloud verzija/reports.py`
- Test: `cloud verzija/tests/test_reports.py`

- [ ] **Step 1: Write the failing tests**

Create `cloud verzija/tests/test_reports.py`:

```python
from reports import (
    format_day_report,
    format_duration,
    format_live_status,
    format_threshold_alert,
    format_ups_alert,
)
from scraper import Device


def make_device(hostname, ip, status):
    return Device(portal_id="1", hostname=hostname, ip=ip, status=status, duration="1h", last_change="")


def test_format_duration_various():
    assert format_duration(30) == "30s"
    assert format_duration(90) == "1m"
    assert format_duration(3661) == "1h 1m"
    assert format_duration(90000) == "1d 1h"


def test_format_live_status_lists_down_devices():
    devices = [make_device("A", "10.0.0.1", "UP"), make_device("B", "10.0.0.2", "DOWN")]
    text = format_live_status(devices)
    assert "UP: 1" in text
    assert "DOWN: 1" in text
    assert "B" in text
    assert "10.0.0.2" in text


def test_format_day_report_empty():
    text = format_day_report("Test", {})
    assert "Nema podataka" in text


def test_format_day_report_lists_worst_devices_sorted_by_downtime():
    data = {
        "A": {"downtime_seconds": 3600, "outage_count": 1},
        "B": {"downtime_seconds": 7200, "outage_count": 2},
        "C": {"downtime_seconds": 0, "outage_count": 0},
    }
    text = format_day_report("Test", data)
    assert "Ukupno: 3 uredjaja aktivnih" in text
    # B has more downtime than A, so it must appear first in the listing.
    assert text.index("B") < text.index("A")
    assert "C" not in text.split("Uredjaji sa najvise downtime-a:")[1]


def test_format_threshold_alert_contains_hostname_and_duration():
    text = format_threshold_alert("HOST-A", "10.0.0.1", 3660, 60)
    assert "HOST-A" in text
    assert "60 min" in text
    assert "1h 1m" in text


def test_format_ups_alert_mentions_power_loss():
    text = format_ups_alert("SITE-UPS", "10.0.0.1", 200)
    assert "SITE-UPS" in text
    assert "struje" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_reports.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reports'`

- [ ] **Step 3: Implement `reports.py`**

Create `cloud verzija/reports.py`:

```python
"""Text formatting for Telegram alerts, commands, and daily reports."""

from typing import Dict, List

from scraper import Device


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def format_live_status(devices: List[Device]) -> str:
    up_count = sum(1 for d in devices if d.is_up)
    down = [d for d in devices if not d.is_up]
    lines = [
        "=== MLFF Monitoring - Trenutni status ===",
        f"UP: {up_count}   DOWN: {len(down)}",
    ]
    if down:
        lines.append("")
        lines.append(f"Trenutno DOWN ({len(down)} uredjaj/a):")
        for d in down:
            lines.append(f"  {d.hostname}  {d.ip}  {d.duration}")
    return "\n".join(lines)


def format_day_report(title: str, day_stats_by_host: Dict[str, dict]) -> str:
    if not day_stats_by_host:
        return f"=== {title} ===\nNema podataka za ovaj period."

    total = len(day_stats_by_host)
    total_seconds = 86400
    total_downtime = sum(v["downtime_seconds"] for v in day_stats_by_host.values())
    network_uptime_pct = 100.0 * (1 - total_downtime / (total * total_seconds))

    lines = [
        f"=== {title} ===",
        f"Ukupno: {total} uredjaja aktivnih",
        f"Mrezni uptime: {network_uptime_pct:.1f}%",
    ]

    worst = sorted(
        (item for item in day_stats_by_host.items() if item[1]["downtime_seconds"] > 0),
        key=lambda kv: kv[1]["downtime_seconds"],
        reverse=True,
    )
    if worst:
        lines.append("")
        lines.append("Uredjaji sa najvise downtime-a:")
        for hostname, stat in worst:
            uptime_pct = 100.0 * (1 - stat["downtime_seconds"] / total_seconds)
            lines.append(
                f"  {hostname}  {format_duration(stat['downtime_seconds'])} DOWN  "
                f"({stat['outage_count']} prekida)  {uptime_pct:.1f}% uptime"
            )
    else:
        lines.append("")
        lines.append("Nema zabelezenih prekida.")

    return "\n".join(lines)


def format_threshold_alert(hostname: str, ip: str, down_duration_seconds: float, threshold_minutes: int) -> str:
    return (
        f"MLFF ALARM - {hostname} nedostupan duze od {threshold_minutes} min\n"
        f"IP: {ip}\n"
        f"Trenutno trajanje: {format_duration(down_duration_seconds)}"
    )


def format_ups_alert(hostname: str, ip: str, down_duration_seconds: float) -> str:
    return (
        f"MLFF UPS ALARM - {hostname} moguc gubitak struje na lokaciji\n"
        f"IP: {ip}\n"
        f"Trenutno trajanje: {format_duration(down_duration_seconds)}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_reports.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/reports.py" "cloud verzija/tests/test_reports.py"
git commit -m "feat: add reports.py text formatting for alerts and stats"
```

---

### Task 5: `telegram_poll.py` — command parsing and polling

**Files:**
- Create: `cloud verzija/telegram_poll.py`
- Test: `cloud verzija/tests/test_telegram_poll.py`

- [ ] **Step 1: Write the failing tests**

Create `cloud verzija/tests/test_telegram_poll.py`:

```python
from unittest.mock import patch

from telegram_poll import TelegramCommandPoller, extract_commands, parse_command


def test_parse_command_recognizes_known_commands():
    assert parse_command("/live") == "/live"
    assert parse_command("/stat") == "/stat"
    assert parse_command("/juce") == "/juce"


def test_parse_command_strips_bot_username_suffix():
    assert parse_command("/live@mlff_bot") == "/live"


def test_parse_command_returns_none_for_unknown_text():
    assert parse_command("hello") is None
    assert parse_command("") is None
    assert parse_command(None) is None


def test_extract_commands_filters_by_allowed_chat_id():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 111}, "text": "/live"}},
        {"update_id": 2, "message": {"chat": {"id": 222}, "text": "/stat"}},
    ]
    result = extract_commands(updates, allowed_chat_ids=["111"])
    assert len(result) == 1
    assert result[0]["chat_id"] == "111"
    assert result[0]["command"] == "/live"


def test_extract_commands_ignores_non_command_text():
    updates = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "hej"}}]
    result = extract_commands(updates, allowed_chat_ids=["111"])
    assert result == []


def test_poll_and_dispatch_sends_handler_response_back():
    updates_seq = [[{"update_id": 5, "message": {"chat": {"id": 111}, "text": "/live"}}]]

    def fake_get_updates(token, offset=None):
        return updates_seq.pop(0) if updates_seq else []

    sent = []

    def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    with patch("telegram_poll.get_telegram_updates", side_effect=fake_get_updates), \
         patch("telegram_poll.send_telegram", side_effect=fake_send):
        poller = TelegramCommandPoller("TOKEN", ["111"])
        poller.poll_and_dispatch(lambda chat_id, command: f"echo:{command}")

    assert sent == [("111", "echo:/live")]


def test_poll_and_dispatch_ignores_disallowed_chat_id():
    updates_seq = [[{"update_id": 5, "message": {"chat": {"id": 999}, "text": "/live"}}]]

    def fake_get_updates(token, offset=None):
        return updates_seq.pop(0) if updates_seq else []

    sent = []

    with patch("telegram_poll.get_telegram_updates", side_effect=fake_get_updates), \
         patch("telegram_poll.send_telegram", side_effect=lambda *a: sent.append(a)):
        poller = TelegramCommandPoller("TOKEN", ["111"])
        poller.poll_and_dispatch(lambda chat_id, command: "should not be called")

    assert sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_telegram_poll.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_poll'`

- [ ] **Step 3: Implement `telegram_poll.py`**

Create `cloud verzija/telegram_poll.py`:

```python
"""Polling and dispatch for on-demand Telegram bot commands (/live, /stat, /juce)."""

import logging
from typing import Callable, Dict, List, Optional

from notifier import get_telegram_updates, send_telegram

log = logging.getLogger(__name__)

COMMANDS = {"/live", "/stat", "/juce"}


def parse_command(text: Optional[str]) -> Optional[str]:
    """Extract a known command (without an @botname suffix) from message text."""
    if not text:
        return None
    first_word = text.strip().split()[0].lower() if text.strip() else ""
    if not first_word:
        return None
    first_word = first_word.split("@")[0]
    return first_word if first_word in COMMANDS else None


def extract_commands(updates: List[dict], allowed_chat_ids: List[str]) -> List[Dict[str, object]]:
    """Return [{"chat_id": str, "command": str, "update_id": int}, ...] for
    messages from allowed chat_ids that match a known command."""
    allowed = set(allowed_chat_ids)
    results = []
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        command = parse_command(msg.get("text", ""))
        if chat_id in allowed and command:
            results.append({"chat_id": chat_id, "command": command, "update_id": u.get("update_id")})
    return results


class TelegramCommandPoller:
    """Polls getUpdates for new messages and dispatches recognized commands."""

    def __init__(self, bot_token: str, allowed_chat_ids: List[str]) -> None:
        self._bot_token = bot_token
        self._allowed_chat_ids = allowed_chat_ids
        self._offset: Optional[int] = None

    def prime(self) -> None:
        """Discard any backlog of messages on startup so old commands aren't reprocessed."""
        updates = get_telegram_updates(self._bot_token)
        if updates:
            self._offset = max(u["update_id"] for u in updates) + 1

    def poll_and_dispatch(self, handler: Callable[[str, str], str]) -> None:
        """Fetch new updates; for each recognized command from an allowed chat,
        call handler(chat_id, command) -> response text, and send it back."""
        updates = get_telegram_updates(self._bot_token, offset=self._offset)
        if not updates:
            return
        self._offset = max(u["update_id"] for u in updates) + 1
        for item in extract_commands(updates, self._allowed_chat_ids):
            try:
                response = handler(item["chat_id"], item["command"])
                send_telegram(self._bot_token, item["chat_id"], response)
            except Exception:
                log.exception("Greska pri obradi Telegram komande %s", item["command"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_telegram_poll.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/telegram_poll.py" "cloud verzija/tests/test_telegram_poll.py"
git commit -m "feat: add telegram_poll.py for on-demand bot commands"
```

---

### Task 6: Wire everything into `service.py`

This is the integration task: config additions, a pure `run_once()` function (testable without network), and thin I/O wrappers around it.

**Files:**
- Modify: `cloud verzija/service.py` (full rewrite of the file — see below)
- Test: `cloud verzija/tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

Create `cloud verzija/tests/test_service.py`:

```python
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import stats
from scraper import Device
from service import ServiceState, run_once


def make_device(hostname, ip, status):
    return Device(portal_id="1", hostname=hostname, ip=ip, status=status, duration="1h", last_change="")


def base_cfg(**overrides):
    cfg = {
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_user": "", "smtp_password": "",
        "email_recipients": [], "telegram_bot_token": "TOKEN", "telegram_chat_ids": ["111"],
        "notify_email": False, "notify_telegram": True,
        "notify_threshold_alert": True, "notify_ups_alert": True,
        "down_threshold_minutes": 60, "ups_alert_delay_minutes": 3,
        "alert_repeat_minutes": 120, "daily_report_time": "09:01", "timezone": "UTC",
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    stats.init_db(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_run_once_no_notifications_on_first_cycle(db_path):
    state = ServiceState()
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state,
                      [make_device("HOST-A", "10.0.0.1", "UP")], datetime(2026, 7, 13, 10, 0, 0))
    assert notes == []
    assert state.first_run is False


def test_run_once_ignores_excluded_hostnames_entirely(db_path):
    state = ServiceState()
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state,
                      [make_device("SCPA1046-L-UPS", "172.23.7.4", "DOWN")], datetime(2026, 7, 13, 10, 0, 0))
    assert notes == []
    assert state.down_since == {}


def test_run_once_sends_per_event_notification_on_status_change(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    t1 = t0 + timedelta(minutes=1)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

    telegram_notes = [n for n in notes if n.channel == "telegram"]
    assert len(telegram_notes) == 1
    assert telegram_notes[0].recipient == "111"


def test_run_once_fires_threshold_alert_after_60_minutes_down(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t0)

    t1 = t0 + timedelta(minutes=61)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

    alert_notes = [n for n in notes if "60 min" in n.text]
    assert len(alert_notes) == 1


def test_run_once_does_not_repeat_threshold_alert_before_interval(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t0)
    t1 = t0 + timedelta(minutes=61)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

    t2 = t1 + timedelta(minutes=30)  # repeat interval is 120 minutes
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t2)
    assert [n for n in notes if "60 min" in n.text] == []


def test_run_once_fires_ups_alert_after_3_minutes_down(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("SITE-UPS", "10.0.0.1", "DOWN")], t0)

    t1 = t0 + timedelta(minutes=4)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("SITE-UPS", "10.0.0.1", "DOWN")], t1)

    ups_notes = [n for n in notes if "struje" in n.text]
    assert len(ups_notes) == 1


def test_run_once_resets_alert_tracking_when_device_recovers(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t0)
    t1 = t0 + timedelta(minutes=61)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

    t2 = t1 + timedelta(minutes=1)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t2)

    t3 = t2 + timedelta(minutes=61)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t3)
    assert len([n for n in notes if "60 min" in n.text]) == 1


def test_run_once_sends_daily_report_at_configured_time(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 0, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    t1 = datetime(2026, 7, 14, 9, 1, 0)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t1)

    assert len([n for n in notes if "Dnevni izvestaj" in n.text]) == 1


def test_run_once_does_not_send_daily_report_twice_same_day(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 0, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    t1 = datetime(2026, 7, 14, 9, 1, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t1)
    notes2 = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t1)

    assert [n for n in notes2 if "Dnevni izvestaj" in n.text] == []


def test_get_config_reads_new_env_vars(monkeypatch):
    monkeypatch.setenv("DOWN_THRESHOLD_MINUTES", "45")
    monkeypatch.setenv("NOTIFY_UPS_ALERT", "false")
    import service
    cfg = service.get_config()
    assert cfg["down_threshold_minutes"] == 45
    assert cfg["notify_ups_alert"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'ServiceState' from 'service'` (current `service.py` has no such symbol)

- [ ] **Step 3: Rewrite `service.py`**

Replace the entire contents of `cloud verzija/service.py`:

```python
"""
MLFF Monitoring – Cloud/headless servis
Pokreće se bez GUI-ja i čita konfiguraciju iz environment varijabli.

Env varijable:
  SMTP_HOST                 (default: smtp.gmail.com)
  SMTP_PORT                 (default: 587)
  SMTP_USER                 email posiljaoca
  SMTP_PASSWORD              app password
  EMAIL_RECIPIENTS           email adrese odvojene zarezom
  TELEGRAM_BOT_TOKEN         token Telegram bota
  TELEGRAM_CHAT_IDS          chat ID-evi odvojeni zarezom
  NOTIFY_EMAIL                true/false (default: true) - per-event email
  NOTIFY_TELEGRAM             true/false (default: true) - per-event telegram
  NOTIFY_THRESHOLD_ALERT      true/false (default: true) - 60-min prag alarm
  NOTIFY_UPS_ALERT            true/false (default: true) - UPS/power-loss alarm
  DOWN_THRESHOLD_MINUTES      (default: 60)
  UPS_ALERT_DELAY_MINUTES     (default: 3)
  ALERT_REPEAT_MINUTES        (default: 120)
  DAILY_REPORT_TIME           (default: 09:01, format HH:MM)
  TIMEZONE                    (default: Europe/Belgrade)
  STATS_DB_PATH                (default: data/stats.db)
  CHECK_INTERVAL_SEC          interval provere u sekundama (default: 60)
  MONITOR_URL                  URL stranice za monitoring
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import reports
import stats
from alerts import AlertTracker
from notifier import build_notification_text, send_email, send_telegram
from scraper import Device, fetch_devices
from telegram_poll import TelegramCommandPoller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MONITOR_URL = os.environ.get("MONITOR_URL", "https://mlff.sdn.rs")
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "60"))
STATS_DB_PATH = os.environ.get("STATS_DB_PATH", "data/stats.db")

EXCLUDED_HOSTNAMES = {"SCPA1046-L-UPS", "SCPA1046-L-IOL"}


def _env_bool(name: str, default: bool = True) -> bool:
    return os.environ.get(name, str(default)).lower() not in ("false", "0", "no")


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def get_config() -> dict:
    return {
        "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
        "email_recipients": _env_list("EMAIL_RECIPIENTS"),
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_ids": _env_list("TELEGRAM_CHAT_IDS"),
        "notify_email": _env_bool("NOTIFY_EMAIL"),
        "notify_telegram": _env_bool("NOTIFY_TELEGRAM"),
        "notify_threshold_alert": _env_bool("NOTIFY_THRESHOLD_ALERT"),
        "notify_ups_alert": _env_bool("NOTIFY_UPS_ALERT"),
        "down_threshold_minutes": int(os.environ.get("DOWN_THRESHOLD_MINUTES", "60")),
        "ups_alert_delay_minutes": int(os.environ.get("UPS_ALERT_DELAY_MINUTES", "3")),
        "alert_repeat_minutes": int(os.environ.get("ALERT_REPEAT_MINUTES", "120")),
        "daily_report_time": os.environ.get("DAILY_REPORT_TIME", "09:01"),
        "timezone": os.environ.get("TIMEZONE", "Europe/Belgrade"),
    }


@dataclass
class Notification:
    channel: str  # "email" or "telegram"
    recipient: str
    text: str
    subject: str = ""


@dataclass
class ServiceState:
    last_statuses: Dict[str, str] = field(default_factory=dict)
    down_since: Dict[str, datetime] = field(default_factory=dict)
    threshold_tracker: AlertTracker = field(default_factory=AlertTracker)
    ups_tracker: AlertTracker = field(default_factory=AlertTracker)
    first_run: bool = True
    last_report_date: Optional[date] = None
    active_devices: List[Device] = field(default_factory=list)


def run_once(
    cfg: dict,
    db_path: str,
    tz: ZoneInfo,
    state: ServiceState,
    devices: List[Device],
    now_utc: datetime,
) -> List[Notification]:
    """Process one poll cycle. No network I/O here - devices are already
    fetched and now_utc is passed in, so this is fully unit-testable.
    Mutates `state` in place and returns notifications for the caller to send."""
    notifications: List[Notification] = []

    active = [d for d in devices if d.hostname not in EXCLUDED_HOSTNAMES]
    all_down = [d for d in active if not d.is_up]
    up_count = sum(1 for d in active if d.is_up)

    new_statuses = {d.key: d.status for d in active}
    changed = [
        d for d in active
        if d.key in state.last_statuses
        and state.last_statuses[d.key].upper() != d.status.upper()
    ]

    for d in active:
        if d.key not in state.last_statuses:
            stats.open_initial_period(db_path, d.key, "UP" if d.is_up else "DOWN", now_utc)
    for d in changed:
        stats.record_transition(db_path, d.key, "UP" if d.is_up else "DOWN", now_utc)

    state.last_statuses = new_statuses

    for d in all_down:
        state.down_since.setdefault(d.key, now_utc)
    for d in active:
        if d.is_up and d.key in state.down_since:
            del state.down_since[d.key]
            state.threshold_tracker.reset(d.key)
            state.ups_tracker.reset(d.key)

    if changed and not state.first_run:
        subject = (
            f"MLFF ALARM – {len([d for d in changed if not d.is_up])} uredjaj(a) DOWN"
            if any(not d.is_up for d in changed)
            else "MLFF – Uredjaj(i) ponovo UP"
        )
        body = build_notification_text(changed, all_down, up_count)
        if cfg["notify_email"]:
            for addr in cfg["email_recipients"]:
                notifications.append(Notification("email", addr, body, subject))
        if cfg["notify_telegram"]:
            for cid in cfg["telegram_chat_ids"]:
                notifications.append(Notification("telegram", cid, body))

    if cfg["notify_threshold_alert"]:
        threshold = timedelta(minutes=cfg["down_threshold_minutes"])
        for d in all_down:
            since = state.down_since.get(d.key)
            if since is None or (now_utc - since) < threshold:
                continue
            if not state.threshold_tracker.should_alert(d.key, now_utc, cfg["alert_repeat_minutes"]):
                continue
            text = reports.format_threshold_alert(
                d.hostname, d.ip, (now_utc - since).total_seconds(), cfg["down_threshold_minutes"]
            )
            for cid in cfg["telegram_chat_ids"]:
                notifications.append(Notification("telegram", cid, text))
            state.threshold_tracker.record_sent(d.key, now_utc)

    if cfg["notify_ups_alert"]:
        threshold = timedelta(minutes=cfg["ups_alert_delay_minutes"])
        for d in all_down:
            if not d.hostname.endswith("-UPS"):
                continue
            since = state.down_since.get(d.key)
            if since is None or (now_utc - since) < threshold:
                continue
            if not state.ups_tracker.should_alert(d.key, now_utc, cfg["alert_repeat_minutes"]):
                continue
            text = reports.format_ups_alert(d.hostname, d.ip, (now_utc - since).total_seconds())
            for cid in cfg["telegram_chat_ids"]:
                notifications.append(Notification("telegram", cid, text))
            state.ups_tracker.record_sent(d.key, now_utc)

    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    if now_local.strftime("%H:%M") == cfg["daily_report_time"]:
        today_local = now_local.date()
        if state.last_report_date != today_local and not stats.was_report_sent(db_path, today_local):
            yesterday_local = today_local - timedelta(days=1)
            start_utc, end_utc = stats.local_day_bounds(yesterday_local, tz)
            day_data = stats.day_stats(db_path, start_utc, end_utc, now_utc)
            text = reports.format_day_report(
                f"Dnevni izvestaj: {yesterday_local.strftime('%d.%m.%Y')}", day_data
            )
            for cid in cfg["telegram_chat_ids"]:
                notifications.append(Notification("telegram", cid, text))
            stats.mark_report_sent(db_path, today_local)
            state.last_report_date = today_local

    state.first_run = False
    state.active_devices = active
    return notifications


def _dispatch_notifications(cfg: dict, notifications: List[Notification]) -> None:
    for n in notifications:
        try:
            if n.channel == "email":
                if cfg["smtp_user"] and cfg["smtp_password"]:
                    send_email(
                        cfg["smtp_host"], cfg["smtp_port"], cfg["smtp_user"], cfg["smtp_password"],
                        n.recipient, n.subject, n.text,
                    )
                    log.info("Email -> %s", n.recipient)
                else:
                    log.warning("Email nije konfigurisan (SMTP_USER / SMTP_PASSWORD nisu postavljeni)")
            elif n.channel == "telegram":
                if cfg["telegram_bot_token"]:
                    send_telegram(cfg["telegram_bot_token"], n.recipient, n.text)
                    log.info("Telegram -> %s", n.recipient)
                else:
                    log.warning("Telegram: TELEGRAM_BOT_TOKEN nije postavljen")
        except Exception as e:
            log.error("%s GRESKA (%s): %s", n.channel, n.recipient, e)


def _handle_command(command: str, db_path: str, tz: ZoneInfo, active_devices: List[Device], now_utc: datetime) -> str:
    if command == "/live":
        return reports.format_live_status(active_devices)

    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    if command == "/stat":
        start_utc, _ = stats.local_day_bounds(now_local.date(), tz)
        day_data = stats.day_stats(db_path, start_utc, now_utc, now_utc)
        return reports.format_day_report(
            f"Statistika: {now_local.date().strftime('%d.%m.%Y')} (do sada)", day_data
        )
    if command == "/juce":
        yesterday = now_local.date() - timedelta(days=1)
        start_utc, end_utc = stats.local_day_bounds(yesterday, tz)
        day_data = stats.day_stats(db_path, start_utc, end_utc, now_utc)
        return reports.format_day_report(f"Statistika: {yesterday.strftime('%d.%m.%Y')}", day_data)
    return "Nepoznata komanda."


def _poll_telegram_commands(
    poller: TelegramCommandPoller, db_path: str, tz: ZoneInfo, active_devices: List[Device], now_utc: datetime
) -> None:
    poller.poll_and_dispatch(
        lambda chat_id, command: _handle_command(command, db_path, tz, active_devices, now_utc)
    )


def run() -> None:
    cfg = get_config()
    os.makedirs(os.path.dirname(STATS_DB_PATH) or ".", exist_ok=True)
    stats.init_db(STATS_DB_PATH)
    tz = ZoneInfo(cfg["timezone"])

    log.info(
        "MLFF Monitoring servis pokrenut. URL: %s  Interval: %ds  Baza: %s",
        MONITOR_URL, CHECK_INTERVAL_SEC, STATS_DB_PATH,
    )

    state = ServiceState()
    poller: Optional[TelegramCommandPoller] = None
    if cfg["telegram_bot_token"] and cfg["telegram_chat_ids"]:
        poller = TelegramCommandPoller(cfg["telegram_bot_token"], cfg["telegram_chat_ids"])
        poller.prime()

    while True:
        now_utc = datetime.utcnow()
        now_str = now_utc.strftime("%H:%M:%S")
        cfg = get_config()
        try:
            devices = fetch_devices(MONITOR_URL)
        except Exception as e:
            log.error("[%s] Greska pri dohvatanju: %s", now_str, e)
            time.sleep(CHECK_INTERVAL_SEC)
            continue

        notifications = run_once(cfg, STATS_DB_PATH, tz, state, devices, now_utc)
        _dispatch_notifications(cfg, notifications)

        down_count = sum(1 for d in state.active_devices if not d.is_up)
        log.info("[%s] UP: %d  DOWN: %d", now_str, len(state.active_devices) - down_count, down_count)

        if poller:
            _poll_telegram_commands(poller, STATS_DB_PATH, tz, state.active_devices, now_utc)

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the entire test suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: PASS (all tests across all files — 36 total)

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/service.py" "cloud verzija/tests/test_service.py"
git commit -m "feat: wire stats, alerts, and telegram commands into service.py main loop"
```

---

### Task 7: Update packaging files (requirements, Docker, .env.example)

**Files:**
- Modify: `cloud verzija/requirements.txt`
- Create: `cloud verzija/requirements-dev.txt`
- Modify: `cloud verzija/Dockerfile`
- Modify: `cloud verzija/docker-compose.yml`
- Modify: `cloud verzija/.env.example`

- [ ] **Step 1: Add `tzdata` to runtime requirements**

`zoneinfo` needs the IANA timezone database, which slim Docker images don't include at the OS level — the `tzdata` PyPI package provides it as a fallback. Update `cloud verzija/requirements.txt`:

```
requests
beautifulsoup4
urllib3
tzdata
```

- [ ] **Step 2: Add a dev-only requirements file**

Create `cloud verzija/requirements-dev.txt`:

```
pytest
```

- [ ] **Step 3: Update the Dockerfile to copy the new modules**

Edit `cloud verzija/Dockerfile`, change the `COPY` line:

```dockerfile
COPY scraper.py notifier.py service.py stats.py alerts.py reports.py telegram_poll.py ./
```

- [ ] **Step 4: Add a persistent volume for the stats database**

Edit `cloud verzija/docker-compose.yml` to mount a host directory for `stats.db`:

```yaml
services:
  mlff-monitor:
    build: .
    image: mlff-monitor
    container_name: mlff-monitor
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/app/data
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

- [ ] **Step 5: Document the new env vars**

Edit `cloud verzija/.env.example`, add after the existing `NOTIFY_TELEGRAM=true` line:

```
# Statistika i alarmi (v2)
NOTIFY_THRESHOLD_ALERT=true
NOTIFY_UPS_ALERT=true
DOWN_THRESHOLD_MINUTES=60
UPS_ALERT_DELAY_MINUTES=3
ALERT_REPEAT_MINUTES=120
DAILY_REPORT_TIME=09:01
TIMEZONE=Europe/Belgrade
STATS_DB_PATH=data/stats.db
```

- [ ] **Step 6: Verify Docker build succeeds locally**

Run: `cd "cloud verzija" && docker build -t mlff-monitor-test .`
Expected: build completes without errors (this validates the Dockerfile COPY line and requirements.txt are consistent — doesn't require running the container).

If Docker isn't available locally, skip this step and rely on the VM build in Task 9.

- [ ] **Step 7: Commit**

```bash
git add "cloud verzija/requirements.txt" "cloud verzija/requirements-dev.txt" "cloud verzija/Dockerfile" "cloud verzija/docker-compose.yml" "cloud verzija/.env.example"
git commit -m "chore: update packaging for stats/alerts/telegram-commands modules"
```

---

### Task 8: Manual end-to-end smoke test with real device data

Unit tests cover the logic in isolation. This step proves the whole thing works together against real (or realistically shaped) data, without needing the VM.

**Files:** none (verification only — uses a scratch script, not committed)

- [ ] **Step 1: Get a fresh HTML snapshot of the monitoring page**

Since `mlff.sdn.rs` is only reachable from the corporate network/whitelisted IPs, fetch it from a machine that has access (PowerShell, as done in earlier sessions):

```powershell
$r = Invoke-WebRequest -Uri "https://mlff.sdn.rs" -UseBasicParsing; $r.Content | Out-File -FilePath "cloud verzija\smoketest_page.html" -Encoding utf8
```

- [ ] **Step 2: Run a one-off script exercising the full pipeline**

From `cloud verzija/`, run this inline Python (adjust the `-c` script as needed, or save as a temporary `smoketest.py` and delete it afterward — do not commit it):

```bash
cd "cloud verzija"
python -c "
import sys
sys.path.insert(0, '.')
from bs4 import BeautifulSoup
import scraper

html = open('smoketest_page.html', encoding='utf-8').read()

class FakeResp:
    text = html
    def raise_for_status(self): pass

import requests
requests.get = lambda *a, **kw: FakeResp()
devices = scraper.fetch_devices('dummy')
print(f'Parsed {len(devices)} devices')

import service, stats
from datetime import datetime
from zoneinfo import ZoneInfo

db_path = 'smoketest_stats.db'
stats.init_db(db_path)
cfg = service.get_config()
cfg['telegram_bot_token'] = ''  # avoid accidentally sending real messages
state = service.ServiceState()
now = datetime.utcnow()
notes = service.run_once(cfg, db_path, ZoneInfo('Europe/Belgrade'), state, devices, now)
print(f'Notifications on first cycle: {len(notes)} (should be 0)')
print(f'Active devices tracked: {len(state.active_devices)}')

live_text = service.reports.format_live_status(state.active_devices)
print()
print(live_text)
"
```

Expected: prints the real device count (matches what the desktop app shows), 0 notifications on the first cycle (no prior state to compare against), and a `/live`-style status listing with real hostnames/IPs.

- [ ] **Step 3: Clean up scratch files**

```bash
cd "cloud verzija"
rm -f smoketest_page.html smoketest_stats.db
```

(On Windows PowerShell instead: `Remove-Item "cloud verzija\smoketest_page.html","cloud verzija\smoketest_stats.db" -ErrorAction SilentlyContinue`)

- [ ] **Step 4: Confirm scratch files are not tracked by git**

Run: `git status --short`
Expected: no `smoketest_*` files listed (they were deleted in Step 3, and `*.db` is not gitignored yet — add `cloud verzija/data/` and `*.db` to root `.gitignore` if missing)

Check `.gitignore` (repo root) contains a line for `*.db` and `data/`; if not, add:

```
*.db
data/
```

- [ ] **Step 5: Commit gitignore update if changed**

```bash
git add .gitignore
git commit -m "chore: ignore local sqlite db and data directory"
```

(Skip this commit if `.gitignore` already covered these patterns.)

---

### Task 9: Update documentation

Keep `UPUTSTVO.md` and `DEPLOY.md` in sync with the new capabilities, and fix the Ubuntu→Oracle Linux / `ubuntu`→`opc` discrepancy discovered during the actual VM setup.

**Files:**
- Modify: `UPUTSTVO.md`
- Modify: `DEPLOY.md`
- Modify: `ORACLE_CLOUD_SETUP.md`

- [ ] **Step 1: Document the new Telegram commands in `UPUTSTVO.md`**

Add a new section after the existing "Cloud verzija" section in `UPUTSTVO.md`:

```markdown
### Telegram komande (cloud verzija)

Kad je cloud servis pokrenut, možeš mu poslati ove komande direktno u Telegramu
(samo sa chat ID-a koji je na listi primalaca u `.env`):

| Komanda | Šta vraća |
|---|---|
| `/live` | Trenutni status svih uređaja (koliko je UP/DOWN, lista DOWN uređaja) |
| `/stat` | Statistika od ponoći do sada (današnji dan) |
| `/juce` | Statistika za ceo prethodni dan |

Automatski dnevni izveštaj (isti sadržaj kao `/juce`) stiže svako jutro u 09:01
bez da išta tražiš.
```

- [ ] **Step 2: Fix OS/username references in `DEPLOY.md` and `ORACLE_CLOUD_SETUP.md`**

In practice, the created instance ended up running **Oracle Linux 9** (not Ubuntu as originally documented), with default username **`opc`** (not `ubuntu`). Search both files for `ubuntu@` and Ubuntu-specific package commands (`apt`), and update:

- Replace `ssh -i "..." ubuntu@<IP>` with `ssh -i "..." opc@<IP>` throughout `DEPLOY.md`.
- Replace the `apt`-based Docker install block in `DEPLOY.md` Korak 3 with the Oracle Linux equivalent (already validated live against the real VM in this session):

```bash
sudo dnf install -y git
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

- In `ORACLE_CLOUD_SETUP.md`, update the "Image" row in the instance-creation table to note both operating systems are acceptable, since the actual image picked can vary by region/availability:

```
| Image and shape → Image | **Ubuntu** or **Oracle Linux** (both work — this guide's DEPLOY.md now covers both; note which one you picked, since the SSH username differs: `ubuntu` for Ubuntu, `opc` for Oracle Linux) |
```

- [ ] **Step 3: Commit**

```bash
git add UPUTSTVO.md DEPLOY.md ORACLE_CLOUD_SETUP.md
git commit -m "docs: document Telegram commands and fix Oracle Linux/opc references"
```

---

### Task 10: Push to GitHub

**Files:** none

- [ ] **Step 1: Verify working tree is clean and all commits are present**

Run: `git status --short && git log --oneline -12`
Expected: clean working tree, and commits from Tasks 1-9 visible at the top of the log.

- [ ] **Step 2: Push**

Run: `git push origin main`
Expected: push succeeds (if GCM credential issues resurface as in earlier sessions, fall back to the embedded-token push method used previously, then immediately clear the token from `.git/config`/remote URL afterward as done before).

---

## What this plan intentionally does NOT do

- Does not touch `app.py` (desktop GUI) or `stabilna verzija/` — out of scope per the spec.
- Does not add a web dashboard — Telegram commands only, per spec.
- Does not add email delivery for threshold/UPS alerts — Telegram only, per spec.
- Does not deploy to the VM — that's a manual step the user does after reviewing this implementation, using the already-written `DEPLOY.md`.
