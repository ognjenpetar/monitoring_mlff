import re
import ssl
from datetime import datetime, timedelta

import requests
import urllib3
from bs4 import BeautifulSoup
from dataclasses import dataclass
from requests.adapters import HTTPAdapter
from typing import List, Optional, Tuple

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


@dataclass
class UpsPowerStatus:
    hostname: str        # e.g. "UPS-11"
    ip: str
    status_text: str     # raw text from the site: "AC OK", "ERR", or an unknown 3rd value
    battery_pct: int

    @property
    def is_ac_ok(self) -> bool:
        return self.status_text.strip().upper() == "AC OK"

    @property
    def portal_id(self) -> str:
        """Derive the portal number from the hostname (e.g. 'UPS-11' -> '11')."""
        parts = self.hostname.rsplit("-", 1)
        return parts[1] if len(parts) == 2 else self.hostname

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


def _get_soup(url: str, timeout: int) -> BeautifulSoup:
    response = _session.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def _parse_devices(soup: BeautifulSoup) -> List[Device]:
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


def _parse_ups_statuses(soup: BeautifulSoup) -> List[UpsPowerStatus]:
    """Parse the 'Monitoring UPS-eva' table (id=upsTable). Returns [] if the
    table isn't present on the page - this is a secondary data source and
    shouldn't fail the whole poll cycle if the site temporarily omits it."""
    table = soup.find("table", id="upsTable")
    if not table:
        return []

    statuses = []
    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cols = row.find_all(["td", "th"])
        if len(cols) < 3:
            continue

        hostname_span = cols[0].find("span", class_="hostname")
        ip_span = cols[0].find("span", class_="dim")
        hostname = hostname_span.get_text(strip=True) if hostname_span else cols[0].get_text(strip=True)
        ip = ip_span.get_text(strip=True) if ip_span else ""

        if not hostname:
            continue

        status_text = cols[1].get_text(strip=True)

        battery_text = cols[2].get_text(strip=True).rstrip("%")
        try:
            battery_pct = int(battery_text)
        except ValueError:
            battery_pct = 0

        statuses.append(UpsPowerStatus(
            hostname=hostname,
            ip=ip,
            status_text=status_text,
            battery_pct=battery_pct,
        ))

    return statuses


def fetch_devices(url: str, timeout: int = 15) -> List[Device]:
    """Fetch and parse device list from the monitoring page."""
    soup = _get_soup(url, timeout)
    return _parse_devices(soup)


def fetch_all(url: str, timeout: int = 15) -> Tuple[List[Device], List[UpsPowerStatus]]:
    """Fetch the monitoring page once and parse both the devices table and
    the UPS power table from the same snapshot, so the two are always
    consistent with each other."""
    soup = _get_soup(url, timeout)
    return _parse_devices(soup), _parse_ups_statuses(soup)
