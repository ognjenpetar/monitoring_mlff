"""SQLite-backed persistence for device uptime/downtime history."""

import sqlite3
from contextlib import closing
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Dict, Tuple
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

CREATE TABLE IF NOT EXISTS ups_power_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,
    status_text TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_ups_power_periods_hostname ON ups_power_periods(hostname);
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
            if start >= period_start:
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


def open_ups_power_period(db_path: str, hostname: str, status_text: str, ts: datetime) -> None:
    """Open a new 'not AC OK' period for hostname, unless one is already open
    (e.g. a service restart mid-outage must not create a duplicate open row)."""
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM ups_power_periods WHERE hostname = ? AND end_ts IS NULL",
            (hostname,),
        )
        (count,) = cur.fetchone()
        if count == 0:
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
