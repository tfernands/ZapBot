from __future__ import annotations

import time
from typing import Iterator, Sequence

from .backends.playwright import PlaywrightZapAPI
from .models import AuthStatus, ChatRef, ChatSummary, HistoryPage, PollResult


class AuthService:

    def __init__(self, client: PlaywrightZapAPI) -> None:
        self._client = client

    def status(self) -> AuthStatus:
        return self._client.auth_status()

    def ensure_ready(self, timeout_ms: int | None = None) -> AuthStatus:
        return self._client.ensure_ready(timeout_ms=timeout_ms)


class ChatService:

    def __init__(self, client: PlaywrightZapAPI) -> None:
        self._client = client

    def current(self) -> ChatRef:
        return self._client.current_chat()

    def list(self, unread_only: bool = False, limit: int | None = None) -> list[ChatSummary]:
        return self._client.list_chats(unread_only=unread_only, limit=limit)

    def get(self, target: str | ChatRef, exact_match: bool = True) -> ChatRef:
        return self._client.select_chat(target, exact_match=exact_match)


class MessageService:

    def __init__(self, client: PlaywrightZapAPI) -> None:
        self._client = client

    def send_text(self, chat: str | ChatRef, text: str) -> ChatRef:
        return self._client.send_text(chat, text)

    def history(
        self,
        chat: str | ChatRef,
        *,
        limit: int = 50,
        before: str | None = None,
        max_scroll_steps: int = 10,
    ) -> HistoryPage:
        return self._client.history(
            chat,
            limit=limit,
            before=before,
            max_scroll_steps=max_scroll_steps,
        )


class InboxService:

    def __init__(self, client: PlaywrightZapAPI) -> None:
        self._client = client

    def poll(
        self,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit_per_chat: int = 50,
    ) -> PollResult:
        return self._client.poll(chats=chats, limit_per_chat=limit_per_chat)

    def listen(
        self,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit_per_chat: int = 50,
        interval_seconds: float | None = None,
    ) -> Iterator[PollResult]:
        delay = interval_seconds
        if delay is None:
            delay = self._client.config.poll_interval_ms / 1000

        while True:
            result = self.poll(chats=chats, limit_per_chat=limit_per_chat)
            if result.messages:
                yield result
            time.sleep(delay)
