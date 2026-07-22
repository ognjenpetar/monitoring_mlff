# UPS baterijski/AC alarm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect real UPS mains-power loss (not just management-card network reachability) by reading the separate `upsTable` on `mlff.sdn.rs`, and alert on both Telegram and email after a 3-minute confirmation delay, with self-measured outage duration saved to history.

**Architecture:** `scraper.py` gains a `fetch_all()` that fetches the monitoring page once and parses both `devicesTable` (existing) and `upsTable` (new) from the same snapshot. `service.py` gains a new pure function `check_ups_power()`, run alongside the existing `run_once()`, that tracks per-hostname "not AC OK since" timestamps in `ServiceState`, reuses the existing `AlertTracker` pattern for repeat-alert timing, and reuses the existing `status_periods`-style pattern in `stats.py` for history. `reports.py` gains two new formatters. Mute support (`/mute`) is explicitly out of scope — it doesn't exist on `main` yet (see spec note).

**Tech Stack:** Python 3, `requests`/`beautifulsoup4` (existing), `sqlite3` (stdlib), `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-22-ups-power-alert-design.md`

---

## File Structure

- Modify `cloud verzija/scraper.py` — add `UpsPowerStatus` dataclass, split fetch/parse, add `fetch_all()`.
- Modify `cloud verzija/stats.py` — add `ups_power_periods` table + open/close functions.
- Modify `cloud verzija/reports.py` — add `format_ups_power_alert`/`format_ups_power_recovered`.
- Modify `cloud verzija/service.py` — add `EXCLUDED_UPS_HOSTNAMES`, `ups_power_confirm_minutes` config, `ServiceState` fields, `check_ups_power()`, wire into `run()`.
- Modify `cloud verzija/.env.example` — document new env var.
- Modify `UPUTSTVO.md` — mention the new alert and link the spec.
- New `cloud verzija/tests/test_scraper.py`.
- Modify `cloud verzija/tests/test_stats.py`, `test_reports.py`, `test_service.py`.

All commands below assume the working directory is the repo root, and are run inside a dedicated worktree (see Task 0).

---

### Task 0: Create the worktree

- [ ] **Step 1: Create worktree and branch**

```bash
git worktree add .worktrees/mlff-ups-power -b feature/ups-power-alert
```

- [ ] **Step 2: Verify baseline tests pass**

```bash
cd ".worktrees/mlff-ups-power/cloud verzija"
python -m pytest -q
```

Expected: `38 passed`.

---

### Task 1: `scraper.py` — parse the `upsTable`

**Files:**
- Modify: `cloud verzija/scraper.py`
- Test: `cloud verzija/tests/test_scraper.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `cloud verzija/tests/test_scraper.py`:

```python
from bs4 import BeautifulSoup

import scraper
from scraper import UpsPowerStatus, fetch_devices

DEVICES_HTML = """
<table id="devicesTable">
  <tr><th>Portal</th><th>Uredjaj</th><th>Status</th><th>Trajanje</th></tr>
  <tr>
    <td>11</td>
    <td><span class="hostname">SCPA1011-L</span><span class="dim">172.23.11.1</span></td>
    <td>UP</td>
    <td>5d 2h 3m</td>
  </tr>
</table>
"""

UPS_HTML = """
<table id="upsTable">
  <tr><th>Uredjaj</th><th>Status</th><th>Baterija</th><th>Runtime</th></tr>
  <tr>
    <td><span class="hostname">UPS-11</span><span class="dim">172.23.11.4</span></td>
    <td><span class="badge badge-success">AC OK</span></td>
    <td>100%</td>
    <td>7790m</td>
  </tr>
  <tr>
    <td><span class="hostname">UPS-7</span><span class="dim">172.23.7.4</span></td>
    <td><span class="badge badge-danger">ERR</span></td>
    <td>0%</td>
    <td>0m</td>
  </tr>
</table>
"""

FULL_PAGE_HTML = f"<html><body>{DEVICES_HTML}{UPS_HTML}</body></html>"


