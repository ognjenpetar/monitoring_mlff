import requests
import urllib3
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List

# Suppress SSL warnings for internal corporate network
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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


def fetch_devices(url: str, timeout: int = 15) -> List[Device]:
    """Fetch and parse device list from the monitoring page."""
    response = requests.get(url, timeout=timeout, verify=False)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    devices = []

    # Try to find the main data table
    table = soup.find("table")
    if not table:
        raise ValueError("Table not found on page – HTML structure may have changed.")

    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cols = row.find_all(["td", "th"])
        if len(cols) < 6:
            continue
        texts = [c.get_text(strip=True) for c in cols]
        devices.append(Device(
            portal_id=texts[0],
            hostname=texts[1],
            ip=texts[2],
            status=texts[3],
            duration=texts[4],
            last_change=texts[5],
        ))

    return devices
