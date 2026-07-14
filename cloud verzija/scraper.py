import re
import ssl
from datetime import datetime, timedelta

import requests
import urllib3
from bs4 import BeautifulSoup
from dataclasses import dataclass
from requests.adapters import HTTPAdapter
from typing import List, Optional

# Suppress SSL warnings for internal corporate network
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_DURATION_RE = re.compile(
    r"(?:(?P<days>\d+)d)?\s*(?:(?P<hours>\d+)h)?\s*(?:(?P<minutes>\d+)m)?\s*(?:(?P<seconds>\d+)s)?"
)


class _NoVerifyAdapter(HTTPAdapter):
    """HTTPS adapter with an explicit no-verify SSL context.

    Passing our own SSLContext keeps urllib3 from falling back to
    context.load_default_certs(), which reads the OS certificate
    store and can raise ssl.SSLError('[ASN1] nested asn1 error') on
    machines with a malformed certificate in that store — even though
    verify=False makes the certs unnecessary in the first place.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


_session = requests.Session()
_session.mount("https://", _NoVerifyAdapter())


@dataclass
class Device:
    portal_id: str
    hostname: str
    ip: str
    status: str
    duration: str
    last_change: str

    @property
    def is_up(self) -> bool:
        return self.status.strip().upper() == "UP"

    @property
    def key(self) -> str:
        return self.hostname


def _parse_duration(text: str) -> Optional[timedelta]:
    """Parse a duration string like '105d 23h 47m 24s' into a timedelta."""
    match = _DURATION_RE.search(text)
    if not match or not any(match.groupdict().values()):
        return None
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    return timedelta(
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )


def _last_change_from_duration(duration_text: str) -> str:
    delta = _parse_duration(duration_text)
    if delta is None:
        return ""
    return (datetime.now() - delta).strftime("%d.%m.%Y %H:%M:%S")


def fetch_devices(url: str, timeout: int = 15) -> List[Device]:
    """Fetch and parse device list from the monitoring page."""
    response = _session.get(url, timeout=timeout, verify=False)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    devices = []

    table = soup.find("table", id="devicesTable") or soup.find("table")
    if not table:
        raise ValueError("Table not found on page – HTML structure may have changed.")

    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cols = row.find_all(["td", "th"])
        if len(cols) < 4:
            continue

        portal_id = cols[0].get_text(strip=True)

        hostname_span = cols[1].find("span", class_="hostname")
        ip_span = cols[1].find("span", class_="dim")
        hostname = hostname_span.get_text(strip=True) if hostname_span else cols[1].get_text(strip=True)
        ip = ip_span.get_text(strip=True) if ip_span else ""

        status = cols[2].get_text(strip=True)

        duration = cols[3].get_text(strip=True)
        last_change = _last_change_from_duration(duration)

        if not hostname:
            continue

        devices.append(Device(
            portal_id=portal_id,
            hostname=hostname,
            ip=ip,
            status=status,
            duration=duration,
            last_change=last_change,
        ))

    return devices
