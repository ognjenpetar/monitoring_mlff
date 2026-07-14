"""
MLFF Monitoring – Cloud/headless servis
Pokreće se bez GUI-ja i čita konfiguraciju iz environment varijabli.

Env varijable:
  SMTP_HOST           (default: smtp.gmail.com)
  SMTP_PORT           (default: 587)
  SMTP_USER           email posiljaoca
  SMTP_PASSWORD       app password
  EMAIL_RECIPIENTS    email adrese odvojene zarezom
  TELEGRAM_BOT_TOKEN  token Telegram bota
  TELEGRAM_CHAT_IDS   chat ID-evi odvojeni zarezom
  NOTIFY_EMAIL        true/false (default: true)
  NOTIFY_TELEGRAM     true/false (default: true)
  CHECK_INTERVAL_SEC  interval provere u sekundama (default: 60)
  MONITOR_URL         URL stranice za monitoring
"""

import os
import time
import logging
from datetime import datetime
from typing import Dict, List

from scraper import fetch_devices, Device
from notifier import send_email, send_telegram, build_notification_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MONITOR_URL = os.environ.get("MONITOR_URL", "https://ot.sdn.rs/portali/")
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "60"))

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
    }


def send_notifications(cfg: dict, changed: List[Device], all_down: List[Device], up_count: int) -> None:
    subject = (
        f"MLFF ALARM – {len([d for d in changed if not d.is_up])} uredjaj(a) DOWN"
        if any(not d.is_up for d in changed)
        else "MLFF – Uredjaj(i) ponovo UP"
    )
    body = build_notification_text(changed, all_down, up_count)

    if cfg["notify_email"] and cfg["smtp_user"] and cfg["smtp_password"]:
        for addr in cfg["email_recipients"]:
            try:
                send_email(
                    cfg["smtp_host"], cfg["smtp_port"],
                    cfg["smtp_user"], cfg["smtp_password"],
                    addr, subject, body,
                )
                log.info("Email -> %s", addr)
            except Exception as e:
                log.error("Email GRESKA (%s): %s", addr, e)
    elif cfg["notify_email"]:
        log.warning("Email nije konfigurisan (SMTP_USER / SMTP_PASSWORD nisu postavljeni)")

    if cfg["notify_telegram"]:
        token = cfg["telegram_bot_token"]
        if not token:
            log.warning("Telegram: TELEGRAM_BOT_TOKEN nije postavljen")
        else:
            for cid in cfg["telegram_chat_ids"]:
                try:
                    send_telegram(token, cid, body)
                    log.info("Telegram -> %s", cid)
                except Exception as e:
                    log.error("Telegram GRESKA (%s): %s", cid, e)


def run() -> None:
    log.info("MLFF Monitoring servis pokrenut. URL: %s  Interval: %ds",
             MONITOR_URL, CHECK_INTERVAL_SEC)
    last_statuses: Dict[str, str] = {}
    first_run = True

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:
            cfg = get_config()
            devices = fetch_devices(MONITOR_URL)
        except Exception as e:
            log.error("[%s] Greska pri dohvatanju: %s", now, e)
            time.sleep(CHECK_INTERVAL_SEC)
            continue

        active = [d for d in devices if d.hostname not in EXCLUDED_HOSTNAMES]
        all_down = [d for d in active if not d.is_up]
        up_count = sum(1 for d in active if d.is_up)

        new_statuses: Dict[str, str] = {d.key: d.status for d in active}
        changed = [
            d for d in active
            if d.key in last_statuses
            and last_statuses[d.key].upper() != d.status.upper()
        ]
        last_statuses = new_statuses

        log.info("[%s] UP: %d  DOWN: %d", now, up_count, len(all_down))

        if changed and not first_run:
            for d in changed:
                log.info("  Promena: %s -> %s", d.hostname, d.status)
            send_notifications(cfg, changed, all_down, up_count)

        first_run = False
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run()
