from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_PROFILE_PATH = Path.cwd() / "userdata" / "profile" / "wpp-playwright"
DEFAULT_BROWSER_ARGS = (
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--no-first-run",
    "--disable-blink-features=AutomationControlled",
)
DEFAULT_POLL_INTERVAL_MS = 250
DEFAULT_BASE_URL = "https://web.whatsapp.com/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class ZapAPIConfig:
    user_data_dir: Path
    headless: bool = True
    debug_level: int = logging.INFO
    browser_args: tuple[str, ...] = DEFAULT_BROWSER_ARGS
    base_url: str = DEFAULT_BASE_URL
    user_agent: str = DEFAULT_USER_AGENT
    launch_timeout_ms: int = 30000
    action_timeout_ms: int = 5000
    poll_interval_ms: int = 500
    slow_mo: int = 0

    @classmethod
    def from_kwargs(
        cls,
        user_data_dir: str | Path = DEFAULT_PROFILE_PATH,
        *,
        headless: bool = True,
        debug_level: int = logging.INFO,
        browser_args: Iterable[str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        launch_timeout_ms: int = 30000,
        action_timeout_ms: int = 5000,
        poll_interval_seconds: float = 0.5,
        slow_mo: int = 0,
    ) -> "ZapAPIConfig":
        return cls(
            user_data_dir=Path(user_data_dir),
            headless=headless,
            debug_level=debug_level,
            browser_args=tuple(browser_args or DEFAULT_BROWSER_ARGS),
            base_url=base_url,
            user_agent=user_agent,
            launch_timeout_ms=launch_timeout_ms,
            action_timeout_ms=action_timeout_ms,
            poll_interval_ms=max(int(poll_interval_seconds * 1000), DEFAULT_POLL_INTERVAL_MS),
            slow_mo=slow_mo,
        )
