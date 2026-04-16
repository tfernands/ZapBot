from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from .backends.playwright import PlaywrightZapAPI
from .config import DEFAULT_PROFILE_PATH, ZapAPIConfig
from .services import AuthService, ChatService, InboxService, MessageService


class ZapAPI:
    def __init__(
        self,
        user_data_dir: str | Path = DEFAULT_PROFILE_PATH,
        *,
        headless: bool = True,
        debug_level: int = logging.INFO,
        browser_args: Iterable[str] | None = None,
        base_url: str = "https://web.whatsapp.com/",
        launch_timeout_ms: int = 30000,
        action_timeout_ms: int = 5000,
        poll_interval_seconds: float = 0.5,
        slow_mo: int = 0,
    ) -> None:
        self.config = ZapAPIConfig.from_kwargs(
            user_data_dir=user_data_dir,
            headless=headless,
            debug_level=debug_level,
            browser_args=browser_args,
            base_url=base_url,
            launch_timeout_ms=launch_timeout_ms,
            action_timeout_ms=action_timeout_ms,
            poll_interval_seconds=poll_interval_seconds,
            slow_mo=slow_mo,
        )
        self._client = PlaywrightZapAPI(config=self.config)
        self.auth = AuthService(self._client)
        self.chats = ChatService(self._client)
        self.messages = MessageService(self._client)
        self.inbox = InboxService(self._client)

    @classmethod
    def connect(
        cls,
        user_data_dir: str | Path = DEFAULT_PROFILE_PATH,
        *,
        headless: bool = True,
        debug_level: int = logging.INFO,
        browser_args: Iterable[str] | None = None,
        base_url: str = "https://web.whatsapp.com/",
        launch_timeout_ms: int = 30000,
        action_timeout_ms: int = 5000,
        poll_interval_seconds: float = 0.5,
        slow_mo: int = 0,
    ) -> "ZapAPI":
        api = cls(
            user_data_dir=user_data_dir,
            headless=headless,
            debug_level=debug_level,
            browser_args=browser_args,
            base_url=base_url,
            launch_timeout_ms=launch_timeout_ms,
            action_timeout_ms=action_timeout_ms,
            poll_interval_seconds=poll_interval_seconds,
            slow_mo=slow_mo,
        )
        return api.start()

    def __enter__(self) -> "ZapAPI":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> "ZapAPI":
        self._client.start()
        return self

    def close(self) -> None:
        self._client.close()
