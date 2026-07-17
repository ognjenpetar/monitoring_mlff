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