def test_parse_ups_statuses_parses_ac_ok_and_err_rows():
    soup = BeautifulSoup(UPS_HTML, "html.parser")
    result = scraper._parse_ups_statuses(soup)

    assert len(result) == 2
    assert result[0].hostname == "UPS-11"
    assert result[0].ip == "172.23.11.4"
    assert result[0].status_text == "AC OK"
    assert result[0].battery_pct == 100
    assert result[0].is_ac_ok is True
    assert result[1].hostname == "UPS-7"
    assert result[1].status_text == "ERR"
    assert result[1].is_ac_ok is False


def test_parse_ups_statuses_returns_empty_list_if_table_missing():
    soup = BeautifulSoup("<html><body><p>no ups table</p></body></html>", "html.parser")
    assert scraper._parse_ups_statuses(soup) == []


def test_ups_power_status_portal_id_derived_from_hostname():
    status = UpsPowerStatus(hostname="UPS-11", ip="172.23.11.4", status_text="AC OK", battery_pct=100)
    assert status.portal_id == "11"


def test_ups_power_status_portal_id_falls_back_to_hostname_if_no_dash():
    status = UpsPowerStatus(hostname="WEIRDNAME", ip="", status_text="AC OK", battery_pct=0)
    assert status.portal_id == "WEIRDNAME"


