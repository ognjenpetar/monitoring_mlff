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
            try:
                _poll_telegram_commands(poller, STATS_DB_PATH, tz, state.active_devices, now_utc)
            except Exception as e:
                log.error("Greska pri obradi Telegram komandi: %s", e)

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run()
