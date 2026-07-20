# MLFF Monitoring v3 Implementation Plan — Grupa B (alarmi, watchdog, mute, sparkline, sedmični izveštaj)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add portal-level alert grouping, a monitoring-pipeline watchdog, mute/unmute Telegram commands, a 7-day sparkline in `/stat`/`/juce`, and an automatic weekly reliability report to `cloud verzija/service.py`.

**Architecture:** Extends the existing pure/I/O split from v2 — `run_once()` stays free of network calls, a new pure `check_watchdog()` function handles the one genuinely I/O-loop-level concern (tracking consecutive fetch failures) separately, and all new Telegram commands route through the existing `_handle_command()` dispatcher.

**Tech Stack:** Same as v2 — Python 3, stdlib `sqlite3`/`zoneinfo`, `pytest`. No new dependencies.

---

## Context for the implementer

- Spec: `docs/superpowers/specs/2026-07-21-mlff-monitoring-v3-alerts-design.md` — read it first for full rationale.
- Builds directly on v2 (`docs/superpowers/specs/2026-07-14-mlff-monitoring-v2-design.md`, plan `docs/superpowers/plans/2026-07-17-mlff-monitoring-v2.md`), which is already implemented and merged to `main`. All work happens in `cloud verzija/`.
- Current module APIs you'll build on (all already exist and are tested):
  - `scraper.Device(portal_id, hostname, ip, status, duration, last_change)` with properties `is_up: bool`, `key: str` (== hostname).
  - `stats.py`: `init_db`, `record_transition`, `open_initial_period`, `day_stats(db_path, period_start, period_end, now) -> Dict[str, dict]`, `local_day_bounds(local_date, tz) -> Tuple[datetime, datetime]`, `mark_report_sent`, `was_report_sent`.
  - `alerts.AlertTracker`: `should_alert(hostname, now, repeat_minutes) -> bool`, `record_sent(hostname, now)`, `reset(hostname)`. Note: "hostname" is just a string key — it works fine with non-hostname keys like `"__watchdog__"`.
  - `reports.py`: `format_duration(seconds) -> str`, `format_live_status(devices) -> str`, `format_day_report(title, day_stats_by_host) -> str`, `format_threshold_alert(...)`, `format_ups_alert(...)`.
  - `telegram_poll.py`: `parse_command(text) -> Optional[str]`, `extract_commands(updates, allowed_chat_ids) -> List[Dict]`, `TelegramCommandPoller` with `.prime()` and `.poll_and_dispatch(handler)`.
  - `service.py`: `get_config() -> dict`, `Notification(channel, recipient, text, subject="")` dataclass, `ServiceState` dataclass, `run_once(cfg, db_path, tz, state, devices, now_utc) -> List[Notification]`, `_handle_command(command, db_path, tz, active_devices, now_utc) -> str`, `_poll_telegram_commands(...)`, `run()`.
- `EXCLUDED_HOSTNAMES = {"SCPA1046-L-UPS", "SCPA1046-L-IOL"}` in `service.py` — these never appear in `active`, so no extra filtering needed in new code that operates on `active`.
- Run tests from inside `cloud verzija/`: `python -m pytest tests/ -v`. Currently 38 passing.

---

### Task 1: `stats.py` — mutes + weekly report tracking

**Files:**
- Modify: `cloud verzija/stats.py`
- Test: `cloud verzija/tests/test_stats.py`

- [ ] **Step 1: Write the failing tests**

Add to `cloud verzija/tests/test_stats.py` (keep existing imports/fixtures, add these test functions):

```python
def test_mute_and_is_muted(db_path):
    now = datetime(2026, 7, 21, 10, 0, 0)
    assert stats.is_muted(db_path, "HOST-A", now) is False
    stats.mute(db_path, "HOST-A", now + timedelta(hours=3))
    assert stats.is_muted(db_path, "HOST-A", now) is True
    later = now + timedelta(hours=4)
    assert stats.is_muted(db_path, "HOST-A", later) is False


def test_mute_overwrites_existing_expiry(db_path):
    now = datetime(2026, 7, 21, 10, 0, 0)
    stats.mute(db_path, "HOST-A", now + timedelta(hours=1))
    stats.mute(db_path, "HOST-A", now + timedelta(hours=3))
    later = now + timedelta(hours=2)
    assert stats.is_muted(db_path, "HOST-A", later) is True


def test_unmute_clears_mute(db_path):
    now = datetime(2026, 7, 21, 10, 0, 0)
    stats.mute(db_path, "HOST-A", now + timedelta(hours=3))
    stats.unmute(db_path, "HOST-A")
    assert stats.is_muted(db_path, "HOST-A", now) is False


def test_unmute_nonexistent_scope_is_noop(db_path):
    stats.unmute(db_path, "HOST-NEVER-MUTED")


def test_is_muted_effective_checks_both_global_and_scope(db_path):
    now = datetime(2026, 7, 21, 10, 0, 0)
    assert stats.is_muted_effective(db_path, "HOST-A", now) is False
    stats.mute(db_path, "HOST-A", now + timedelta(hours=1))
    assert stats.is_muted_effective(db_path, "HOST-A", now) is True
    assert stats.is_muted_effective(db_path, "HOST-B", now) is False
    stats.mute(db_path, "__ALL__", now + timedelta(hours=1))
    assert stats.is_muted_effective(db_path, "HOST-B", now) is True
    assert stats.is_muted_effective(db_path, None, now) is True


def test_list_active_mutes_excludes_expired(db_path):
    now = datetime(2026, 7, 21, 10, 0, 0)
    stats.mute(db_path, "HOST-A", now + timedelta(hours=1))
    stats.mute(db_path, "HOST-B", now - timedelta(hours=1))
    active = stats.list_active_mutes(db_path, now)
    assert [m["scope"] for m in active] == ["HOST-A"]


def test_purge_expired_mutes_removes_only_expired(db_path):
    now = datetime(2026, 7, 21, 10, 0, 0)
    stats.mute(db_path, "HOST-A", now + timedelta(hours=1))
    stats.mute(db_path, "HOST-B", now - timedelta(hours=1))
    stats.purge_expired_mutes(db_path, now)
    remaining = stats.list_active_mutes(db_path, now - timedelta(hours=2))
    assert [m["scope"] for m in remaining] == ["HOST-A"]


def test_local_week_bounds_spans_seven_days():
    monday = date(2026, 7, 20)
    start, end = stats.local_week_bounds(monday, ZoneInfo("UTC"))
    assert start == datetime(2026, 7, 20, 0, 0, 0)
    assert end == datetime(2026, 7, 27, 0, 0, 0)


def test_mark_and_check_weekly_report_sent(db_path):
    week_start = date(2026, 7, 20)
    assert stats.was_weekly_report_sent(db_path, week_start) is False
    stats.mark_weekly_report_sent(db_path, week_start)
    assert stats.was_weekly_report_sent(db_path, week_start) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_stats.py -v`
Expected: FAIL — `AttributeError: module 'stats' has no attribute 'mute'` (and similar for the other new names)

- [ ] **Step 3: Implement the additions in `stats.py`**

Add to the `SCHEMA` string in `cloud verzija/stats.py` (inside the triple-quoted string, after the existing `sent_reports` table definition):

```python
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

CREATE TABLE IF NOT EXISTS sent_weekly_reports (
    week_start_date TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS mutes (
    scope TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);
"""
```

