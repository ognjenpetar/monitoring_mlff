"""Polling and dispatch for on-demand Telegram bot commands (/live, /stat, /juce)."""

import logging
from typing import Callable, Dict, List, Optional

from notifier import get_telegram_updates, send_telegram

log = logging.getLogger(__name__)

COMMANDS = {"/live", "/stat", "/juce"}


def parse_command(text: Optional[str]) -> Optional[str]:
    """Extract a known command (without an @botname suffix) from message text."""
    if not text:
        return None
    first_word = text.strip().split()[0].lower() if text.strip() else ""
    if not first_word:
        return None
    first_word = first_word.split("@")[0]
    return first_word if first_word in COMMANDS else None


def extract_commands(updates: List[dict], allowed_chat_ids: List[str]) -> List[Dict[str, object]]:
    """Return [{"chat_id": str, "command": str, "update_id": int}, ...] for
    messages from allowed chat_ids that match a known command."""
    allowed = set(allowed_chat_ids)
    results = []
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        command = parse_command(msg.get("text", ""))
        if chat_id in allowed and command:
            results.append({"chat_id": chat_id, "command": command, "update_id": u.get("update_id")})
    return results


class TelegramCommandPoller:
    """Polls getUpdates for new messages and dispatches recognized commands."""

    def __init__(self, bot_token: str, allowed_chat_ids: List[str]) -> None:
        self._bot_token = bot_token
        self._allowed_chat_ids = allowed_chat_ids
        self._offset: Optional[int] = None

    def prime(self) -> None:
        """Discard any backlog of messages on startup so old commands aren't reprocessed."""
        updates = get_telegram_updates(self._bot_token)
        if updates:
            self._offset = max(u["update_id"] for u in updates) + 1

    def poll_and_dispatch(self, handler: Callable[[str, str], str]) -> None:
        """Fetch new updates; for each recognized command from an allowed chat,
        call handler(chat_id, command) -> response text, and send it back."""
        updates = get_telegram_updates(self._bot_token, offset=self._offset)
        if not updates:
            return
        self._offset = max(u["update_id"] for u in updates) + 1
        for item in extract_commands(updates, self._allowed_chat_ids):
            try:
                response = handler(item["chat_id"], item["command"])
                send_telegram(self._bot_token, item["chat_id"], response)
            except Exception:
                log.exception("Greska pri obradi Telegram komande %s", item["command"])