def test_fetch_all_fetches_page_once_and_parses_both_tables(monkeypatch):
    call_count = {"n": 0}

    class FakeResponse:
        text = FULL_PAGE_HTML

        def raise_for_status(self):
            pass

    def fake_get(url, timeout, verify):
        call_count["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(scraper._session, "get", fake_get)

    devices, ups_statuses = scraper.fetch_all("http://fake-url")

    assert call_count["n"] == 1
    assert len(devices) == 1
    assert devices[0].hostname == "SCPA1011-L"
    assert len(ups_statuses) == 2


def test_fetch_devices_still_works_as_before(monkeypatch):
    class FakeResponse:
        text = DEVICES_HTML

        def raise_for_status(self):
            pass

    monkeypatch.setattr(scraper._session, "get", lambda url, timeout, verify: FakeResponse())

    devices = fetch_devices("http://fake-url")
    assert len(devices) == 1
    assert devices[0].hostname == "SCPA1011-L"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "cloud verzija"
python -m pytest tests/test_scraper.py -v
```

Expected: failures — `UpsPowerStatus` and `scraper._parse_ups_statuses`/`scraper.fetch_all` don't exist yet.

- [ ] **Step 3: Implement**

Add `UpsPowerStatus` right after the existing `Device` dataclass in `scraper.py`:

```python
@dataclass
class UpsPowerStatus:
    hostname: str        # e.g. "UPS-11"
    ip: str
    status_text: str     # raw text from the site: "AC OK", "ERR", or an unknown 3rd value
    battery_pct: int

    @property
    def is_ac_ok(self) -> bool:
        return self.status_text.strip().upper() == "AC OK"

    @property
    def portal_id(self) -> str:
        """Derive the portal number from the hostname (e.g. 'UPS-11' -> '11')."""
        parts = self.hostname.rsplit("-", 1)
        return parts[1] if len(parts) == 2 else self.hostname

    @property
    def key(self) -> str:
        return self.hostname
```

Add `Tuple` to the `typing` import:

```python
from typing import List, Optional, Tuple
```

Replace the body of `fetch_devices` (everything from `def fetch_devices` to the end of the file) with:

```python
def _get_soup(url: str, timeout: int) -> BeautifulSoup:
    response = _session.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def _parse_devices(soup: BeautifulSoup) -> List[Device]:
    devices = []

    table = soup.find("table", id="devicesTable") or soup.find("table")
    if not table:
        raise ValueError("Table not found on page – HTML structure may have changed.")

    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cols = row.find_all(["td", "th"])
        if len(cols) < 4:
            continue

        portal_id = cols[0].get_text(strip=True)

        hostname_span = cols[1].find("span", class_="hostname")
        ip_span = cols[1].find("span", class_="dim")
        hostname = hostname_span.get_text(strip=True) if hostname_span else cols[1].get_text(strip=True)
        ip = ip_span.get_text(strip=True) if ip_span else ""

        status = cols[2].get_text(strip=True)

        duration = cols[3].get_text(strip=True)
        last_change = _last_change_from_duration(duration)

        if not hostname:
            continue

        devices.append(Device(
            portal_id=portal_id,
            hostname=hostname,
            ip=ip,
            status=status,
            duration=duration,
            last_change=last_change,
        ))

    return devices


def _parse_ups_statuses(soup: BeautifulSoup) -> List[UpsPowerStatus]:
    """Parse the 'Monitoring UPS-eva' table (id=upsTable). Returns [] if the
    table isn't present on the page - this is a secondary data source and
    shouldn't fail the whole poll cycle if the site temporarily omits it."""
    table = soup.find("table", id="upsTable")
    if not table:
        return []

    statuses = []
    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cols = row.find_all(["td", "th"])
        if len(cols) < 3:
            continue

        hostname_span = cols[0].find("span", class_="hostname")
        ip_span = cols[0].find("span", class_="dim")
        hostname = hostname_span.get_text(strip=True) if hostname_span else cols[0].get_text(strip=True)
        ip = ip_span.get_text(strip=True) if ip_span else ""

        if not hostname:
            continue

        status_text = cols[1].get_text(strip=True)

        battery_text = cols[2].get_text(strip=True).rstrip("%")
        try:
            battery_pct = int(battery_text)
        except ValueError:
            battery_pct = 0

        statuses.append(UpsPowerStatus(
            hostname=hostname,
            ip=ip,
            status_text=status_text,
            battery_pct=battery_pct,
        ))

    return statuses


def fetch_devices(url: str, timeout: int = 15) -> List[Device]:
    """Fetch and parse device list from the monitoring page."""
    soup = _get_soup(url, timeout)
    return _parse_devices(soup)


def fetch_all(url: str, timeout: int = 15) -> Tuple[List[Device], List[UpsPowerStatus]]:
    """Fetch the monitoring page once and parse both the devices table and
    the UPS power table from the same snapshot, so the two are always
    consistent with each other."""
    soup = _get_soup(url, timeout)
    return _parse_devices(soup), _parse_ups_statuses(soup)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_scraper.py -v
python -m pytest -q
```

Expected: new tests pass, and full suite shows `44 passed` (38 existing + 6 new).

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/scraper.py" "cloud verzija/tests/test_scraper.py"
git commit -m "Add UpsPowerStatus parsing and fetch_all() to scraper.py"
```

---

### Task 2: `stats.py` — history table for UPS power periods

**Files:**
- Modify: `cloud verzija/stats.py`
- Test: `cloud verzija/tests/test_stats.py`

- [ ] **Step 1: Write the failing tests**

Add `import sqlite3` to the top of `cloud verzija/tests/test_stats.py` (it currently only imports `os`, `tempfile`, `date`/`datetime`/`timedelta`, `ZoneInfo`, `pytest`, `stats`), then append:

```python
def test_open_and_close_ups_power_period(db_path):
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    stats.open_ups_power_period(db_path, "UPS-11", "ERR", t0)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT hostname, status_text, start_ts, end_ts FROM ups_power_periods WHERE hostname = ?",
            ("UPS-11",),
        ).fetchone()
    assert row == ("UPS-11", "ERR", t0.isoformat(), None)

    t1 = t0 + timedelta(minutes=5)
    stats.close_ups_power_period(db_path, "UPS-11", t1)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT end_ts FROM ups_power_periods WHERE hostname = ?", ("UPS-11",)
        ).fetchone()
    assert row[0] == t1.isoformat()


def test_close_ups_power_period_is_noop_if_none_open(db_path):
    # Must not raise even when there's nothing open to close.
    stats.close_ups_power_period(db_path, "UPS-99", datetime(2026, 7, 22, 10, 0, 0))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_stats.py -v
```

Expected: `AttributeError: module 'stats' has no attribute 'open_ups_power_period'`.

- [ ] **Step 3: Implement**

In `cloud verzija/stats.py`, append to the `SCHEMA` string (inside the triple-quoted string, after the existing `sent_reports` table):

```python
CREATE TABLE IF NOT EXISTS ups_power_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,
    status_text TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_ups_power_periods_hostname ON ups_power_periods(hostname);
```

Add these two functions at the end of the file:

```python
def open_ups_power_period(db_path: str, hostname: str, status_text: str, ts: datetime) -> None:
    """Open a new 'not AC OK' period for hostname."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO ups_power_periods (hostname, status_text, start_ts, end_ts) VALUES (?, ?, ?, NULL)",
            (hostname, status_text, ts.isoformat()),
        )
        conn.commit()


def close_ups_power_period(db_path: str, hostname: str, ts: datetime) -> None:
    """Close the currently-open 'not AC OK' period for hostname, if any."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "UPDATE ups_power_periods SET end_ts = ? WHERE hostname = ? AND end_ts IS NULL",
            (ts.isoformat(), hostname),
        )
        conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_stats.py -v
python -m pytest -q
```

Expected: `46 passed`.

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/stats.py" "cloud verzija/tests/test_stats.py"
git commit -m "Add ups_power_periods table for UPS AC-outage history"
```

---

### Task 3: `reports.py` — alert and recovery message formatting

**Files:**
- Modify: `cloud verzija/reports.py`
- Test: `cloud verzija/tests/test_reports.py`

- [ ] **Step 1: Write the failing tests**

In `cloud verzija/tests/test_reports.py`, change the import block at the top to:

```python
from reports import (
    format_day_report,
    format_duration,
    format_live_status,
    format_threshold_alert,
    format_ups_alert,
    format_ups_power_alert,
    format_ups_power_recovered,
)
from scraper import Device, UpsPowerStatus
```

Add a helper next to `make_device` and append these tests:

```python
def make_ups_status(hostname, ip, status_text, battery_pct):
    return UpsPowerStatus(hostname=hostname, ip=ip, status_text=status_text, battery_pct=battery_pct)


def test_format_ups_power_alert_contains_all_fields():
    status = make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)
    text = format_ups_power_alert(status, 300)

    assert "UPS-11" in text
    assert "172.23.11.4" in text
    assert "Portal 11" in text
    assert "ERR" in text
    assert "87%" in text
    assert "5m" in text