Add these functions to `cloud verzija/stats.py` (anywhere after `was_report_sent`, at module level):

```python
def mute(db_path: str, scope: str, expires_at: datetime) -> None:
    """Mute alerts for `scope` (a hostname, or '__ALL__' for everything)
    until `expires_at`. Calling again for the same scope replaces the
    expiry (does not stack)."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO mutes (scope, expires_at) VALUES (?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET expires_at = excluded.expires_at",
            (scope, expires_at.isoformat()),
        )
        conn.commit()


def unmute(db_path: str, scope: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM mutes WHERE scope = ?", (scope,))
        conn.commit()


def is_muted(db_path: str, scope: str, now: datetime) -> bool:
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute("SELECT expires_at FROM mutes WHERE scope = ?", (scope,))
        row = cur.fetchone()
    if row is None:
        return False
    return datetime.fromisoformat(row[0]) > now


def is_muted_effective(db_path: str, scope: Optional[str], now: datetime) -> bool:
    """True if globally muted ('__ALL__'), or if `scope` is individually
    muted. Pass scope=None to check only the global mute (used for the
    watchdog alert, which has no hostname)."""
    if is_muted(db_path, "__ALL__", now):
        return True
    if scope is not None and is_muted(db_path, scope, now):
        return True
    return False


def list_active_mutes(db_path: str, now: datetime) -> List[dict]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT scope, expires_at FROM mutes WHERE expires_at > ? ORDER BY expires_at",
            (now.isoformat(),),
        ).fetchall()
    return [
        {"scope": r["scope"], "expires_at": datetime.fromisoformat(r["expires_at"])}
        for r in rows
    ]


def purge_expired_mutes(db_path: str, now: datetime) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM mutes WHERE expires_at <= ?", (now.isoformat(),))
        conn.commit()


def local_week_bounds(week_start_date: date_cls, tz: ZoneInfo) -> Tuple[datetime, datetime]:
    """Convert a Monday calendar date in `tz` into [start, end) as naive UTC
    datetimes spanning 7 days (week_start_date through week_start_date+6)."""
    start_utc, _ = local_day_bounds(week_start_date, tz)
    _, end_utc = local_day_bounds(week_start_date + timedelta(days=6), tz)
    return start_utc, end_utc


def mark_weekly_report_sent(db_path: str, week_start_date: date_cls) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_weekly_reports (week_start_date) VALUES (?)",
            (week_start_date.isoformat(),),
        )
        conn.commit()


def was_weekly_report_sent(db_path: str, week_start_date: date_cls) -> bool:
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT 1 FROM sent_weekly_reports WHERE week_start_date = ?",
            (week_start_date.isoformat(),),
        )
        return cur.fetchone() is not None
```

No new imports needed — `Optional`, `Tuple`, `List`, `Dict`, `date as date_cls`, `datetime`, `timedelta`, `ZoneInfo` are all already imported in `stats.py` from the v2 work. `List` and `Dict` specifically: check the existing `from typing import Dict, Optional, Tuple` line — if `List` isn't already there, add it (`list_active_mutes` returns `List[dict]`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_stats.py -v`
Expected: PASS (all previous + 9 new = 15 tests)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: PASS, 47 total (38 existing + 9 new stats tests)

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/stats.py" "cloud verzija/tests/test_stats.py"
git commit -m "feat: add mute/unmute tracking and weekly report bounds to stats.py"
```

---

### Task 2: `reports.py` — sparkline, portal/watchdog/muted formatters, multi-day support

**Files:**
- Modify: `cloud verzija/reports.py`
- Test: `cloud verzija/tests/test_reports.py`

- [ ] **Step 1: Write the failing tests**

Add to `cloud verzija/tests/test_reports.py` (add `from datetime import datetime` to the existing imports if not already there):

```python
def test_format_sparkline_maps_to_fixed_scale():
    assert format_sparkline([0, 50, 100]) == "▁▄█"


def test_format_sparkline_clamps_out_of_range_values():
    assert format_sparkline([-10, 110]) == "▁█"


def test_format_portal_down_alert_lists_all_hostnames():
    text = format_portal_down_alert("14", ["SCPA1055-R-UPS", "SCPA1055-R-IOL"])
    assert "Portal 14" in text
    assert "2 uredjaja" in text
    assert "SCPA1055-R-UPS" in text
    assert "SCPA1055-R-IOL" in text


def test_format_watchdog_alert_contains_duration():
    text = format_watchdog_alert(1800)
    assert "30m" in text
    assert "mlff.sdn.rs" in text


def test_format_muted_list_empty():
    assert format_muted_list([]) == "Nema aktivnih utisavanja."


def test_format_muted_list_shows_scope_and_expiry():
    mutes = [{"scope": "HOST-A", "expires_at": datetime(2026, 7, 21, 13, 0, 0)}]
    text = format_muted_list(mutes)
    assert "HOST-A" in text
    assert "13:00" in text


def test_format_muted_list_shows_all_devices_label_for_global():
    mutes = [{"scope": "__ALL__", "expires_at": datetime(2026, 7, 21, 13, 0, 0)}]
    text = format_muted_list(mutes)
    assert "SVI UREDJAJI" in text


def test_format_day_report_with_period_days_uses_correct_denominator():
    data = {"A": {"downtime_seconds": 604800 / 10, "outage_count": 1}}
    text = format_day_report("Nedelja", data, period_days=7)
    assert "90.0% uptime" in text
