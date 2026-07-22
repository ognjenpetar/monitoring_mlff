from bs4 import BeautifulSoup

import scraper
from scraper import UpsPowerStatus, fetch_devices

DEVICES_HTML = """
<table id="devicesTable">
  <tr><th>Portal</th><th>Uredjaj</th><th>Status</th><th>Trajanje</th></tr>
  <tr>
    <td>11</td>
    <td><span class="hostname">SCPA1011-L</span><span class="dim">172.23.11.1</span></td>
    <td>UP</td>
    <td>5d 2h 3m</td>
  </tr>
</table>
"""

UPS_HTML = """
<table id="upsTable">
  <tr><th>Uredjaj</th><th>Status</th><th>Baterija</th><th>Runtime</th></tr>
  <tr>
    <td><span class="hostname">UPS-11</span><span class="dim">172.23.11.4</span></td>
    <td><span class="badge badge-success">AC OK</span></td>
    <td>100%</td>
    <td>7790m</td>
  </tr>
  <tr>
    <td><span class="hostname">UPS-7</span><span class="dim">172.23.7.4</span></td>
    <td><span class="badge badge-danger">ERR</span></td>
    <td>0%</td>
    <td>0m</td>
  </tr>
</table>
"""

FULL_PAGE_HTML = f"<html><body>{DEVICES_HTML}{UPS_HTML}</body></html>"


def test_parse_ups_statuses_parses_ac_ok_and_err_rows():
    soup = BeautifulSoup(UPS_HTML, "html.parser")
    result = scraper._parse_ups_statuses(soup)

    assert len(result) == 2
    assert result[0].hostname == "UPS-11"
    assert result[0].ip == "172.23.11.4"
    assert result[0].status_text == "AC OK"
    assert result[0].battery_pct == 100
    assert result[0].is_ac_ok is True
    assert result[1].hostname == "UPS-7"
    assert result[1].status_text == "ERR"
    assert result[1].is_ac_ok is False


def test_parse_ups_statuses_returns_empty_list_if_table_missing():
    soup = BeautifulSoup("<html><body><p>no ups table</p></body></html>", "html.parser")
    assert scraper._parse_ups_statuses(soup) == []


def test_ups_power_status_portal_id_derived_from_hostname():
    status = UpsPowerStatus(hostname="UPS-11", ip="172.23.11.4", status_text="AC OK", battery_pct=100)
    assert status.portal_id == "11"


def test_ups_power_status_portal_id_falls_back_to_hostname_if_no_dash():
    status = UpsPowerStatus(hostname="WEIRDNAME", ip="", status_text="AC OK", battery_pct=0)
    assert status.portal_id == "WEIRDNAME"


def test_fetch_all_fetches_page_once_and_parses_both_tables(monkeypatch):
    call_count = {"n": 0}

    class FakeResponse:
        text = FULL_PAGE_HTML

        def raise_for_status(self):
            pass

    def fake_get(url, timeout, verify):
        call_count["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(scraper._session, "get", fake_get)

    devices, ups_statuses = scraper.fetch_all("http://fake-url")

    assert call_count["n"] == 1
    assert len(devices) == 1
    assert devices[0].hostname == "SCPA1011-L"
    assert len(ups_statuses) == 2


def test_fetch_devices_still_works_as_before(monkeypatch):
    class FakeResponse:
        text = DEVICES_HTML

        def raise_for_status(self):
            pass

    monkeypatch.setattr(scraper._session, "get", lambda url, timeout, verify: FakeResponse())

    devices = fetch_devices("http://fake-url")
    assert len(devices) == 1
    assert devices[0].hostname == "SCPA1011-L"
