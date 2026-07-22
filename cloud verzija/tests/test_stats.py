import os
import sqlite3
import tempfile
from contextlib import closing
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


def test_day_stats_attributes_outage_count_to_the_day_it_started_not_every_overlapping_day(db_path):
    # Outage starts 23:50 on day 1, ends 00:10 on day 2 (crosses midnight).
    t0 = datetime(2026, 7, 13, 23, 50, 0)
    stats.open_initial_period(db_path, "HOST-E", "DOWN", t0)
    t1 = datetime(2026, 7, 14, 0, 10, 0)
    stats.record_transition(db_path, "HOST-E", "UP", t1)

    day1_start, day1_end = stats.local_day_bounds(date(2026, 7, 13), ZoneInfo("UTC"))
    day2_start, day2_end = stats.local_day_bounds(date(2026, 7, 14), ZoneInfo("UTC"))
    now = datetime(2026, 7, 14, 12, 0, 0)

    day1_result = stats.day_stats(db_path, day1_start, day1_end, now)
    day2_result = stats.day_stats(db_path, day2_start, day2_end, now)

    # The outage started on day 1, so it should count as 1 outage there...
    assert day1_result["HOST-E"]["outage_count"] == 1
    assert day1_result["HOST-E"]["downtime_seconds"] == pytest.approx(600, abs=1)  # 10 min (23:50-24:00)

    # ...and 0 additional outages on day 2, even though 10 minutes of downtime
    # (00:00-00:10) genuinely occurred on day 2 and must still be counted in duration.
    assert day2_result["HOST-E"]["outage_count"] == 0
    assert day2_result["HOST-E"]["downtime_seconds"] == pytest.approx(600, abs=1)  # 10 min (00:00-00:10)


def test_open_initial_period_is_noop_if_periods_already_exist(db_path):
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    stats.open_initial_period(db_path, "HOST-D", "UP", t0)
    stats.open_initial_period(db_path, "HOST-D", "DOWN", t0 + timedelta(minutes=5))

    start, end = stats.local_day_bounds(date(2026, 7, 13), ZoneInfo("UTC"))
    result = stats.day_stats(db_path, start, end, t0 + timedelta(hours=1))
    # Second call must be ignored - status stays UP, no DOWN period created.
    assert result["HOST-D"]["downtime_seconds"] == 0


def test_open_and_close_ups_power_period(db_path):
    t0 = datetime(2026, 7, 22, 10, 0, 0)
    stats.open_ups_power_period(db_path, "UPS-11", "ERR", t0)

    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT hostname, status_text, start_ts, end_ts FROM ups_power_periods WHERE hostname = ?",
            ("UPS-11",),
        ).fetchone()
    assert row == ("UPS-11", "ERR", t0.isoformat(), None)

    t1 = t0 + timedelta(minutes=5)
    stats.close_ups_power_period(db_path, "UPS-11", t1)

    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT end_ts FROM ups_power_periods WHERE hostname = ?", ("UPS-11",)
        ).fetchone()
    assert row[0] == t1.isoformat()


def test_close_ups_power_period_is_noop_if_none_open(db_path):
    # Must not raise even when there's nothing open to close.
    stats.close_ups_power_period(db_path, "UPS-99", datetime(2026, 7, 22, 10, 0, 0))
