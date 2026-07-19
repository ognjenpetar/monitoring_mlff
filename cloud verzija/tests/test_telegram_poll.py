from unittest.mock import patch

from telegram_poll import TelegramCommandPoller, extract_commands, parse_command


def test_parse_command_recognizes_known_commands():
    assert parse_command("/live") == "/live"
    assert parse_command("/stat") == "/stat"
    assert parse_command("/juce") == "/juce"


def test_parse_command_strips_bot_username_suffix():
    assert parse_command("/live@mlff_bot") == "/live"


def test_parse_command_returns_none_for_unknown_text():
    assert parse_command("hello") is None
    assert parse_command("") is None
    assert parse_command(None) is None


def test_extract_commands_filters_by_allowed_chat_id():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 111}, "text": "/live"}},
        {"update_id": 2, "message": {"chat": {"id": 222}, "text": "/stat"}},
    ]
    result = extract_commands(updates, allowed_chat_ids=["111"])
    assert len(result) == 1
    assert result[0]["chat_id"] == "111"
    assert result[0]["command"] == "/live"


def test_extract_commands_ignores_non_command_text():
    updates = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "hej"}}]
    result = extract_commands(updates, allowed_chat_ids=["111"])
    assert result == []


def test_poll_and_dispatch_sends_handler_response_back():
    updates_seq = [[{"update_id": 5, "message": {"chat": {"id": 111}, "text": "/live"}}]]

    def fake_get_updates(token, offset=None):
        return updates_seq.pop(0) if updates_seq else []

    sent = []

    def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    with patch("telegram_poll.get_telegram_updates", side_effect=fake_get_updates), \
         patch("telegram_poll.send_telegram", side_effect=fake_send):
        poller = TelegramCommandPoller("TOKEN", ["111"])
        poller.poll_and_dispatch(lambda chat_id, command: f"echo:{command}")

    assert sent == [("111", "echo:/live")]


def test_poll_and_dispatch_ignores_disallowed_chat_id():
    updates_seq = [[{"update_id": 5, "message": {"chat": {"id": 999}, "text": "/live"}}]]

    def fake_get_updates(token, offset=None):
        return updates_seq.pop(0) if updates_seq else []

    sent = []

    with patch("telegram_poll.get_telegram_updates", side_effect=fake_get_updates), \
         patch("telegram_poll.send_telegram", side_effect=lambda *a: sent.append(a)):
        poller = TelegramCommandPoller("TOKEN", ["111"])
        poller.poll_and_dispatch(lambda chat_id, command: "should not be called")

    assert sent == []
