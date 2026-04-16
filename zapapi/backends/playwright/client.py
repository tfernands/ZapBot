from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

from ...config import ZapAPIConfig
from ...errors import ChatNotFoundException, NoOpenChatException, WhatsAppWebTimeoutException
from ...models import AuthStatus, ChatMessage, ChatRef, ChatSummary, HistoryPage, PollResult
from ...state import InboxState
from .parser import WhatsAppParser
from .selectors import (
    ATTACH_BUTTON_SELECTORS,
    CHAT_LIST_ITEM_SELECTORS,
    CHAT_SUMMARY_EVALUATOR,
    IMAGE_FILE_INPUT_SELECTORS,
    MESSAGE_COMPOSER_SELECTORS,
    MESSAGE_LIST_CONTAINER_SELECTORS,
    MESSAGE_PAYLOAD_EVALUATOR,
    MESSAGE_ROW_SELECTORS,
    MEDIA_SEND_BUTTON_SELECTORS,
    OPEN_CHAT_TITLE_SELECTORS,
    SEARCH_BOX_SELECTORS,
    SEND_BUTTON_SELECTORS,
)
from .session import PlaywrightSession


class PlaywrightZapAPI:
    _MESSAGE_CONTAINER_MARKER = "data-zapapi-message-container"

    def __init__(
        self,
        config: ZapAPIConfig,
        *,
        session: PlaywrightSession | None = None,
        state: InboxState | None = None,
        parser: type[WhatsAppParser] = WhatsAppParser,
    ) -> None:
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(config.debug_level)

        self.session = session or PlaywrightSession(config, logger=self.logger)
        self.state = state or InboxState()
        self.parser = parser

    def start(self) -> "PlaywrightZapAPI":
        self.session.start()
        return self

    def close(self) -> None:
        self.session.close()

    def auth_status(self) -> AuthStatus:
        return self.session.auth_status()

    def ensure_ready(self, timeout_ms: int | None = None) -> AuthStatus:
        return self.session.wait_until_ready(timeout_ms=timeout_ms)

    def current_chat(self) -> ChatRef:
        locator = self.session.find_visible_locator(
            OPEN_CHAT_TITLE_SELECTORS,
            timeout_ms=self.config.action_timeout_ms,
        )
        if locator is None:
            raise NoOpenChatException()
        name = locator.inner_text().strip()
        return ChatRef(name=name)

    def list_chats(self, unread_only: bool = False, limit: int | None = None) -> list[ChatSummary]:
        chats: list[ChatSummary] = []
        for row in self.session.locators_for_any(CHAT_LIST_ITEM_SELECTORS):
            chat = self.parser.parse_chat_summary(self.session.safe_evaluate(row, CHAT_SUMMARY_EVALUATOR))
            if chat is None:
                continue
            if unread_only and chat.unread_count <= 0:
                continue
            chats.append(chat)
            if limit is not None and len(chats) >= limit:
                break
        return chats

    def select_chat(self, target: str | ChatRef, exact_match: bool = True) -> ChatRef:
        target_name = target.name if isinstance(target, ChatRef) else target

        try:
            if self.current_chat().name == target_name:
                return ChatRef(name=target_name)
        except NoOpenChatException:
            pass

        row = self._find_chat_row(target_name, exact_match=exact_match)
        if row is not None:
            row.click()
            self.session.sleep_ui_tick()
            chat = self.current_chat()
            if exact_match and chat.name != target_name:
                raise ChatNotFoundException(target_name, message=f"Chat aberto diverge do alvo: {chat.name}")
            return chat

        search_box = self.session.require_visible_locator(
            SEARCH_BOX_SELECTORS,
            timeout_ms=self.config.action_timeout_ms,
        )
        self.session.clear_editable(search_box)
        search_box.fill(target_name)
        self.session.sleep_ui_tick()

        row = self._find_chat_row(target_name, exact_match=exact_match)
        if row is None:
            self.session.clear_editable(search_box)
            raise ChatNotFoundException(target_name)

        row.click()
        self.session.clear_editable(search_box)
        self.session.sleep_ui_tick()

        chat = self.current_chat()
        if exact_match and chat.name != target_name:
            raise ChatNotFoundException(target_name, message=f"Chat aberto diverge do alvo: {chat.name}")
        return chat

    def send_text(self, chat: str | ChatRef, text: str) -> ChatRef:
        chat_ref = self.select_chat(chat)
        composer = self.session.require_visible_locator(
            MESSAGE_COMPOSER_SELECTORS,
            timeout_ms=self.config.action_timeout_ms,
        )
        composer.click()
        self.session.clear_editable(composer)

        page = self.session.require_page()
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line:
                page.keyboard.insert_text(line)
            if index < len(lines) - 1:
                page.keyboard.press("Shift+Enter")

        send_button = self.session.find_visible_locator(SEND_BUTTON_SELECTORS, timeout_ms=1500)
        if send_button is not None:
            send_button.click()
        else:
            page.keyboard.press("Enter")
        return chat_ref

    def send_image(self, chat: str | ChatRef, image_path: str) -> ChatRef:
        file_path = Path(image_path).expanduser()
        if not file_path.is_file():
            raise FileNotFoundError(f"Arquivo de imagem nao encontrado: {file_path}")

        chat_ref = self.select_chat(chat)
        file_input = self._file_input_locator()
        if file_input is None:
            attach_button = self.session.find_visible_locator(
                ATTACH_BUTTON_SELECTORS,
                timeout_ms=self.config.action_timeout_ms,
            )
            if attach_button is None:
                raise WhatsAppWebTimeoutException("Nao foi possivel localizar o controle de anexo do WhatsApp Web.")
            attach_button.click()
            self.session.sleep_ui_tick()
            file_input = self._file_input_locator()

        if file_input is None:
            raise WhatsAppWebTimeoutException("Nao foi possivel localizar o input de upload de imagem.")

        file_input.set_input_files(str(file_path))
        self.session.sleep_ui_tick()

        send_button = self.session.require_visible_locator(
            MEDIA_SEND_BUTTON_SELECTORS,
            timeout_ms=self.config.action_timeout_ms,
        )
        send_button.click()
        return chat_ref

    def history(
        self,
        chat: str | ChatRef,
        *,
        limit: int = 50,
        before: str | None = None,
        max_scroll_steps: int = 10,
    ) -> HistoryPage:
        if limit <= 0:
            raise ValueError("limit precisa ser maior que zero.")

        chat_ref = self.select_chat(chat)
        self._scroll_to_bottom()

        collected = self._extract_visible_messages(chat_ref)
        seen = {message.id for message in collected}
        scroll_steps = 0
        exhausted = False

        while True:
            page_messages = self._paginate_messages(collected, limit=limit, before=before)
            cursor_available = before is None or any(message.id == before for message in collected)
            if cursor_available and len(page_messages) >= limit:
                break
            if scroll_steps >= max_scroll_steps:
                break

            changed = self._load_older_messages()
            if not changed:
                exhausted = True
                break

            visible = self._extract_visible_messages(chat_ref)
            unseen = [message for message in visible if message.id not in seen]
            if not unseen:
                exhausted = True
                break

            collected = unseen + collected
            seen.update(message.id for message in unseen)
            scroll_steps += 1

        page_messages = self._paginate_messages(collected, limit=limit, before=before)
        has_more = self._has_more_history(collected, page_messages, exhausted=exhausted, before=before)
        cursor = page_messages[0].id if page_messages else None
        return HistoryPage(
            chat=chat_ref,
            messages=tuple(page_messages),
            cursor=cursor,
            has_more=has_more,
        )

    def poll(
        self,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit_per_chat: int = 50,
    ) -> PollResult:
        targets = list(chats) if chats is not None else self.list_chats(unread_only=True)
        scanned_chats: list[ChatRef] = []
        new_messages: list[ChatMessage] = []

        for target in targets:
            try:
                chat_ref = self.select_chat(target)
                scanned_chats.append(chat_ref)
                page = self.history(chat_ref, limit=limit_per_chat)
            except (ChatNotFoundException, NoOpenChatException):
                continue

            new_messages.extend(
                self.parser.mark_new_messages(chat_ref, page.messages, self.state.last_message_id_by_chat)
            )

        return PollResult(
            chats=tuple(scanned_chats),
            messages=tuple(new_messages),
        )

    def _extract_visible_messages(self, chat: ChatRef) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for row in self.session.locators_for_any(MESSAGE_ROW_SELECTORS):
            message = self.parser.parse_message(
                chat,
                self.session.safe_evaluate(row, MESSAGE_PAYLOAD_EVALUATOR),
            )
            if message is not None:
                messages.append(message)
        return self.parser.dedupe_messages(messages)

    def _file_input_locator(self):
        page = self.session.require_page()
        for selector in IMAGE_FILE_INPUT_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        return None

    def _find_chat_row(self, target_name: str, *, exact_match: bool):
        deadline = time.monotonic() + (self.config.action_timeout_ms / 1000)
        normalized_target = target_name.casefold()

        while time.monotonic() < deadline:
            for row in self.session.locators_for_any(CHAT_LIST_ITEM_SELECTORS):
                chat = self.parser.parse_chat_summary(self.session.safe_evaluate(row, CHAT_SUMMARY_EVALUATOR))
                if chat is None:
                    continue

                normalized_name = chat.name.casefold()
                if exact_match and normalized_name == normalized_target:
                    return row
                if not exact_match and normalized_target in normalized_name:
                    return row

            self.session.require_page().wait_for_timeout(self.config.poll_interval_ms)

        return None

    def _scroll_to_bottom(self) -> None:
        container = self._message_container()
        container.evaluate("node => { node.scrollTop = node.scrollHeight; }")
        self.session.sleep_ui_tick()

    def _load_older_messages(self, *, settle_timeout_ms: int | None = None) -> bool:
        container = self._message_container()
        settle_timeout_ms = self.config.action_timeout_ms if settle_timeout_ms is None else settle_timeout_ms
        before = self._visible_message_ids(limit=3)
        container.evaluate(
            """node => {
                const step = Math.max(node.clientHeight * 0.85, 400);
                node.scrollTop = Math.max(0, node.scrollTop - step);
            }"""
        )
        return self._wait_for_message_list_change(before, timeout_ms=settle_timeout_ms)

    def _visible_message_ids(self, limit: int | None = None) -> list[str]:
        chat = self.current_chat()
        visible = self._extract_visible_messages(chat)
        ids = [message.id for message in visible]
        if limit is not None:
            return ids[:limit]
        return ids

    def _wait_for_message_list_change(self, before_ids: list[str], *, timeout_ms: int) -> bool:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            after_ids = self._visible_message_ids(limit=len(before_ids) or None)
            if after_ids != before_ids:
                return True
            self.session.require_page().wait_for_timeout(self.config.poll_interval_ms)
        return False

    def _message_container(self):
        page = self.session.require_page()
        page.locator(f"[{self._MESSAGE_CONTAINER_MARKER}]").evaluate_all(
            f"(nodes) => nodes.forEach((node) => node.removeAttribute('{self._MESSAGE_CONTAINER_MARKER}'))"
        )

        for row in self.session.locators_for_any(MESSAGE_ROW_SELECTORS):
            try:
                found = row.evaluate(
                    """(node, marker) => {
                        let current = node;
                        while (current) {
                            const style = window.getComputedStyle(current);
                            const isScrollable =
                                current.scrollHeight > current.clientHeight + 10 ||
                                style.overflowY === 'auto' ||
                                style.overflowY === 'scroll';
                            if (isScrollable && current.clientHeight > 100) {
                                current.setAttribute(marker, 'true');
                                return true;
                            }
                            current = current.parentElement;
                        }
                        return false;
                    }""",
                    self._MESSAGE_CONTAINER_MARKER,
                )
            except Exception:
                continue

            if not found:
                continue

            container = page.locator(f"[{self._MESSAGE_CONTAINER_MARKER}='true']").first
            if container.count() > 0 and container.is_visible():
                return container

        return self.session.require_visible_locator(
            MESSAGE_LIST_CONTAINER_SELECTORS,
            timeout_ms=self.config.action_timeout_ms,
        )

    @staticmethod
    def _paginate_messages(
        messages: list[ChatMessage],
        *,
        limit: int,
        before: str | None,
    ) -> list[ChatMessage]:
        if before is None:
            pool = messages
        else:
            try:
                before_index = next(index for index, message in enumerate(messages) if message.id == before)
            except StopIteration:
                pool = []
            else:
                pool = messages[:before_index]
        return pool[-limit:]

    @staticmethod
    def _has_more_history(
        collected: list[ChatMessage],
        page_messages: list[ChatMessage],
        *,
        exhausted: bool,
        before: str | None,
    ) -> bool:
        if not page_messages:
            return not exhausted and bool(collected)

        oldest_page_id = page_messages[0].id
        oldest_page_index = next(
            (index for index, message in enumerate(collected) if message.id == oldest_page_id),
            None,
        )
        if oldest_page_index is None:
            return not exhausted

        if before is not None:
            return oldest_page_index > 0 or not exhausted
        return oldest_page_index > 0 or not exhausted
