import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import stats
from scraper import Device, UpsPowerStatus
from service import ServiceState, check_ups_power, run_once


def make_device(hostname, ip, status):
    return Device(portal_id="1", hostname=hostname, ip=ip, status=status, duration="1h", last_change="")


def make_ups_status(hostname, ip, status_text, battery_pct):
    return UpsPowerStatus(hostname=hostname, ip=ip, status_text=status_text, battery_pct=battery_pct)


def base_cfg(**overrides):
    cfg = {
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_user": "", "smtp_password": "",
        "email_recipients": [], "telegram_bot_token": "TOKEN", "telegram_chat_ids": ["111"],
        "notify_email": False, "notify_telegram": True,
        "notify_threshold_alert": True, "notify_ups_alert": True,
        "down_threshold_minutes": 60, "ups_alert_delay_minutes": 3,
        "ups_power_confirm_minutes": 3,
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
    # daily_report_time is pushed past t0/t1 so this test (unrelated to daily
    # reports) doesn't incidentally also trigger the >= catch-up report path.
    cfg = base_cfg(daily_report_time="23:59")
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 10, 0, 0)
    run_once(cfg, db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    t1 = t0 + timedelta(minutes=1)
    notes = run_once(cfg, db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t1)

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

    # New outage starts at t3; down_since resets on recovery, so the 60-min
    # threshold must elapse again counting from t3, not from the original t0 outage.
    t3 = t2 + timedelta(minutes=1)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t3)

    t4 = t3 + timedelta(minutes=61)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "DOWN")], t4)
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


def test_run_once_sends_daily_report_even_if_exact_minute_is_missed(db_path):
    """A transient failure during the exact report minute shouldn't
    permanently skip the day's report - the next cycle should catch up."""
    state = ServiceState()
    t0 = datetime(2026, 7, 13, 0, 0, 0)
    run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t0)

    # Poll cycle lands at 09:03, three minutes AFTER the configured 09:01 report
    # time - simulating that the 09:01 cycle itself was skipped (e.g. fetch failed).
    t1 = datetime(2026, 7, 14, 9, 3, 0)
    notes = run_once(base_cfg(), db_path, ZoneInfo("UTC"), state, [make_device("HOST-A", "10.0.0.1", "UP")], t1)

    assert len([n for n in notes if "Dnevni izvestaj" in n.text]) == 1


def test_get_config_reads_new_env_vars(monkeypatch):
    monkeypatch.setenv("DOWN_THRESHOLD_MINUTES", "45")
    monkeypatch.setenv("NOTIFY_UPS_ALERT", "false")
    import service
    cfg = service.get_config()
    assert cfg["down_threshold_minutes"] == 45
    assert cfg["notify_ups_alert"] is False


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

    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT hostname, status_text, end_ts FROM ups_power_periods WHERE hostname = ?", ("UPS-11",)
        ).fetchone()
    assert row == ("UPS-11", "ERR", None)


def test_get_config_reads_ups_power_confirm_minutes(monkeypatch):
    monkeypatch.setenv("UPS_POWER_CONFIRM_MINUTES", "5")
    import service
    cfg = service.get_config()
    assert cfg["ups_power_confirm_minutes"] == 5