def test_format_ups_power_recovered_contains_total_duration():
    status = make_ups_status("UPS-11", "172.23.11.4", "AC OK", 100)
    text = format_ups_power_recovered(status, 2520)

    assert "UPS-11" in text
    assert "Portal 11" in text
    assert "42m" in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_reports.py -v
```

Expected: `ImportError: cannot import name 'format_ups_power_alert' from 'reports'`.

- [ ] **Step 3: Implement**

In `cloud verzija/reports.py`, change the import line at the top:

```python
from scraper import Device, UpsPowerStatus
```

Append these two functions at the end of the file:

```python
def format_ups_power_alert(status: UpsPowerStatus, duration_seconds: float) -> str:
    """Format an alarm for a UPS that has lost mains (AC) power."""
    return (
        f"MLFF ALARM - UPS uredjaj nije na mreznom napajanju (AC OK)\n\n"
        f"{status.hostname}\n"
        f"IP: {status.ip}\n"
        f"Lokacija: Portal {status.portal_id}\n"
        f"Status: {status.status_text}\n"
        f"Baterija: {status.battery_pct}%\n"
        f"Trenutno trajanje: {format_duration(duration_seconds)}"
    )


def format_ups_power_recovered(status: UpsPowerStatus, duration_seconds: float) -> str:
    """Format a recovery message for a UPS that has returned to mains (AC) power."""
    return (
        f"MLFF - UPS uredjaj vracen na mrezno napajanje\n\n"
        f"{status.hostname}\n"
        f"IP: {status.ip}\n"
        f"Lokacija: Portal {status.portal_id}\n"
        f"Trajanje ispada: {format_duration(duration_seconds)}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_reports.py -v
python -m pytest -q
```

Expected: `48 passed`.

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/reports.py" "cloud verzija/tests/test_reports.py"
git commit -m "Add UPS AC-power alert/recovery message formatters"
```

---

### Task 4: `service.py` — tracking, alert rule, and wiring

**Files:**
- Modify: `cloud verzija/service.py`
- Test: `cloud verzija/tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

In `cloud verzija/tests/test_service.py`, add `sqlite3` to the imports and update the `service`/`scraper` imports:

```python
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import stats
from scraper import Device, UpsPowerStatus
from service import ServiceState, check_ups_power, run_once
```

In `base_cfg()`, add a default key (next to `"ups_alert_delay_minutes": 3,`):

```python
    "ups_power_confirm_minutes": 3,
```

Add a helper next to `make_device` and append these tests at the end of the file:

```python
def make_ups_status(hostname, ip, status_text, battery_pct):
    return UpsPowerStatus(hostname=hostname, ip=ip, status_text=status_text, battery_pct=battery_pct)


def test_check_ups_power_no_alert_before_confirm_threshold(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    notes = check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t0)

    assert notes == []
    assert "UPS-11" in state.ups_not_ok_since


def test_check_ups_power_alerts_after_confirm_threshold_on_both_channels(db_path):
    state = ServiceState()
    cfg = base_cfg(email_recipients=["kolega@example.com"])
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    check_ups_power(cfg, db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t0)

    t1 = t0 + timedelta(minutes=3)
    notes = check_ups_power(cfg, db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t1)

    telegram_notes = [n for n in notes if n.channel == "telegram"]
    email_notes = [n for n in notes if n.channel == "email"]
    assert len(telegram_notes) == 1
    assert len(email_notes) == 1
    assert "UPS-11" in telegram_notes[0].text
    assert "UPS-11" in email_notes[0].text


def test_check_ups_power_does_not_repeat_before_alert_repeat_minutes(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t0)
    t1 = t0 + timedelta(minutes=3)
    check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t1)

    t2 = t1 + timedelta(minutes=30)  # repeat interval is 120 minutes
    notes = check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t2)
    assert notes == []


