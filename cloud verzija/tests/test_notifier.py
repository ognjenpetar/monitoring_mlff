import json
from unittest.mock import MagicMock, patch

from notifier import get_telegram_updates


def _fake_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


def test_get_telegram_updates_includes_offset_in_url():
    resp = _fake_response({"ok": True, "result": []})
    with patch("notifier.urllib.request.urlopen", return_value=resp) as mock_urlopen:
        get_telegram_updates("TOKEN", offset=42)
        called_request = mock_urlopen.call_args[0][0]
        assert "offset=42" in called_request.full_url


def test_get_telegram_updates_without_offset_omits_param():
    resp = _fake_response({"ok": True, "result": []})
    with patch("notifier.urllib.request.urlopen", return_value=resp) as mock_urlopen:
        get_telegram_updates("TOKEN")
        called_request = mock_urlopen.call_args[0][0]
        assert "offset" not in called_request.full_url