```

Also update the existing `from reports import (...)` line at the top of `test_reports.py` to include the new names:

```python
from reports import (
    format_day_report,
    format_duration,
    format_live_status,
    format_muted_list,
    format_portal_down_alert,
    format_sparkline,
    format_threshold_alert,
    format_ups_alert,
    format_watchdog_alert,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_reports.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_sparkline' from 'reports'`

- [ ] **Step 3: Implement the additions in `reports.py`**

Add these functions to `cloud verzija/reports.py` (anywhere after `format_duration`):

```python
def format_sparkline(values: List[float]) -> str:
    """Render a list of 0-100 percentages as a Unicode sparkline, one
    character per value, mapped onto a fixed 0-100 scale (not the min/max
    of the list) so sparklines from different calls stay comparable."""
    levels = "▁▂▃▄▅▆▇█"
    chars = []
    for v in values:
        v = max(0.0, min(100.0, v))
        idx = int(v / 100 * (len(levels) - 1))
        chars.append(levels[idx])
    return "".join(chars)


def format_portal_down_alert(portal_id: str, hostnames: List[str]) -> str:
    lines = [
        f"MLFF ALARM - Portal {portal_id} kompletno nedostupan ({len(hostnames)} uredjaja)",
        "",
    ]
    lines.extend(f"  {h}" for h in hostnames)
    return "\n".join(lines)


def format_watchdog_alert(duration_seconds: float) -> str:
    return (
        "MLFF UPOZORENJE - servis ne moze da dohvati mlff.sdn.rs\n"
        f"Neprekidno neuspesno: {format_duration(duration_seconds)}\n"
        "Ovo znaci problem sa samim monitoring servisom ili mrezom, ne nuzno sa uredjajima."
    )


def format_muted_list(mutes: List[dict]) -> str:
    if not mutes:
        return "Nema aktivnih utisavanja."
    lines = ["=== Aktivna utisavanja ==="]
    for m in mutes:
        scope = "SVI UREDJAJI" if m["scope"] == "__ALL__" else m["scope"]
        lines.append(f"  {scope}  do {m['expires_at'].strftime('%H:%M %d.%m.%Y')}")
    return "\n".join(lines)
```

Now modify the existing `format_day_report` function signature and its `total_seconds` line. Find:

```python
def format_day_report(title: str, day_stats_by_host: Dict[str, dict]) -> str:
    if not day_stats_by_host:
        return f"=== {title} ===\nNema podataka za ovaj period."

    total = len(day_stats_by_host)
    total_seconds = 86400
```

Replace with:

```python
def format_day_report(title: str, day_stats_by_host: Dict[str, dict], period_days: int = 1) -> str:
    if not day_stats_by_host:
        return f"=== {title} ===\nNema podataka za ovaj period."

    total = len(day_stats_by_host)
    total_seconds = 86400 * period_days
```

(The rest of `format_day_report`'s body is unchanged — it already uses the `total_seconds` variable, so it now correctly scales for multi-day periods.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_reports.py -v`
Expected: PASS (all previous + 8 new = 14 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: PASS, no regressions (existing `format_day_report` calls without `period_days` still work — it defaults to 1)

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/reports.py" "cloud verzija/tests/test_reports.py"
git commit -m "feat: add sparkline, portal/watchdog/muted formatters; generalize format_day_report for multi-day periods"
```

---

### Task 3: `telegram_poll.py` — commands with arguments (`/mute HOSTNAME`, `/unmute HOSTNAME`)

**Files:**
- Modify: `cloud verzija/telegram_poll.py`
- Test: `cloud verzija/tests/test_telegram_poll.py`

- [ ] **Step 1: Write the failing tests**

Add to `cloud verzija/tests/test_telegram_poll.py`:

```python
def test_extract_commands_includes_arg_when_present():
    updates = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "/mute SCPA1055-R-UPS"}}]
    result = extract_commands(updates, allowed_chat_ids=["111"])
    assert result[0]["command"] == "/mute"
    assert result[0]["arg"] == "SCPA1055-R-UPS"


def test_extract_commands_arg_is_none_when_absent():
    updates = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "/mutesve"}}]
    result = extract_commands(updates, allowed_chat_ids=["111"])
    assert result[0]["arg"] is None


def test_poll_and_dispatch_passes_arg_to_handler():
    updates_seq = [[{"update_id": 5, "message": {"chat": {"id": 111}, "text": "/mute HOST-A"}}]]

    def fake_get_updates(token, offset=None):
        return updates_seq.pop(0) if updates_seq else []

    received = []

    def handler(chat_id, command, arg):
        received.append((chat_id, command, arg))
        return "ok"

    with patch("telegram_poll.get_telegram_updates", side_effect=fake_get_updates), \
         patch("telegram_poll.send_telegram", side_effect=lambda *a: None):
        poller = TelegramCommandPoller("TOKEN", ["111"])
        poller.poll_and_dispatch(handler)

    assert received == [("111", "/mute", "HOST-A")]
```

Also fix the two existing tests whose handler lambdas take only `(chat_id, command)` — they need a third `arg` parameter now that `poll_and_dispatch` always passes it. Find and update:

```python
def test_poll_and_dispatch_sends_handler_response_back():
    ...
        poller.poll_and_dispatch(lambda chat_id, command: f"echo:{command}")
```

Change the lambda to:

```python
        poller.poll_and_dispatch(lambda chat_id, command, arg: f"echo:{command}")
```

And:

```python
def test_poll_and_dispatch_ignores_disallowed_chat_id():
    ...
        poller.poll_and_dispatch(lambda chat_id, command: "should not be called")
```

Change the lambda to:

```python
        poller.poll_and_dispatch(lambda chat_id, command, arg: "should not be called")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_telegram_poll.py -v`
Expected: FAIL — `KeyError: 'arg'` on the new tests, and `TypeError: <lambda>() takes 2 positional arguments but 3 were given` on the two existing ones (since you haven't changed the implementation yet, but you already edited the test lambdas — this is expected at this stage since the two edited tests are now testing behavior that doesn't exist yet either; that's fine, all of them should fail for now)

- [ ] **Step 3: Implement in `telegram_poll.py`**

Add `COMMANDS` set update — find:

```python
COMMANDS = {"/live", "/stat", "/juce"}
```

Replace with:

```python
COMMANDS = {"/live", "/stat", "/juce", "/mute", "/mutesve", "/unmute", "/unmutesve", "/muted"}
```

Add a new helper function right after `parse_command`:

```python
def _extract_arg(text: str) -> Optional[str]:
    """Return the text after the command word (e.g. the hostname in
    '/mute HOST-A'), or None if there isn't one."""
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
```

Modify `extract_commands` — find:

```python
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
```

Replace with:

```python
def extract_commands(updates: List[dict], allowed_chat_ids: List[str]) -> List[Dict[str, object]]:
    """Return [{"chat_id": str, "command": str, "update_id": int, "arg": Optional[str]}, ...]
    for messages from allowed chat_ids that match a known command."""
    allowed = set(allowed_chat_ids)
    results = []
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        text = msg.get("text", "")
        command = parse_command(text)
        if chat_id in allowed and command:
            results.append({
                "chat_id": chat_id,
                "command": command,
                "update_id": u.get("update_id"),
                "arg": _extract_arg(text),
            })
    return results
```

Modify `TelegramCommandPoller.poll_and_dispatch` — find:

```python
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

Replace with:

```python
    def poll_and_dispatch(self, handler: Callable[[str, str, Optional[str]], str]) -> None:
        """Fetch new updates; for each recognized command from an allowed chat,
        call handler(chat_id, command, arg) -> response text, and send it back."""
        updates = get_telegram_updates(self._bot_token, offset=self._offset)
        if not updates:
            return
        self._offset = max(u["update_id"] for u in updates) + 1
        for item in extract_commands(updates, self._allowed_chat_ids):
            try:
                response = handler(item["chat_id"], item["command"], item.get("arg"))
                send_telegram(self._bot_token, item["chat_id"], response)
            except Exception:
                log.exception("Greska pri obradi Telegram komande %s", item["command"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_telegram_poll.py -v`
