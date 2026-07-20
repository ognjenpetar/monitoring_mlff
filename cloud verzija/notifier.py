import json
import smtplib
import urllib.parse
import urllib.request
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, TYPE_CHECKING

import certifi

if TYPE_CHECKING:
    from scraper import Device


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context from certifi's bundled CA certs instead of the
    OS certificate store. ssl.create_default_context() with no cafile calls
    load_default_certs(), which reads the Windows certificate store and
    raises ssl.SSLError('[ASN1] nested asn1 error') on machines with a
    malformed certificate in that store. Using certifi's CA bundle keeps
    real certificate verification (unlike disabling verification outright)
    while avoiding the broken OS store."""
    return ssl.create_default_context(cafile=certifi.where())


def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient

    msg.attach(MIMEText(body, "plain", "utf-8"))

    context = _ssl_context()
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, recipient, msg.as_string())


def send_telegram(bot_token: str, chat_id: str, message: str) -> None:
    """Send a Telegram message via Bot API (free, no extra libs needed)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))


def get_telegram_updates(bot_token: str, offset: int = None) -> list:
    """Return recent updates (messages sent to the bot) – used to find chat_id
    and to poll for on-demand commands. Pass `offset` (last update_id + 1) to
    avoid reprocessing already-seen messages."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    if offset is not None:
        url += f"?offset={offset}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result.get("result", [])



def build_notification_text(
    changed_devices: List["Device"],
    all_down: List["Device"],
    all_up_count: int,
) -> str:
    lines = [
        "=== MLFF Monitoring - Promena statusa ===",
        f"UP: {all_up_count}   DOWN: {len(all_down)}",
        "",
    ]

    if changed_devices:
        lines.append(f"Promena statusa - {len(changed_devices)} uredjaj(a):")
        for d in changed_devices:
            arrow = "[UP]" if d.is_up else "[DOWN]"
            lines.append(
                f"  {arrow}  Portal {d.portal_id}  {d.hostname}  {d.ip}  "
                f"{d.duration}  {d.last_change}"
            )
        lines.append("")

    if all_down:
        lines.append(f"Trenutno DOWN ({len(all_down)} uredjaj/a):")
        for d in all_down:
            lines.append(
                f"  Portal {d.portal_id}  {d.hostname}  {d.ip}  "
                f"{d.duration}  {d.last_change}"
            )

    return "\n".join(lines)
