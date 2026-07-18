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


def test_get_config_reads_new_env_vars(monkeypatch):
    monkeypatch.setenv("DOWN_THRESHOLD_MINUTES", "45")
    monkeypatch.setenv("NOTIFY_UPS_ALERT", "false")
    import service
    cfg = service.get_config()
    assert cfg["down_threshold_minutes"] == 45
    assert cfg["notify_ups_alert"] is False