def test_check_ups_power_sends_recovery_message_with_total_duration(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t0)
    t1 = t0 + timedelta(minutes=3)
    check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t1)

    t2 = t1 + timedelta(minutes=39)  # total time on battery: 42 minutes since t0
    notes = check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "AC OK", 100)], t2)

    telegram_notes = [n for n in notes if n.channel == "telegram"]
    assert len(telegram_notes) == 1
    assert "42m" in telegram_notes[0].text
    assert "UPS-11" not in state.ups_not_ok_since


def test_check_ups_power_ignores_excluded_hostname(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-7", "172.23.7.4", "ERR", 0)], t0)

    t1 = t0 + timedelta(minutes=10)
    notes = check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-7", "172.23.7.4", "ERR", 0)], t1)

    assert notes == []
    assert state.ups_not_ok_since == {}


def test_check_ups_power_records_history_period_in_db(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    check_ups_power(base_cfg(), db_path, state, [make_ups_status("UPS-11", "172.23.11.4", "ERR", 87)], t0)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT hostname, status_text, end_ts FROM ups_power_periods WHERE hostname = ?", ("UPS-11",)
        ).fetchone()
    assert row == ("UPS-11", "ERR", None)


def test_get_config_reads_ups_power_confirm_minutes(monkeypatch):
    monkeypatch.setenv("UPS_POWER_CONFIRM_MINUTES", "5")
    import service
    cfg = service.get_config()
    assert cfg["ups_power_confirm_minutes"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_service.py -v
```

Expected: `ImportError: cannot import name 'check_ups_power' from 'service'`.

- [ ] **Step 3: Implement**

In `cloud verzija/service.py`:

1. Update the module docstring's env-var list — add this line right after `UPS_ALERT_DELAY_MINUTES`:

```
  UPS_POWER_CONFIRM_MINUTES   (default: 3) - koliko dugo UPS mora biti "nije AC OK" (na bateriji/gresci) pre alarma
```

2. Change the scraper import:

```python
from scraper import Device, UpsPowerStatus, fetch_all
```

3. Add a new module-level constant, right after `EXCLUDED_HOSTNAMES`:

```python
EXCLUDED_UPS_HOSTNAMES = {"UPS-7"}
```

4. In `get_config()`, add a new key (next to `"ups_alert_delay_minutes"`):

```python
        "ups_power_confirm_minutes": int(os.environ.get("UPS_POWER_CONFIRM_MINUTES", "3")),
```

5. In `ServiceState`, add two new fields (next to `ups_tracker`):

```python
    ups_not_ok_since: Dict[str, datetime] = field(default_factory=dict)
    ups_power_tracker: AlertTracker = field(default_factory=AlertTracker)
```

6. Add these two new functions right after `run_once` (before `_dispatch_notifications`):

```python
def check_ups_power(
    cfg: dict,
    db_path: str,
    state: ServiceState,
    ups_statuses: List[UpsPowerStatus],
    now_utc: datetime,
) -> List[Notification]:
    """Process one poll cycle's UPS AC-power statuses. No network I/O here -
    ups_statuses are already fetched and now_utc is passed in, so this is
    fully unit-testable. Mutates `state` in place and returns notifications
    for the caller to send."""
    notifications: List[Notification] = []
    confirm_seconds = cfg["ups_power_confirm_minutes"] * 60

    for status in ups_statuses:
        if status.hostname in EXCLUDED_UPS_HOSTNAMES:
            continue

        if status.is_ac_ok:
            if status.hostname in state.ups_not_ok_since:
                since = state.ups_not_ok_since.pop(status.hostname)
                stats.close_ups_power_period(db_path, status.hostname, now_utc)
                state.ups_power_tracker.reset(status.hostname)
                duration = (now_utc - since).total_seconds()
                text = reports.format_ups_power_recovered(status, duration)
                notifications.extend(_ups_power_notifications(cfg, text))
            continue

        if status.hostname not in state.ups_not_ok_since:
            state.ups_not_ok_since[status.hostname] = now_utc
            stats.open_ups_power_period(db_path, status.hostname, status.status_text, now_utc)

        since = state.ups_not_ok_since[status.hostname]
        duration = (now_utc - since).total_seconds()
        if duration < confirm_seconds:
            continue
        if not state.ups_power_tracker.should_alert(status.hostname, now_utc, cfg["alert_repeat_minutes"]):
            continue

        text = reports.format_ups_power_alert(status, duration)
        notifications.extend(_ups_power_notifications(cfg, text))
        state.ups_power_tracker.record_sent(status.hostname, now_utc)

    return notifications


def _ups_power_notifications(cfg: dict, text: str) -> List[Notification]:
    """UPS AC-power alerts always go to both channels, regardless of the
    NOTIFY_EMAIL/NOTIFY_TELEGRAM toggles - this alert is critical enough to
    always be on (per spec, no separate on/off switch for now)."""
    subject = text.splitlines()[0]
    notes = []
    for addr in cfg["email_recipients"]:
        notes.append(Notification("email", addr, text, subject))
    for cid in cfg["telegram_chat_ids"]:
        notes.append(Notification("telegram", cid, text))
    return notes
```

7. In `run()`, replace:

```python
        try:
            devices = fetch_devices(MONITOR_URL)
        except Exception as e:
            log.error("[%s] Greska pri dohvatanju: %s", now_str, e)
            devices = None

        if devices is not None:
            notifications = run_once(cfg, STATS_DB_PATH, tz, state, devices, now_utc)
            _dispatch_notifications(cfg, notifications)
```

with:

```python
        try:
            devices, ups_statuses = fetch_all(MONITOR_URL)
        except Exception as e:
            log.error("[%s] Greska pri dohvatanju: %s", now_str, e)
            devices = None
            ups_statuses = []

        if devices is not None:
            notifications = run_once(cfg, STATS_DB_PATH, tz, state, devices, now_utc)
            notifications += check_ups_power(cfg, STATS_DB_PATH, state, ups_statuses, now_utc)
            _dispatch_notifications(cfg, notifications)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_service.py -v
python -m pytest -q
```

Expected: `55 passed`.

- [ ] **Step 5: Commit**

```bash
git add "cloud verzija/service.py" "cloud verzija/tests/test_service.py"
git commit -m "Wire UPS AC-power tracking into service.py poll loop"
```

---

### Task 5: Config and docs

**Files:**
- Modify: `cloud verzija/.env.example`
- Modify: `UPUTSTVO.md`

- [ ] **Step 1: Add the new env var to `.env.example`**

In `cloud verzija/.env.example`, add this line right after `UPS_ALERT_DELAY_MINUTES=3`:

```
UPS_POWER_CONFIRM_MINUTES=3
```

- [ ] **Step 2: Mention the new alert in `UPUTSTVO.md`**

In `UPUTSTVO.md`, in the "Cloud verzija" section, add a bullet right after the existing list of doc links (after the `DODAVANJE_PRIMALACA.md` line, before the v2 spec line):

```markdown
- Alarm za gubitak mreznog (AC) napajanja UPS uredjaja (baterijski rad), sa
  potvrdom od 3 minuta i porukom na email i Telegram:
  [`docs/superpowers/specs/2026-07-22-ups-power-alert-design.md`](docs/superpowers/specs/2026-07-22-ups-power-alert-design.md)
```

- [ ] **Step 3: Commit**

```bash
git add "cloud verzija/.env.example" UPUTSTVO.md
git commit -m "Document UPS_POWER_CONFIRM_MINUTES and the new AC-power alert"
```

---

### Task 6: Local manual smoke test

Not a code task — run by hand to confirm the real page still parses correctly before merging or deploying.

- [ ] **Step 1: Run the full test suite one more time**

```bash
python -m pytest -q
```

Expected: `55 passed`.

- [ ] **Step 2: Fetch the real page and inspect the parsed UPS statuses**

From `cloud verzija/`, with the local Python environment active:

```bash
python -c "from scraper import fetch_all; d, u = fetch_all('https://mlff.sdn.rs'); print('devices:', len(d)); [print(s.hostname, s.ip, repr(s.status_text), s.battery_pct, s.is_ac_ok) for s in u]"
```

Expected: prints the device count (matches current known device count) and one line per UPS with a real `status_text` — confirm `UPS-7` is present with whatever status it has (it will still be parsed, exclusion happens in `check_ups_power`, not in the scraper), and confirm all other UPS units currently show `AC OK` (`is_ac_ok=True`), since none are known to be on battery right now. If any row shows an unexpected `status_text` (not `AC OK`/`ERR`), note it — the alert will still fire correctly since the message uses the exact site text, but it's worth knowing for future reference.

- [ ] **Step 3: Run the service locally in the foreground for 2-3 cycles**

```bash
python service.py
```

Watch the log output for the usual `UP: x DOWN: y` line each cycle, and confirm no exceptions/tracebacks appear (especially not from `check_ups_power` or `fetch_all`). Stop with Ctrl+C once confirmed stable for 2-3 cycles.

---

### Task 7: Merge, deploy to VM, and verify

Ops task — executed directly (not via subagent), same pattern as the v2 deploy.

- [ ] **Step 1: Merge the feature branch**

From the repo root (not the worktree):

```bash
git checkout main
git merge feature/ups-power-alert
git worktree remove .worktrees/mlff-ups-power
```

- [ ] **Step 2: Push to GitHub**

```bash
git push origin main
```

- [ ] **Step 3: Deploy to the VM**

Copy the changed files to the VM and add the new env var to the VM's `.env`, then rebuild:

```bash
scp -i "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" "cloud verzija/scraper.py" "cloud verzija/stats.py" "cloud verzija/reports.py" "cloud verzija/service.py" opc@92.4.216.70:~/monitoring_mlff/"cloud verzija"/
ssh -i "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" opc@92.4.216.70
```

On the VM:

```bash
cd ~/monitoring_mlff/"cloud verzija"
nano .env   # add: UPS_POWER_CONFIRM_MINUTES=3
docker compose up -d --build
docker compose logs -f --tail=50
```

- [ ] **Step 4: Verify on the VM**

Watch the logs for several poll cycles (`CHECK_INTERVAL_SEC`, default 60s each) and confirm:
- The usual `UP: x DOWN: y` line still appears every cycle (existing alerts unaffected).
- No exceptions/tracebacks.
- `/live` Telegram command still responds normally.

Since no UPS is currently known to be on battery, a real alert can't be forced — this verification confirms the new code path runs cleanly against production data every cycle without breaking anything existing. The alert logic itself is already covered by the Task 4 unit tests (confirm delay, repeat interval, recovery duration, exclusion, dual-channel dispatch).

- [ ] **Step 5: Update project memory**

Record in `project_mlff_monitoring.md` that this feature is live, and that v3 Grupa B (parked) is next.
