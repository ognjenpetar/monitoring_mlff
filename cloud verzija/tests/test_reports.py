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
