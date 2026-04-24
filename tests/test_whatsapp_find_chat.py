from __future__ import annotations

import unittest

from zapapi.mcp.handlers import ToolHandlers
from zapapi.mcp.options import ServerOptions
from zapapi.mcp.security import SecurityPolicy
from zapapi.models import ChatSummary


class FakeChatService:
    def __init__(self, chats: list[ChatSummary]) -> None:
        self._chats = chats

    def find(
        self,
        query: str,
        *,
        limit: int = 10,
        scroll_steps: int = 2,
    ) -> list[ChatSummary]:
        return self._chats[:limit]


class FakeAPI:
    def __init__(self, chats: list[ChatSummary]) -> None:
        self.chats = FakeChatService(chats)


class WhatsAppFindChatTests(unittest.TestCase):
    def _handler(self, chats: list[ChatSummary]) -> ToolHandlers:
        return ToolHandlers(
            options=ServerOptions(),
            security=SecurityPolicy(ServerOptions()),
            api_getter=lambda: FakeAPI(chats),
        )

    def test_marks_duplicate_normalized_names_as_ambiguous(self) -> None:
        handler = self._handler([
            ChatSummary(name="Vitoria"),
            ChatSummary(name="Vitória 💚"),
        ])

        result = handler.whatsapp_find_chat({"query": "vitoria"})

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["resolved"], False)
        self.assertEqual(result["ambiguous"], True)
        self.assertIn("mesmo nome apos normalizacao", result["ambiguity_reason"])

    def test_resolves_single_normalized_exact_match(self) -> None:
        handler = self._handler([
            ChatSummary(name="Vitória 💚"),
            ChatSummary(name="Vitoria Trabalho"),
        ])

        result = handler.whatsapp_find_chat({"query": "vitoria"})

        self.assertEqual(result["resolved"], True)
        self.assertEqual(result["ambiguous"], False)
        self.assertEqual(result["resolved_chat"]["name"], "Vitória 💚")


if __name__ == "__main__":
    unittest.main()