Expected: PASS (all previous + 3 new = 10 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: FAIL — `test_service.py`'s tests that reference `TelegramCommandPoller`/`_poll_telegram_commands`/`_handle_command` are not affected yet since Task 3 doesn't touch `service.py`, but if any existing test in `test_service.py` indirectly exercises the poller's 2-arg handler pattern, it would break here. Check the output: if `test_service.py` fails, it's because `service.py`'s own internal lambda (in `_poll_telegram_commands`) still calls `handler(chat_id, command)` with only 2 args — but `poll_and_dispatch` now calls `handler(chat_id, command, arg)` with 3. This mismatch is expected and will be fixed in Task 5. For now, confirm the failure is isolated to that one integration point and not to `test_telegram_poll.py` itself (which should be fully green).

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/telegram_poll.py" "cloud verzija/tests/test_telegram_poll.py"
git commit -m "feat: support command arguments in telegram_poll.py for /mute HOSTNAME"
```

Note for the implementer: it's expected and fine that `cloud verzija/service.py`'s own `_poll_telegram_commands` lambda is now mismatched with the 3-arg handler contract until Task 5 fixes it. Do not attempt to fix `service.py` in this task — that's out of scope here and handled explicitly in Task 5.

---

### Task 4: `service.py` — config additions + portal-level alert grouping (Predlog 03)

**Files:**
- Modify: `cloud verzija/service.py`
- Test: `cloud verzija/tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

First, update the `base_cfg()` helper in `cloud verzija/tests/test_service.py` to include the three new v3 config keys (needed by this and later tasks). Find:

```python
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
```

Replace with:

```python
def base_cfg(**overrides):
    cfg = {
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_user": "", "smtp_password": "",
        "email_recipients": [], "telegram_bot_token": "TOKEN", "telegram_chat_ids": ["111"],
        "notify_email": False, "notify_telegram": True,
        "notify_threshold_alert": True, "notify_ups_alert": True,
        "down_threshold_minutes": 60, "ups_alert_delay_minutes": 3,
        "alert_repeat_minutes": 120, "daily_report_time": "09:01", "timezone": "UTC",
        "watchdog_threshold_minutes": 30, "mute_duration_minutes": 180,
        "weekly_report_time": "09:01",
    }
    cfg.update(overrides)
    return cfg
```

Add a second device-construction helper right after `make_device` (needed for portal-grouping tests, which must control `portal_id`):

```python
def make_portal_device(portal_id, hostname, ip, status):
    return Device(portal_id=portal_id, hostname=hostname, ip=ip, status=status, duration="1h", last_change="")
```

Add these new tests:

```python
def test_get_config_reads_v3_env_vars(monkeypatch):
    monkeypatch.setenv("WATCHDOG_THRESHOLD_MINUTES", "45")
    monkeypatch.setenv("MUTE_DURATION_MINUTES", "60")
    monkeypatch.setenv("WEEKLY_REPORT_TIME", "08:30")
    import service
    cfg = service.get_config()
    assert cfg["watchdog_threshold_minutes"] == 45
    assert cfg["mute_duration_minutes"] == 60
    assert cfg["weekly_report_time"] == "08:30"


def test_run_once_sends_portal_aggregate_when_all_portal_devices_down_together(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    devices_up = [
        make_portal_device("14", "SCPA1055-R-UPS", "1.1.1.1", "UP"),
        make_portal_device("14", "SCPA1055-R-IOL", "1.1.1.2", "UP"),
    ]
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, devices_up, t0)

    t1 = t0 + timedelta(minutes=1)
    devices_down = [
        make_portal_device("14", "SCPA1055-R-UPS", "1.1.1.1", "DOWN"),
        make_portal_device("14", "SCPA1055-R-IOL", "1.1.1.2", "DOWN"),
    ]
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, devices_down, t1)

    portal_notes = [n for n in notes if "Portal 14" in n.text]
    assert len(portal_notes) == 1
    per_event_notes = [n for n in notes if "Promena statusa" in n.text]
    assert len(per_event_notes) == 1


def test_run_once_no_portal_aggregate_when_only_one_device_down(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    devices_up = [
        make_portal_device("14", "SCPA1055-R-UPS", "1.1.1.1", "UP"),
        make_portal_device("14", "SCPA1055-R-IOL", "1.1.1.2", "UP"),
    ]
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, devices_up, t0)

    t1 = t0 + timedelta(minutes=1)
    devices_partial = [
        make_portal_device("14", "SCPA1055-R-UPS", "1.1.1.1", "DOWN"),
        make_portal_device("14", "SCPA1055-R-IOL", "1.1.1.2", "UP"),
    ]
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, devices_partial, t1)

    assert [n for n in notes if "Portal 14" in n.text] == []


def test_run_once_no_portal_aggregate_when_a_portal_device_is_muted(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    devices_up = [
        make_portal_device("14", "SCPA1055-R-UPS", "1.1.1.1", "UP"),
        make_portal_device("14", "SCPA1055-R-IOL", "1.1.1.2", "UP"),
    ]
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, devices_up, t0)
    stats.mute(db_path, "SCPA1055-R-UPS", t0 + timedelta(hours=3))

    t1 = t0 + timedelta(minutes=1)
    devices_down = [
        make_portal_device("14", "SCPA1055-R-UPS", "1.1.1.1", "DOWN"),
        make_portal_device("14", "SCPA1055-R-IOL", "1.1.1.2", "DOWN"),
    ]
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, devices_down, t1)

    assert [n for n in notes if "Portal 14" in n.text] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: FAIL — `KeyError: 'watchdog_threshold_minutes'` and portal-related tests failing since the aggregate logic doesn't exist yet.

- [ ] **Step 3: Implement in `service.py`**

Add the three new keys to `get_config()`. Find the `return { ... }` block in `get_config()` and add these three lines before the closing `}` (after `"timezone": os.environ.get("TIMEZONE", "Europe/Belgrade"),`):

```python
        "watchdog_threshold_minutes": int(os.environ.get("WATCHDOG_THRESHOLD_MINUTES", "30")),
        "mute_duration_minutes": int(os.environ.get("MUTE_DURATION_MINUTES", "180")),
        "weekly_report_time": os.environ.get("WEEKLY_REPORT_TIME", "09:01"),
