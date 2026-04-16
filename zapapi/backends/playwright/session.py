from __future__ import annotations

import logging
import time
from typing import Sequence

from ...config import ZapAPIConfig
from ...errors import AuthenticationRequiredException, WhatsAppWebTimeoutException
from ...models import AuthState, AuthStatus
from .selectors import HOME_READY_SELECTORS, QR_CODE_SELECTORS

try:
    from playwright.sync_api import BrowserContext, Error as PlaywrightError, Locator, Page, Playwright, sync_playwright
except ModuleNotFoundError as exc:
    BrowserContext = Locator = Page = Playwright = object  # type: ignore[assignment]
    PlaywrightError = Exception
    sync_playwright = None
    _PLAYWRIGHT_IMPORT_ERROR = exc
else:
    _PLAYWRIGHT_IMPORT_ERROR = None


class PlaywrightSession:

    def __init__(self, config: ZapAPIConfig, *, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def start(self) -> "PlaywrightSession":
        if self.page is not None:
            return self
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright nao esta instalado. "
                "Adicione 'playwright' nas dependencias e execute "
                "'python -m playwright install chromium'."
            ) from _PLAYWRIGHT_IMPORT_ERROR

        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        desktop_chrome = self._playwright.devices["Desktop Chrome"]
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.config.user_data_dir),
            headless=self.config.headless,
            args=list(self.config.browser_args),
            slow_mo=self.config.slow_mo,
            user_agent=desktop_chrome["user_agent"],
            viewport=desktop_chrome["viewport"],
        )
        self.context.set_default_timeout(self.config.action_timeout_ms)
        self.context.set_default_navigation_timeout(self.config.launch_timeout_ms)
        self.context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined,
            });
            """
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(
            self.config.base_url,
            wait_until="domcontentloaded",
            timeout=self.config.launch_timeout_ms,
        )
        self.page.wait_for_timeout(self.config.poll_interval_ms)
        self.logger.info(
            "Playwright inicializado com perfil persistente em %s",
            self.config.user_data_dir,
        )
        return self

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self.context = None
        self.page = None
        self._playwright = None

    def auth_status(self) -> AuthStatus:
        if self.find_visible_locator(HOME_READY_SELECTORS, timeout_ms=0) is not None:
            return AuthStatus(
                state=AuthState.READY,
                authenticated=True,
                detail="WhatsApp Web pronto para uso.",
            )
        if self.find_visible_locator(QR_CODE_SELECTORS, timeout_ms=0) is not None:
            return AuthStatus(
                state=AuthState.QR_REQUIRED,
                authenticated=False,
                detail="Escaneie o QR Code para autenticar a sessao.",
            )
        return AuthStatus(
            state=AuthState.LOADING,
            authenticated=False,
            detail="WhatsApp Web ainda esta carregando.",
        )

    def is_authenticated(self) -> bool:
        return self.auth_status().authenticated

    def wait_until_ready(self, timeout_ms: int | None = None) -> AuthStatus:
        timeout_ms = self.config.launch_timeout_ms if timeout_ms is None else timeout_ms
        page = self.require_page()
        deadline = None if timeout_ms <= 0 else time.monotonic() + (timeout_ms / 1000)

        while True:
            status = self.auth_status()
            if status.state is AuthState.READY:
                return status

            if status.state is AuthState.QR_REQUIRED and self.config.headless:
                raise AuthenticationRequiredException()

            if deadline is not None and time.monotonic() >= deadline:
                if status.state is AuthState.QR_REQUIRED:
                    raise AuthenticationRequiredException()
                raise WhatsAppWebTimeoutException()

            page.wait_for_timeout(self.config.poll_interval_ms)

    def find_visible_locator(
        self,
        selectors: Sequence[str],
        *,
        timeout_ms: int,
    ) -> Locator | None:
        deadline = time.monotonic() + (timeout_ms / 1000) if timeout_ms > 0 else None
        page = self.require_page()

        while True:
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count() > 0 and locator.is_visible():
                        return locator
                except PlaywrightError:
                    continue

            if deadline is None or time.monotonic() >= deadline:
                return None
            page.wait_for_timeout(self.config.poll_interval_ms)

    def require_visible_locator(self, selectors: Sequence[str], *, timeout_ms: int) -> Locator:
        locator = self.find_visible_locator(selectors, timeout_ms=timeout_ms)
        if locator is None:
            raise WhatsAppWebTimeoutException(
                f"Timeout aguardando seletor ficar visivel: {', '.join(selectors)}"
            )
        return locator

    def locators_for_any(self, selectors: Sequence[str]) -> list[Locator]:
        page = self.require_page()
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = locator.count()
            except PlaywrightError:
                continue

            if count <= 0:
                continue
            return [locator.nth(index) for index in range(count)]
        return []

    def safe_evaluate(self, locator: Locator, script: str) -> dict | None:
        try:
            payload = locator.evaluate(script)
        except PlaywrightError:
            return None
        return payload if isinstance(payload, dict) else None

    def clear_editable(self, locator: Locator) -> None:
        try:
            locator.fill("")
            return
        except PlaywrightError:
            pass

        locator.click()
        page = self.require_page()
        for shortcut in ("Meta+A", "Control+A"):
            try:
                page.keyboard.press(shortcut)
                break
            except PlaywrightError:
                continue
        page.keyboard.press("Backspace")

    def sleep_ui_tick(self) -> None:
        self.require_page().wait_for_timeout(self.config.poll_interval_ms)

    def require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("ZapAPI.start() precisa ser executado antes do uso.")
        return self.page
