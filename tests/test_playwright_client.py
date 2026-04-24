from __future__ import annotations

import unittest

from zapapi.backends.playwright.client import SyncPlaywrightZapAPI
from zapapi.backends.playwright.parser import WhatsAppParser
from zapapi.config import ZapAPIConfig
from zapapi.errors import WhatsAppWebTimeoutException
from zapapi.models import AuthState, AuthStatus, ChatRef, ChatTextMessage, MessageDirection


class FakeKeyboard:
    def __init__(self) -> None:
        self.inserted_text: list[str] = []
        self.pressed_keys: list[str] = []

    def insert_text(self, text: str) -> None:
        self.inserted_text.append(text)

    def press(self, key: str) -> None:
        self.pressed_keys.append(key)


class FakePage:
    def __init__(self) -> None:
        self.keyboard = FakeKeyboard()
        self.waits: list[int] = []

    def wait_for_timeout(self, timeout_ms: int) -> None:
        self.waits.append(timeout_ms)


class FakeLocator:
    def __init__(self) -> None:
        self.click_count = 0

    def click(self) -> None:
        self.click_count += 1


class FakeSession:
    def __init__(self) -> None:
        self.page = FakePage()
        self.composer = FakeLocator()
        self.send_button = FakeLocator()
        self.ready_timeouts: list[int | None] = []
        self.cleared = False

    def wait_until_ready(self, timeout_ms: int | None = None) -> AuthStatus:
        self.ready_timeouts.append(timeout_ms)
        return AuthStatus(
            state=AuthState.READY,
            authenticated=True,
            detail="ready",
        )

    def require_visible_locator(self, selectors, *, timeout_ms: int):
        return self.composer

    def clear_editable(self, locator) -> None:
        self.cleared = True

    def require_page(self) -> FakePage:
        return self.page

    def find_visible_locator(self, selectors, *, timeout_ms: int):
        return self.send_button


class FakeSearchSession(FakeSession):
    def __init__(self, *, inside_sidebar: bool, metadata: dict[str, str] | None = None) -> None:
        super().__init__()
        self.search_box = FakeLocator()
        self.inside_sidebar = inside_sidebar
        self.metadata = metadata or {}

    def find_visible_locator(self, selectors, *, timeout_ms: int):
        return self.search_box

    def locator_matches_ancestor(self, locator, selector: str) -> bool:
        return locator is self.search_box and selector == "#side" and self.inside_sidebar

    def locator_text_metadata(self, locator) -> dict[str, str]:
        return self.metadata if locator is self.search_box else {}


class ScriptedPlaywrightZapAPI(SyncPlaywrightZapAPI):
    def __init__(self, *, config: ZapAPIConfig, session: FakeSession, message_batches: list[list[ChatTextMessage]]) -> None:
        super().__init__(config=config, session=session)
        self._message_batches = [list(batch) for batch in message_batches]
        self._last_batch: list[ChatTextMessage] = []

    def select_chat(self, target: str | ChatRef, exact_match: bool = True) -> ChatRef:
        if isinstance(target, ChatRef):
            return target
        return ChatRef(name=target)

    def _extract_visible_messages(self, chat: ChatRef):
        if self._message_batches:
            self._last_batch = self._message_batches.pop(0)
        return list(self._last_batch)

    def _scroll_to_bottom(self) -> None:
        return None

    def _stable_visible_messages(self, chat: ChatRef):
        return self._extract_visible_messages(chat)


class PlaywrightClientSendTextTests(unittest.TestCase):
    def _config(self) -> ZapAPIConfig:
        return ZapAPIConfig.from_kwargs(
            user_data_dir="./userdata/profile/wpp-playwright",
            launch_timeout_ms=1234,
            action_timeout_ms=1,
            poll_interval_seconds=0.01,
        )

    def _outbound(self, chat: ChatRef, message_id: str, text: str) -> ChatTextMessage:
        return ChatTextMessage(
            id=message_id,
            chat=chat,
            sender=chat.name,
            direction=MessageDirection.OUTBOUND,
            text=text,
        )

    def test_send_text_waits_until_message_is_visible(self) -> None:
        session = FakeSession()
        chat = ChatRef(name="Eu")
        client = ScriptedPlaywrightZapAPI(
            config=self._config(),
            session=session,
            message_batches=[
                [],
                [self._outbound(chat, "msg-1", "teste final")],
            ],
        )

        selected_chat = client.send_text(chat, "teste final")

        self.assertEqual(selected_chat.name, "Eu")
        self.assertEqual(session.ready_timeouts, [1234])
        self.assertTrue(session.cleared)
        self.assertEqual(session.composer.click_count, 1)
        self.assertEqual(session.send_button.click_count, 1)
        self.assertEqual(session.page.keyboard.inserted_text, ["teste final"])

    def test_send_text_raises_when_message_does_not_appear(self) -> None:
        session = FakeSession()
        chat = ChatRef(name="Eu")
        client = ScriptedPlaywrightZapAPI(
            config=self._config(),
            session=session,
            message_batches=[
                [],
                [],
            ],
        )

        with self.assertRaises(WhatsAppWebTimeoutException):
            client.send_text(chat, "teste que nao aparece")


class PlaywrightClientSearchBoxTests(unittest.TestCase):
    def _config(self) -> ZapAPIConfig:
        return ZapAPIConfig.from_kwargs(
            user_data_dir="./userdata/profile/wpp-playwright",
            action_timeout_ms=1,
            poll_interval_seconds=0.01,
        )

    def test_sidebar_search_box_accepts_locator_inside_sidebar(self) -> None:
        session = FakeSearchSession(inside_sidebar=True)
        client = SyncPlaywrightZapAPI(config=self._config(), session=session)

        self.assertIs(client._sidebar_search_box(), session.search_box)

    def test_sidebar_search_box_rejects_locator_outside_sidebar(self) -> None:
        session = FakeSearchSession(inside_sidebar=False)
        client = SyncPlaywrightZapAPI(config=self._config(), session=session)

        self.assertIsNone(client._sidebar_search_box())

    def test_sidebar_search_box_accepts_search_label_outside_legacy_side(self) -> None:
        session = FakeSearchSession(
            inside_sidebar=False,
            metadata={"ariaLabel": "Pesquisar ou começar uma nova conversa"},
        )
        client = SyncPlaywrightZapAPI(config=self._config(), session=session)

        self.assertIs(client._sidebar_search_box(), session.search_box)


class WhatsAppParserChatMatchTests(unittest.TestCase):
    def test_chat_match_key_ignores_accents_and_emoji(self) -> None:
        self.assertEqual(
            WhatsAppParser.chat_match_key("Vitória 💚"),
            WhatsAppParser.chat_match_key("vitoria"),
        )


if __name__ == "__main__":
    unittest.main()
