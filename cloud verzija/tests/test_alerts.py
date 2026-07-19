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