```

Add the portal-grouping block to `run_once()`. Find the existing per-event notification block:

```python
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
```

Immediately after this block (still inside `run_once`, before the threshold-alert `if` block), add:

```python
    if changed and not state.first_run and cfg["telegram_chat_ids"]:
        newly_down_portals = {d.portal_id for d in changed if not d.is_up}
        for portal_id in newly_down_portals:
            portal_devices = [d for d in active if d.portal_id == portal_id]
            if len(portal_devices) < 2:
                continue
            if not all(not d.is_up for d in portal_devices):
                continue
            portal_hostnames = [d.hostname for d in portal_devices]
            if any(stats.is_muted_effective(db_path, h, now_utc) for h in portal_hostnames):
                continue
            text = reports.format_portal_down_alert(portal_id, portal_hostnames)
            for cid in cfg["telegram_chat_ids"]:
                notifications.append(Notification("telegram", cid, text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: PASS for `test_get_config_reads_v3_env_vars` and the 3 portal tests. The pre-existing `_poll_telegram_commands`/`TelegramCommandPoller` signature mismatch from Task 3 does NOT affect these tests since none of them exercise the Telegram command-polling path — only `run_once()` and `get_config()` directly.

- [ ] **Step 5: Run the full suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: All pass except possibly a lingering integration mismatch noted in Task 3 (if it manifests as a test failure rather than just a latent bug, it will be fixed in Task 5). If `test_service.py` has no test that actually calls `_poll_telegram_commands`/`run()` end-to-end, there should be no failures at all right now.

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/service.py" "cloud verzija/tests/test_service.py"
git commit -m "feat: add v3 config keys and portal-level alert grouping"
```

---

### Task 5: `service.py` — mute wiring across all alert paths + mute/unmute/muted commands

**Files:**
- Modify: `cloud verzija/service.py`
- Test: `cloud verzija/tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `cloud verzija/tests/test_service.py`:

```python
def test_run_once_purges_expired_mutes_and_skips_muted_threshold_alert(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t0)
    stats.mute(db_path, "HOST-A", t0 + timedelta(hours=3))

    t1 = t0 + timedelta(minutes=61)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

    assert [n for n in notes if "60 min" in n.text] == []


def test_run_once_skips_per_event_notification_for_muted_host(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)
    stats.mute(db_path, "HOST-A", t0 + timedelta(hours=3))

    t1 = t0 + timedelta(minutes=1)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

    assert [n for n in notes if n.channel == "telegram"] == []


def test_run_once_still_records_stats_for_muted_host(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)
    stats.mute(db_path, "HOST-A", t0 + timedelta(hours=3))

    t1 = t0 + timedelta(minutes=1)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

    start, end = stats.local_day_bounds(date(2026, 7, 21), ZoneInfo("UTC"))
    data = stats.day_stats(db_path, start, end, t1)
    assert data["HOST-A"]["outage_count"] == 1


def test_handle_command_mutesve_and_muted(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    response = service._handle_command("/mutesve", None, db_path, tz, [], now, 180)
    assert "utisana" in response.lower()
    listing = service._handle_command("/muted", None, db_path, tz, [], now, 180)
    assert "SVI UREDJAJI" in listing


def test_handle_command_unmutesve(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    service._handle_command("/mutesve", None, db_path, tz, [], now, 180)
    service._handle_command("/unmutesve", None, db_path, tz, [], now, 180)
    listing = service._handle_command("/muted", None, db_path, tz, [], now, 180)
    assert listing == "Nema aktivnih utisavanja."


def test_handle_command_mute_known_host(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    devices = [make_device("HOST-A", "10.0.0.1", "UP")]
    response = service._handle_command("/mute", "HOST-A", db_path, tz, devices, now, 180)
    assert "HOST-A" in response
    assert stats.is_muted(db_path, "HOST-A", now) is True


def test_handle_command_mute_unknown_host(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    devices = [make_device("HOST-A", "10.0.0.1", "UP")]
    response = service._handle_command("/mute", "HOST-DOES-NOT-EXIST", db_path, tz, devices, now, 180)
    assert "Nepoznat" in response
    assert stats.is_muted(db_path, "HOST-DOES-NOT-EXIST", now) is False


def test_handle_command_mute_without_arg(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    response = service._handle_command("/mute", None, db_path, tz, [], now, 180)
    assert "Upotreba" in response


def test_handle_command_unmute_specific_host(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    devices = [make_device("HOST-A", "10.0.0.1", "UP")]
    service._handle_command("/mute", "HOST-A", db_path, tz, devices, now, 180)
    service._handle_command("/unmute", "HOST-A", db_path, tz, devices, now, 180)
    assert stats.is_muted(db_path, "HOST-A", now) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: FAIL — mute-skip tests fail because alert paths don't check mute yet; `_handle_command` tests fail with `TypeError` (wrong number of arguments — current signature is `_handle_command(command, db_path, tz, active_devices, now_utc)`, missing `arg` and `mute_duration_minutes`).

- [ ] **Step 3: Implement in `service.py`**

Add a `purge_expired_mutes` call at the very start of `run_once()`'s body (right after the docstring, before `notifications: List[Notification] = []`):

```python
    stats.purge_expired_mutes(db_path, now_utc)
```

Update the per-event notification block. Find:

```python
    if changed and not state.first_run:
        subject = (
            f"MLFF ALARM – {len([d for d in changed if not d.is_up])} uredjaj(a) DOWN"
            if any(not d.is_up for d in changed)
            else "MLFF – Uredjaj(i) ponovo UP"
        )
        body = build_notification_text(changed, all_down, up_count)
```

Replace with:

```python
    non_muted_changed = [d for d in changed if not stats.is_muted_effective(db_path, d.key, now_utc)]
    if non_muted_changed and not state.first_run:
        subject = (
            f"MLFF ALARM – {len([d for d in non_muted_changed if not d.is_up])} uredjaj(a) DOWN"
            if any(not d.is_up for d in non_muted_changed)
            else "MLFF – Uredjaj(i) ponovo UP"
        )
        body = build_notification_text(non_muted_changed, all_down, up_count)
```

Update the threshold-alert loop. Find:

```python
    if cfg["notify_threshold_alert"]:
        threshold = timedelta(minutes=cfg["down_threshold_minutes"])
        for d in all_down:
            since = state.down_since.get(d.key)
```

Replace with:

```python
    if cfg["notify_threshold_alert"]:
        threshold = timedelta(minutes=cfg["down_threshold_minutes"])
        for d in all_down:
            if stats.is_muted_effective(db_path, d.key, now_utc):
                continue
            since = state.down_since.get(d.key)
```

Update the UPS-alert loop. Find:

```python
    if cfg["notify_ups_alert"]:
        threshold = timedelta(minutes=cfg["ups_alert_delay_minutes"])
        for d in all_down:
            if not d.hostname.endswith("-UPS"):
                continue
            since = state.down_since.get(d.key)
```

Replace with:

```python
    if cfg["notify_ups_alert"]:
        threshold = timedelta(minutes=cfg["ups_alert_delay_minutes"])
        for d in all_down:
            if not d.hostname.endswith("-UPS"):
                continue
            if stats.is_muted_effective(db_path, d.key, now_utc):
                continue
            since = state.down_since.get(d.key)
```

Now update `_handle_command` to accept and handle the new commands. Find:

```python
def _handle_command(command: str, db_path: str, tz: ZoneInfo, active_devices: List[Device], now_utc: datetime) -> str:
    if command == "/live":
        return reports.format_live_status(active_devices)

    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    if command == "/stat":
```

Replace with:

```python
def _handle_command(
    command: str,
    arg: Optional[str],
    db_path: str,
    tz: ZoneInfo,
    active_devices: List[Device],
    now_utc: datetime,
    mute_duration_minutes: int,
) -> str:
    if command == "/live":
        return reports.format_live_status(active_devices)

    if command == "/mutesve":
        expires = now_utc + timedelta(minutes=mute_duration_minutes)
        stats.mute(db_path, "__ALL__", expires)
        return f"Sva obavestenja utisana do {expires.strftime('%H:%M %d.%m.%Y')} (UTC)."

    if command == "/unmutesve":
        stats.unmute(db_path, "__ALL__")
        return "Globalno utisavanje ukinuto."

    if command == "/mute":
        if not arg:
            return "Upotreba: /mute HOSTNAME"
        hostname = arg.strip()
        known = {d.hostname for d in active_devices}
        if hostname not in known:
            return f"Nepoznat uredjaj: {hostname}"
        expires = now_utc + timedelta(minutes=mute_duration_minutes)
        stats.mute(db_path, hostname, expires)
        return f"{hostname} utisan do {expires.strftime('%H:%M %d.%m.%Y')} (UTC)."

    if command == "/unmute":
        if not arg:
            return "Upotreba: /unmute HOSTNAME"
        hostname = arg.strip()
        stats.unmute(db_path, hostname)
        return f"{hostname} vise nije utisan."

    if command == "/muted":
        mutes = stats.list_active_mutes(db_path, now_utc)
        return reports.format_muted_list(mutes)

    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    if command == "/stat":
```

Update `_poll_telegram_commands` to pass `arg` through and pass `mute_duration_minutes`. Find:

```python
def _poll_telegram_commands(
    poller: TelegramCommandPoller, db_path: str, tz: ZoneInfo, active_devices: List[Device], now_utc: datetime
) -> None:
    poller.poll_and_dispatch(
        lambda chat_id, command: _handle_command(command, db_path, tz, active_devices, now_utc)
    )
```

Replace with:

```python
def _poll_telegram_commands(
    poller: TelegramCommandPoller,
    db_path: str,
    tz: ZoneInfo,
    active_devices: List[Device],
    now_utc: datetime,
    mute_duration_minutes: int,
) -> None:
    poller.poll_and_dispatch(
        lambda chat_id, command, arg: _handle_command(
            command, arg, db_path, tz, active_devices, now_utc, mute_duration_minutes
        )
    )
```

Update the one call site of `_poll_telegram_commands` inside `run()`. Find:

```python
        if poller:
            try:
                _poll_telegram_commands(poller, STATS_DB_PATH, tz, state.active_devices, now_utc)
            except Exception as e:
                log.error("Greska pri obradi Telegram komandi: %s", e)
```

Replace with:

```python
        if poller:
            try:
                _poll_telegram_commands(
                    poller, STATS_DB_PATH, tz, state.active_devices, now_utc, cfg["mute_duration_minutes"]
                )
            except Exception as e:
                log.error("Greska pri obradi Telegram komandi: %s", e)
```

Check that `Optional` is imported in `service.py` (it already is, from v2's `from typing import Dict, List, Optional`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: PASS (all previous + 10 new tests)

- [ ] **Step 5: Run the full suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/service.py" "cloud verzija/tests/test_service.py"
git commit -m "feat: wire mute/unmute into all alert paths and add mute Telegram commands"
```

---

### Task 6: `service.py` — watchdog for the monitoring pipeline (Predlog 06)

**Files:**
- Modify: `cloud verzija/service.py`
- Test: `cloud verzija/tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `cloud verzija/tests/test_service.py`:

```python
def test_check_watchdog_no_alert_before_threshold(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    notes = service.check_watchdog(base_cfg(), db_path, state, t0)
    assert notes == []
    assert state.fetch_failure_since == t0


def test_check_watchdog_fires_after_threshold(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    service.check_watchdog(base_cfg(), db_path, state, t0)
    t1 = t0 + timedelta(minutes=31)
    notes = service.check_watchdog(base_cfg(), db_path, state, t1)
    assert len(notes) == 1
    assert "mlff.sdn.rs" in notes[0].text


def test_check_watchdog_does_not_repeat_before_interval(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    cfg = base_cfg()
    service.check_watchdog(cfg, db_path, state, t0)
    t1 = t0 + timedelta(minutes=31)
    service.check_watchdog(cfg, db_path, state, t1)
    t2 = t1 + timedelta(minutes=30)
    notes = service.check_watchdog(cfg, db_path, state, t2)
    assert notes == []


def test_check_watchdog_repeats_after_interval(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    cfg = base_cfg()
    service.check_watchdog(cfg, db_path, state, t0)
    t1 = t0 + timedelta(minutes=31)
    service.check_watchdog(cfg, db_path, state, t1)
    t2 = t1 + timedelta(minutes=121)  # past the 120-minute repeat interval
    notes = service.check_watchdog(cfg, db_path, state, t2)
    assert len(notes) == 1


def test_check_watchdog_respects_global_mute(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    cfg = base_cfg()
    stats.mute(db_path, "__ALL__", t0 + timedelta(hours=3))
    service.check_watchdog(cfg, db_path, state, t0)
    t1 = t0 + timedelta(minutes=31)
    notes = service.check_watchdog(cfg, db_path, state, t1)
    assert notes == []


def test_reset_watchdog_clears_state(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 21, 10, 0, 0)
    service.check_watchdog(base_cfg(), db_path, state, t0)
    service.reset_watchdog(state)
    assert state.fetch_failure_since is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: FAIL — `AttributeError: module 'service' has no attribute 'check_watchdog'`

- [ ] **Step 3: Implement in `service.py`**

Add two new fields to `ServiceState`. Find:

```python
@dataclass
class ServiceState:
    last_statuses: Dict[str, str] = field(default_factory=dict)
    down_since: Dict[str, datetime] = field(default_factory=dict)
    threshold_tracker: AlertTracker = field(default_factory=AlertTracker)
    ups_tracker: AlertTracker = field(default_factory=AlertTracker)
    first_run: bool = True
    last_report_date: Optional[date] = None
    active_devices: List[Device] = field(default_factory=list)
```

Replace with:

```python
@dataclass
class ServiceState:
    last_statuses: Dict[str, str] = field(default_factory=dict)
    down_since: Dict[str, datetime] = field(default_factory=dict)
    threshold_tracker: AlertTracker = field(default_factory=AlertTracker)
    ups_tracker: AlertTracker = field(default_factory=AlertTracker)
    watchdog_tracker: AlertTracker = field(default_factory=AlertTracker)
    first_run: bool = True
    last_report_date: Optional[date] = None
    active_devices: List[Device] = field(default_factory=list)
    fetch_failure_since: Optional[datetime] = None
```

Add two new module-level functions in `service.py` (place them right after `run_once`, before `_dispatch_notifications`):

```python
def check_watchdog(cfg: dict, db_path: str, state: ServiceState, now_utc: datetime) -> List[Notification]:
    """Call once per cycle when fetch_devices() has just failed. Tracks how
    long the fetch has been continuously failing in state.fetch_failure_since,
    and returns a Notification list (empty, or one per chat_id) if the
    configured threshold has been crossed, subject to the same repeat-interval
    and mute rules as other alerts."""
    if state.fetch_failure_since is None:
        state.fetch_failure_since = now_utc
    failure_duration = now_utc - state.fetch_failure_since
    threshold = timedelta(minutes=cfg["watchdog_threshold_minutes"])
    if failure_duration < threshold:
        return []
    if stats.is_muted_effective(db_path, None, now_utc):
        return []
    if not state.watchdog_tracker.should_alert("__watchdog__", now_utc, cfg["alert_repeat_minutes"]):
        return []
    state.watchdog_tracker.record_sent("__watchdog__", now_utc)
    text = reports.format_watchdog_alert(failure_duration.total_seconds())
    return [Notification("telegram", cid, text) for cid in cfg["telegram_chat_ids"]]


def reset_watchdog(state: ServiceState) -> None:
    state.fetch_failure_since = None
    state.watchdog_tracker.reset("__watchdog__")
```

Wire it into `run()`. Find:

```python
        try:
            devices = fetch_devices(MONITOR_URL)
        except Exception as e:
            log.error("[%s] Greska pri dohvatanju: %s", now_str, e)
            devices = None

        if devices is not None:
            notifications = run_once(cfg, STATS_DB_PATH, tz, state, devices, now_utc)
            _dispatch_notifications(cfg, notifications)

            down_count = sum(1 for d in state.active_devices if not d.is_up)
            log.info("[%s] UP: %d  DOWN: %d", now_str, len(state.active_devices) - down_count, down_count)
```

Replace with:

```python
        try:
            devices = fetch_devices(MONITOR_URL)
        except Exception as e:
            log.error("[%s] Greska pri dohvatanju: %s", now_str, e)
            devices = None

        if devices is not None:
            reset_watchdog(state)
            notifications = run_once(cfg, STATS_DB_PATH, tz, state, devices, now_utc)
            _dispatch_notifications(cfg, notifications)

            down_count = sum(1 for d in state.active_devices if not d.is_up)
            log.info("[%s] UP: %d  DOWN: %d", now_str, len(state.active_devices) - down_count, down_count)
        else:
            watchdog_notifications = check_watchdog(cfg, STATS_DB_PATH, state, now_utc)
            if watchdog_notifications:
                _dispatch_notifications(cfg, watchdog_notifications)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: PASS (all previous + 5 new watchdog tests)

- [ ] **Step 5: Run the full suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/service.py" "cloud verzija/tests/test_service.py"
git commit -m "feat: add watchdog alert for sustained fetch failures"
```

---

### Task 7: `service.py` — 7-day sparkline in `/stat` and `/juce` (Predlog 04)

**Files:**
- Modify: `cloud verzija/service.py`
- Test: `cloud verzija/tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `cloud verzija/tests/test_service.py`:

```python
def test_handle_command_stat_includes_sparkline(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    stats.open_initial_period(db_path, "HOST-A", "UP", now - timedelta(days=7))
    response = service._handle_command("/stat", None, db_path, tz, [], now, 180)
    assert "Poslednjih 7 dana:" in response


def test_handle_command_juce_includes_sparkline(db_path):
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 21, 10, 0, 0)
    stats.open_initial_period(db_path, "HOST-A", "UP", now - timedelta(days=7))
    response = service._handle_command("/juce", None, db_path, tz, [], now, 180)
    assert "Poslednjih 7 dana:" in response
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: FAIL — `AssertionError` (the string "Poslednjih 7 dana:" isn't in the response yet)

- [ ] **Step 3: Implement in `service.py`**

Add a helper function right before `_handle_command`:

```python
def _network_uptime_pct_for_day(db_path: str, day: date, tz: ZoneInfo, now_utc: datetime) -> float:
    start, end = stats.local_day_bounds(day, tz)
    window_end = min(end, now_utc)
    total_seconds = (window_end - start).total_seconds()
    if total_seconds <= 0:
        return 100.0
    data = stats.day_stats(db_path, start, window_end, now_utc)
    if not data:
        return 100.0
    total = len(data)
    total_downtime = sum(v["downtime_seconds"] for v in data.values())
    return 100.0 * (1 - total_downtime / (total * total_seconds))
```

Update the `/stat` and `/juce` branches of `_handle_command`. Find:

```python
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
```

Replace with:

```python
    if command == "/stat":
        start_utc, _ = stats.local_day_bounds(now_local.date(), tz)
        day_data = stats.day_stats(db_path, start_utc, now_utc, now_utc)
        text = reports.format_day_report(
            f"Statistika: {now_local.date().strftime('%d.%m.%Y')} (do sada)", day_data
        )
        sparkline_days = [now_local.date() - timedelta(days=i) for i in range(6, -1, -1)]
        values = [_network_uptime_pct_for_day(db_path, d, tz, now_utc) for d in sparkline_days]
        return text + "\nPoslednjih 7 dana: " + reports.format_sparkline(values)
    if command == "/juce":
        yesterday = now_local.date() - timedelta(days=1)
        start_utc, end_utc = stats.local_day_bounds(yesterday, tz)
        day_data = stats.day_stats(db_path, start_utc, end_utc, now_utc)
        text = reports.format_day_report(f"Statistika: {yesterday.strftime('%d.%m.%Y')}", day_data)
        sparkline_days = [yesterday - timedelta(days=i) for i in range(6, -1, -1)]
        values = [_network_uptime_pct_for_day(db_path, d, tz, now_utc) for d in sparkline_days]
        return text + "\nPoslednjih 7 dana: " + reports.format_sparkline(values)
    return "Nepoznata komanda."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: PASS (all previous + 2 new)

- [ ] **Step 5: Run the full suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/service.py" "cloud verzija/tests/test_service.py"
git commit -m "feat: add 7-day network uptime sparkline to /stat and /juce"
```

---

### Task 8: `service.py` — automatic weekly reliability report (Ideja 03)

**Files:**
- Modify: `cloud verzija/service.py`
- Test: `cloud verzija/tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `cloud verzija/tests/test_service.py`:

```python
def test_run_once_sends_weekly_report_on_monday(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 0, 0, 0)  # Monday
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    monday = datetime(2026, 7, 20, 9, 1, 0)  # the following Monday, 09:01
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], monday)

    assert len([n for n in notes if "Sedmicni izvestaj" in n.text]) == 1


def test_run_once_does_not_send_weekly_report_on_non_monday(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 0, 0, 0)  # Monday
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    tuesday = datetime(2026, 7, 21, 9, 1, 0)  # Tuesday
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], tuesday)

    assert [n for n in notes if "Sedmicni izvestaj" in n.text] == []


def test_run_once_does_not_send_weekly_report_twice_same_week(db_path):
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 0, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    monday = datetime(2026, 7, 20, 9, 1, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], monday)
    notes2 = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], monday)

    assert [n for n in notes2 if "Sedmicni izvestaj" in n.text] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: FAIL — no weekly report is ever sent (`assert 1 == 1` becomes `assert 0 == 1` type failure on the first new test)

- [ ] **Step 3: Implement in `service.py`**

Add a new field to `ServiceState`. Find:

```python
    active_devices: List[Device] = field(default_factory=list)
    fetch_failure_since: Optional[datetime] = None
```

Replace with:

```python
    active_devices: List[Device] = field(default_factory=list)
    fetch_failure_since: Optional[datetime] = None
    last_weekly_report_week: Optional[date] = None
```

Add the weekly-report block to `run_once()`, immediately after the existing daily-report block and before `state.first_run = False`. The existing daily-report block ends with:

```python
            stats.mark_report_sent(db_path, today_local)
            state.last_report_date = today_local

    state.first_run = False
    state.active_devices = active
    return notifications
```

Insert the new block between the closing of the daily-report `if` and `state.first_run = False`:

```python
            stats.mark_report_sent(db_path, today_local)
            state.last_report_date = today_local

    if now_local.weekday() == 0:
        weekly_report_time = datetime.strptime(cfg["weekly_report_time"], "%H:%M").time()
        if not state.first_run and now_local.time() >= weekly_report_time:
            today_local = now_local.date()
            week_start = today_local - timedelta(days=7)
            if state.last_weekly_report_week != week_start and not stats.was_weekly_report_sent(db_path, week_start):
                week_end = week_start + timedelta(days=6)
                start_utc, end_utc = stats.local_week_bounds(week_start, tz)
                week_data = stats.day_stats(db_path, start_utc, end_utc, now_utc)
                text = reports.format_day_report(
                    f"Sedmicni izvestaj: {week_start.strftime('%d.%m.%Y')} - {week_end.strftime('%d.%m.%Y')}",
                    week_data,
                    period_days=7,
                )
                for cid in cfg["telegram_chat_ids"]:
                    notifications.append(Notification("telegram", cid, text))
                stats.mark_weekly_report_sent(db_path, week_start)
                state.last_weekly_report_week = week_start

    state.first_run = False
    state.active_devices = active
    return notifications
```

Note: `today_local` is re-declared here (shadowing the daily-report block's own `today_local`, which is scoped inside that `if` block and not visible here) — this is intentional and correct, not a bug; both blocks independently compute "today" from `now_local.date()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "cloud verzija" && python -m pytest tests/test_service.py -v`
Expected: PASS (all previous + 3 new)

- [ ] **Step 5: Run the full suite**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: PASS, no regressions. Total test count should now be substantially higher than the 38 baseline (Tasks 1-8 add roughly 40 new tests across all files).

- [ ] **Step 6: Commit**

```bash
git add "cloud verzija/service.py" "cloud verzija/tests/test_service.py"
git commit -m "feat: add automatic weekly reliability report"
```

---

### Task 9: Update `.env.example` and `UPUTSTVO.md`

**Files:**
- Modify: `cloud verzija/.env.example`
- Modify: `UPUTSTVO.md`

- [ ] **Step 1: Add the three new env vars to `.env.example`**

Edit `cloud verzija/.env.example`, find the existing v2 block:

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

Add immediately after it:

```

# Watchdog, mute, sedmicni izvestaj (v3)
WATCHDOG_THRESHOLD_MINUTES=30
MUTE_DURATION_MINUTES=180
WEEKLY_REPORT_TIME=09:01
```

- [ ] **Step 2: Update the Telegram commands table in `UPUTSTVO.md`**

Find the existing table:

```markdown
| Komanda | Šta vraća |
|---|---|
| `/live` | Trenutni status svih uređaja (koliko je UP/DOWN, lista DOWN uređaja) |
| `/stat` | Statistika od ponoći do sada (današnji dan) |
| `/juce` | Statistika za ceo prethodni dan |

Automatski dnevni izveštaj (isti sadržaj kao `/juce`) stiže svako jutro u 09:01
bez da išta tražiš.
```

Replace with:

```markdown
| Komanda | Šta vraća |
|---|---|
| `/live` | Trenutni status svih uređaja (koliko je UP/DOWN, lista DOWN uređaja) |
| `/stat` | Statistika od ponoći do sada (današnji dan), sa sparkline-om poslednjih 7 dana |
| `/juce` | Statistika za ceo prethodni dan, sa sparkline-om poslednjih 7 dana |
| `/mute HOSTNAME` | Utišava alarme za taj uređaj na 3h (statistika se i dalje beleži) |
| `/mutesve` | Utišava sve alarme (uključujući watchdog) na 3h |
| `/unmute HOSTNAME` | Ranije ukida utišavanje za taj uređaj |
| `/unmutesve` | Ranije ukida globalno utišavanje |
| `/muted` | Prikazuje trenutno aktivna utišavanja |

Automatski dnevni izveštaj (isti sadržaj kao `/juce`) stiže svako jutro u 09:01.
Automatski sedmični izveštaj (rang lista uređaja po downtime-u za prethodnih 7
dana) stiže svakog ponedeljka u 09:01. Ako uređaj padne na dva ili više
uređaja istog portala odjednom, stiže i dodatna agregirana poruka o celom
portalu. Ako servis ne uspe da dohvati `mlff.sdn.rs` neprekidno 30 minuta,
stiže poseban "servis ima problem" alarm.
```

- [ ] **Step 3: Commit**

```bash
git add "cloud verzija/.env.example" UPUTSTVO.md
git commit -m "docs: document v3 env vars and Telegram commands"
```

---

### Task 10: Manual end-to-end smoke test with real data

**Files:** none (verification only)

- [ ] **Step 1: Run the full automated test suite one more time**

Run: `cd "cloud verzija" && python -m pytest tests/ -v`
Expected: All tests pass (should be 82 total: 38 from v2 + 44 new from Tasks 1-8 — 9+8+3+4+10+5+2+3).

- [ ] **Step 2: Get a fresh HTML snapshot and exercise the pipeline against real data**

From a machine with network access to `mlff.sdn.rs` (PowerShell):

```powershell
$r = Invoke-WebRequest -Uri "https://mlff.sdn.rs" -UseBasicParsing; $r.Content | Out-File -FilePath "cloud verzija\smoketest_page.html" -Encoding utf8
```

- [ ] **Step 3: Run a scratch script exercising mute, watchdog, sparkline, and portal grouping together**

From `cloud verzija/`:

```bash
cd "cloud verzija"
python -c "
import sys
sys.path.insert(0, '.')
import scraper

html = open('smoketest_page.html', encoding='utf-8').read()

class FakeResp:
    text = html
    def raise_for_status(self): pass

scraper._session.get = lambda *a, **kw: FakeResp()
devices = scraper.fetch_devices('dummy')
print(f'Parsed {len(devices)} devices')

import service, stats
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

db_path = 'smoketest_stats.db'
if os.path.exists(db_path):
    os.remove(db_path)
stats.init_db(db_path)
cfg = service.get_config()
cfg['telegram_bot_token'] = ''
tz = ZoneInfo('Europe/Belgrade')
state = service.ServiceState()
now = datetime.utcnow()

notes = service.run_once(cfg, db_path, tz, state, devices, now)
print(f'Cycle 1 notifications: {len(notes)} (should be 0, first cycle)')

# Test mute round-trip against a real hostname from the live device list.
real_hostname = devices[0].hostname
resp = service._handle_command('/mute', real_hostname, db_path, tz, state.active_devices, now, 180)
print('mute response:', resp)
assert stats.is_muted(db_path, real_hostname, now)

resp = service._handle_command('/muted', None, db_path, tz, state.active_devices, now, 180)
print('muted list:')
print(resp)

resp = service._handle_command('/unmute', real_hostname, db_path, tz, state.active_devices, now, 180)
print('unmute response:', resp)
assert not stats.is_muted(db_path, real_hostname, now)

# Test /stat sparkline against real data.
resp = service._handle_command('/stat', None, db_path, tz, state.active_devices, now, 180)
print()
print('/stat response:')
print(resp)
assert 'Poslednjih 7 dana:' in resp

# Test watchdog check function directly (does not require an actual fetch failure).
fake_state = service.ServiceState()
watchdog_notes = service.check_watchdog(cfg, db_path, fake_state, now)
print(f'Watchdog notes on first failed-fetch cycle: {len(watchdog_notes)} (should be 0, under threshold)')
"
```

Expected output: real device count printed (matches what `/live` shows in production), mute/unmute round-trip succeeds against a real hostname pulled from the live page, `/muted` shows it while active, `/stat` response includes a `Poslednjih 7 dana:` line with a sparkline, and the watchdog check returns 0 notifications on a single simulated failure (correctly under the 30-minute threshold).

- [ ] **Step 4: Clean up scratch files**

```bash
cd "cloud verzija"
rm -f smoketest_page.html smoketest_stats.db
```

(On Windows PowerShell instead: `Remove-Item "cloud verzija\smoketest_page.html","cloud verzija\smoketest_stats.db" -ErrorAction SilentlyContinue`)

- [ ] **Step 5: Confirm no scratch files leaked into git**

Run: `git status --short`
Expected: clean — `*.db` and the smoketest HTML are not tracked (already covered by root `.gitignore`'s `*.db`/`data/` patterns from v2; if `smoketest_page.html` shows as untracked, it's fine since Step 4 already deleted it).

---

## What this plan intentionally does NOT do

- Does not implement Predlog 01 (web dashboard) or Predlog 02 (public status page) — deferred to a separate spec/plan once the user opens the required port on the Oracle VM.
- Does not touch `app.py` (desktop GUI) or `stabilna verzija/` — out of scope per the spec.
- Does not add email delivery for any new alert type (portal aggregate, watchdog) — Telegram only, consistent with the existing threshold/UPS alert design from v2.
- Does not deploy to the VM — that's a manual step the user does after reviewing the implementation, using the existing `DEPLOY.md` update procedure (`git pull && docker compose down && docker compose up -d --build`).
