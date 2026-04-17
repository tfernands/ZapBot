from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

from ...config import ZapAPIConfig
from ...errors import ChatNotFoundException, NoOpenChatException, WhatsAppWebTimeoutException
from ...models import (
    AuthStatus,
    ChatMessage,
    ChatRef,
    ChatSummary,
    HistoryPage,
    InboxEntry,
    MessageDirection,
    PollResult,
    SearchHit,
    SkippedChat,
)
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


class SyncPlaywrightZapAPI:
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

    def start(self) -> "SyncPlaywrightZapAPI":
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

    def list_chats(
        self,
        unread_only: bool = False,
        limit: int | None = None,
        scroll_steps: int = 0,
    ) -> list[ChatSummary]:
        seen_names: set[str] = set()
        chats: list[ChatSummary] = []

        def _collect_visible() -> None:
            for row in self.session.locators_for_any(CHAT_LIST_ITEM_SELECTORS):
                chat = self.parser.parse_chat_summary(self.session.safe_evaluate(row, CHAT_SUMMARY_EVALUATOR))
                if chat is None:
                    continue
                normalized = chat.name.casefold()
                if normalized in seen_names:
                    continue
                if unread_only and chat.unread_count <= 0:
                    continue
                seen_names.add(normalized)
                chats.append(chat)
                if limit is not None and len(chats) >= limit:
                    return

        _collect_visible()

        if scroll_steps > 0 and (limit is None or len(chats) < limit):
            sidebar = self.session.find_visible_locator(
                ("#pane-side",),
                timeout_ms=self.config.action_timeout_ms,
            )
            if sidebar is not None:
                for _ in range(scroll_steps):
                    sidebar.evaluate(
                        "node => { node.scrollTop += node.clientHeight * 0.8; }"
                    )
                    self.session.sleep_ui_tick()
                    _collect_visible()
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
        self.ensure_ready(timeout_ms=self.config.launch_timeout_ms)
        chat_ref = self.select_chat(chat)
        self._scroll_to_bottom()
        visible_before_send = self._stable_visible_messages(chat_ref)
        visible_message_ids = {message.id for message in visible_before_send}
        normalized_text = self.parser._normalize_message_text(text)
        matching_messages_before_send = sum(
            1
            for message in visible_before_send
            if message.direction is MessageDirection.OUTBOUND and message.text == normalized_text
        )
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
        self._wait_for_outbound_text(
            chat_ref,
            text=text,
            before_ids=visible_message_ids,
            before_match_count=matching_messages_before_send,
        )
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
        skipped: list[SkippedChat] = []

        for target in targets:
            try:
                chat_ref = self.select_chat(target)
                scanned_chats.append(chat_ref)
                page = self.history(chat_ref, limit=limit_per_chat)
            except ChatNotFoundException:
                skipped.append(SkippedChat(chat=target, reason="Chat nao encontrado na sidebar ou busca."))
                continue
            except NoOpenChatException:
                skipped.append(SkippedChat(chat=target, reason="Nao foi possivel abrir o chat."))
                continue

            new_messages.extend(
                self.parser.mark_new_messages(chat_ref, page.messages, self.state.last_message_id_by_chat)
            )

        return PollResult(
            chats=tuple(scanned_chats),
            messages=tuple(new_messages),
            skipped=tuple(skipped),
        )

    def inbox(
        self,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit_per_chat: int = 10,
        scroll_steps: int = 3,
        max_chats: int | None = None,
    ) -> list[InboxEntry]:
        """Unified inbox: list chats with their recent messages in one pass."""
        t0 = time.monotonic()

        if chats is not None:
            targets: list[str | ChatRef] = list(chats)
            self.logger.info("inbox: %d chat(s) explicito(s) solicitado(s)", len(targets))
        else:
            targets = self.list_chats(scroll_steps=scroll_steps)
            self.logger.info(
                "inbox: list_chats retornou %d chat(s) (scroll_steps=%d) em %.1fs",
                len(targets), scroll_steps, time.monotonic() - t0,
            )

        if max_chats is not None and len(targets) > max_chats:
            self.logger.info(
                "inbox: limitando de %d para %d chats (max_chats=%d)",
                len(targets), max_chats, max_chats,
            )
            targets = targets[:max_chats]

        entries: list[InboxEntry] = []
        for i, target in enumerate(targets, 1):
            chat_name = target.name if hasattr(target, "name") else str(target)
            t_chat = time.monotonic()
            summary = target if isinstance(target, ChatSummary) else None
            try:
                chat_ref = self.select_chat(target)
                page = self.history(chat_ref, limit=limit_per_chat)
                if summary is None:
                    summary = ChatSummary(
                        name=chat_ref.name,
                        key=chat_ref.key,
                        preview=page.messages[-1].text if page.messages else None,
                        unread_count=0,
                    )
                entries.append(InboxEntry(chat=summary, messages=page.messages))
                self.logger.debug(
                    "inbox: [%d/%d] '%s' — %d msg em %.1fs",
                    i, len(targets), chat_name, len(page.messages), time.monotonic() - t_chat,
                )
            except ChatNotFoundException:
                if summary is None:
                    name = target.name if isinstance(target, ChatRef) else str(target)
                    summary = ChatSummary(name=name)
                entries.append(InboxEntry(
                    chat=summary,
                    messages=(),
                    skipped=True,
                    skip_reason="Chat nao encontrado na sidebar ou busca.",
                ))
                self.logger.warning(
                    "inbox: [%d/%d] '%s' — skipped (nao encontrado) em %.1fs",
                    i, len(targets), chat_name, time.monotonic() - t_chat,
                )
            except NoOpenChatException:
                if summary is None:
                    name = target.name if isinstance(target, ChatRef) else str(target)
                    summary = ChatSummary(name=name)
                entries.append(InboxEntry(
                    chat=summary,
                    messages=(),
                    skipped=True,
                    skip_reason="Nao foi possivel abrir o chat.",
                ))
                self.logger.warning(
                    "inbox: [%d/%d] '%s' — skipped (nao abriu) em %.1fs",
                    i, len(targets), chat_name, time.monotonic() - t_chat,
                )

        self.logger.info(
            "inbox: concluido — %d entries (%d skipped) em %.1fs total",
            len(entries),
            sum(1 for e in entries if e.skipped),
            time.monotonic() - t0,
        )
        return entries

    def search_messages(
        self,
        query: str,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit: int = 20,
        scroll_steps: int = 3,
        max_chats: int | None = None,
    ) -> list[SearchHit]:
        """Search for messages containing query text across one or more chats."""
        t0 = time.monotonic()

        if chats is not None:
            targets: list[str | ChatRef] = list(chats)
        else:
            targets = self.list_chats(scroll_steps=scroll_steps)

        if max_chats is not None and len(targets) > max_chats:
            self.logger.info(
                "search: limitando de %d para %d chats (max_chats=%d)",
                len(targets), max_chats, max_chats,
            )
            targets = targets[:max_chats]

        self.logger.info("search: query='%s' em %d chat(s), limit=%d", query, len(targets), limit)

        query_lower = query.casefold()
        hits: list[SearchHit] = []

        for i, target in enumerate(targets, 1):
            chat_name = target.name if hasattr(target, "name") else str(target)
            try:
                chat_ref = self.select_chat(target)
                page = self.history(chat_ref, limit=50)
                before_count = len(hits)
                for msg in page.messages:
                    if query_lower in msg.text.casefold():
                        hits.append(SearchHit(message=msg, chat=chat_ref))
                        if len(hits) >= limit:
                            self.logger.info(
                                "search: limite de %d hits atingido em [%d/%d] '%s' (%.1fs total)",
                                limit, i, len(targets), chat_name, time.monotonic() - t0,
                            )
                            return hits
                found = len(hits) - before_count
                if found:
                    self.logger.debug("search: [%d/%d] '%s' — %d hit(s)", i, len(targets), chat_name, found)
            except (ChatNotFoundException, NoOpenChatException):
                self.logger.warning("search: [%d/%d] '%s' — skipped", i, len(targets), chat_name)
                continue

        self.logger.info("search: concluido — %d hit(s) em %.1fs", len(hits), time.monotonic() - t0)

        return hits

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

    def _wait_for_outbound_text(
        self,
        chat: ChatRef,
        *,
        text: str,
        before_ids: set[str],
        before_match_count: int,
    ) -> None:
        expected_text = self.parser._normalize_message_text(text)
        deadline = time.monotonic() + (self.config.action_timeout_ms / 1000)

        while time.monotonic() < deadline:
            visible_messages = self._extract_visible_messages(chat)
            matching_messages = [
                message
                for message in visible_messages
                if message.direction is MessageDirection.OUTBOUND and message.text == expected_text
            ]
            if len(matching_messages) > before_match_count:
                return
            if any(message.id not in before_ids for message in matching_messages):
                return
            self.session.require_page().wait_for_timeout(self.config.poll_interval_ms)

        raise WhatsAppWebTimeoutException(
            "A mensagem nao apareceu na conversa apos o envio."
        )

    def _stable_visible_messages(self, chat: ChatRef) -> list[ChatMessage]:
        deadline = time.monotonic() + (self.config.action_timeout_ms / 1000)
        previous_ids: list[str] | None = None
        last_messages: list[ChatMessage] = []

        while time.monotonic() < deadline:
            last_messages = self._extract_visible_messages(chat)
            current_ids = [message.id for message in last_messages]
            if current_ids == previous_ids:
                return last_messages
            previous_ids = current_ids
            self.session.require_page().wait_for_timeout(self.config.poll_interval_ms)

        return last_messages

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


from .threadsafe import ThreadBoundPlaywrightZapAPI


class PlaywrightZapAPI(ThreadBoundPlaywrightZapAPI):
    """Thread-safe public facade over the sync Playwright implementation."""

    def __init__(
        self,
        config: ZapAPIConfig,
        *,
        session: PlaywrightSession | None = None,
        state: InboxState | None = None,
        parser: type[WhatsAppParser] = WhatsAppParser,
    ) -> None:
        super().__init__(
            config=config,
            client_factory=lambda: SyncPlaywrightZapAPI(
                config=config,
                session=session,
                state=state,
                parser=parser,
            ),
        )
