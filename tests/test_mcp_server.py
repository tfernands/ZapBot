from __future__ import annotations

import unittest
from pathlib import Path

from zapapi.models import (
    AuthState,
    AuthStatus,
    ChatRef,
    ChatSummary,
    HistoryPage,
    PollResult,
)
from zapapi.errors import AuthenticationRequiredException
from zapapi.mcp.server import LATEST_PROTOCOL_VERSION, ServerOptions, build_server


class FakeAuthService:
    def status(self) -> AuthStatus:
        return AuthStatus(
            state=AuthState.READY,
            authenticated=True,
            detail="ready",
        )

    def ensure_ready(self, timeout_ms: int | None = None) -> AuthStatus:
        return AuthStatus(
            state=AuthState.READY,
            authenticated=True,
            detail=f"timeout={timeout_ms}",
        )


class FakeChatService:
    def current(self) -> ChatRef:
        return ChatRef(name="Equipe", key="chat-1")

    def list(self, unread_only: bool = False, limit: int | None = None) -> list[ChatSummary]:
        chats = [
            ChatSummary(
                name="Equipe",
                key="chat-1",
                preview="Oi",
                timestamp="10:00",
                unread_count=2,
            ),
            ChatSummary(
                name="Financeiro",
                key="chat-2",
                preview="Planilha",
                timestamp="09:00",
                unread_count=0,
            ),
        ]
        if unread_only:
            chats = [chat for chat in chats if chat.unread_count]
        if limit is None:
            return chats
        return chats[:limit]

    def get(self, target: str | ChatRef, exact_match: bool = True) -> ChatRef:
        if isinstance(target, ChatRef):
            return target
        return ChatRef(name=target, key="resolved")


class FakeMessageService:
    def __init__(self) -> None:
        self.sent_texts: list[tuple[str | ChatRef, str]] = []
        self.sent_images: list[tuple[str | ChatRef, str]] = []

    def send_text(self, chat: str | ChatRef, text: str) -> ChatRef:
        self.sent_texts.append((chat, text))
        if isinstance(chat, ChatRef):
            return chat
        return ChatRef(name=chat, key="chat-text")

    def send_image(self, chat: str | ChatRef, image_path: str) -> ChatRef:
        self.sent_images.append((chat, image_path))
        if isinstance(chat, ChatRef):
            return chat
        return ChatRef(name=chat, key="chat-image")

    def history(
        self,
        chat: str | ChatRef,
        *,
        limit: int = 50,
        before: str | None = None,
        max_scroll_steps: int = 10,
    ) -> HistoryPage:
        selected = chat if isinstance(chat, ChatRef) else ChatRef(name=chat, key="history")
        return HistoryPage(
            chat=selected,
            messages=(),
            cursor=before or "cursor-1",
            has_more=False,
        )


class FakeInboxService:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str | ChatRef] | None, int]] = []

    def poll(
        self,
        *,
        chats: list[str | ChatRef] | None = None,
        limit_per_chat: int = 50,
    ) -> PollResult:
        self.calls.append((chats, limit_per_chat))
        refs = chats or [ChatRef(name="Equipe", key="chat-1")]
        normalized = tuple(
            chat if isinstance(chat, ChatRef) else ChatRef(name=chat, key="from-poll")
            for chat in refs
        )
        return PollResult(chats=normalized, messages=())


class FakeZapAPI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.auth = FakeAuthService()
        self.chats = FakeChatService()
        self.messages = FakeMessageService()
        self.inbox = FakeInboxService()

    def start(self) -> "FakeZapAPI":
        self.started = True
        return self

    def close(self) -> None:
        self.closed = True


class FakeAPIFactory:
    def __init__(self) -> None:
        self.instances: list[FakeZapAPI] = []

    def __call__(self, **kwargs) -> FakeZapAPI:
        api = FakeZapAPI(**kwargs)
        self.instances.append(api)
        return api


class BootstrapAuthService:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode
        self.ensure_ready_calls: list[int | None] = []

    def status(self) -> AuthStatus:
        if self.mode == "qr_required":
            return AuthStatus(
                state=AuthState.QR_REQUIRED,
                authenticated=False,
                detail="qr_required",
            )
        return AuthStatus(
            state=AuthState.READY,
            authenticated=True,
            detail="ready",
        )

    def ensure_ready(self, timeout_ms: int | None = None) -> AuthStatus:
        self.ensure_ready_calls.append(timeout_ms)
        if self.mode == "qr_required":
            raise AuthenticationRequiredException()
        return self.status()


class BootstrapFakeZapAPI(FakeZapAPI):
    def __init__(self, *, auth_mode: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.auth = BootstrapAuthService(mode=auth_mode)


class BootstrapAPIFactory:
    def __init__(self) -> None:
        self.instances: list[BootstrapFakeZapAPI] = []
        self._headless_bootstrap_completed = False

    def __call__(self, **kwargs) -> BootstrapFakeZapAPI:
        headless = kwargs.get("headless", True)
        if headless and not self._headless_bootstrap_completed:
            auth_mode = "qr_required"
        else:
            auth_mode = "ready"
        if not headless:
            self._headless_bootstrap_completed = True
        api = BootstrapFakeZapAPI(auth_mode=auth_mode, **kwargs)
        self.instances.append(api)
        return api


class MCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = FakeAPIFactory()
        self.server = build_server(ServerOptions(), api_factory=self.factory)

    def _initialize_server(self, server=None) -> None:
        current_server = self.server if server is None else server
        current_server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "test-client",
                        "version": "1.0.0",
                    },
                },
            }
        )

    def test_initialize_and_list_tools(self) -> None:
        initialize = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "test-client",
                        "version": "1.0.0",
                    },
                },
            }
        )
        assert isinstance(initialize, dict)
        self.assertEqual(initialize["result"]["protocolVersion"], "2025-06-18")

        tools = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
        assert isinstance(tools, dict)
        names = {tool["name"] for tool in tools["result"]["tools"]}
        self.assertIn("auth_status", names)
        self.assertIn("messages_send_text", names)
        self.assertIn("notify_completion", names)

    def test_unknown_protocol_version_negotiates_to_latest(self) -> None:
        initialize = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "9999-01-01",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "test-client",
                        "version": "1.0.0",
                    },
                },
            }
        )
        assert isinstance(initialize, dict)
        self.assertEqual(initialize["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)

    def test_tool_call_starts_api_once_and_returns_structured_content(self) -> None:
        self._initialize_server()

        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "auth_status",
                    "arguments": {},
                },
            }
        )
        assert isinstance(response, dict)
        self.assertEqual(response["result"]["structuredContent"]["ok"], True)
        self.assertEqual(
            response["result"]["structuredContent"]["status"]["state"],
            "ready",
        )
        self.assertEqual(len(self.factory.instances), 1)
        self.assertTrue(self.factory.instances[0].started)

        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "chats_list",
                    "arguments": {
                        "unread_only": True,
                    },
                },
            }
        )
        assert isinstance(response, dict)
        self.assertEqual(response["result"]["structuredContent"]["count"], 1)
        self.assertEqual(len(self.factory.instances), 1)

    def test_invalid_tool_arguments_return_tool_error(self) -> None:
        self._initialize_server()

        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "messages_send_text",
                    "arguments": {
                        "chat": "",
                        "text": "oi",
                    },
                },
            }
        )
        assert isinstance(response, dict)
        self.assertEqual(response["result"]["isError"], True)
        self.assertEqual(response["result"]["structuredContent"]["ok"], False)

    def test_notify_completion_uses_server_defaults(self) -> None:
        server = build_server(
            ServerOptions(
                notify_chat="Eu",
                notify_message="Refatoracao terminada.",
            ),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "notify_completion",
                    "arguments": {},
                },
            }
        )

        assert isinstance(response, dict)
        self.assertEqual(response["result"]["structuredContent"]["ok"], True)
        self.assertEqual(
            response["result"]["structuredContent"]["sent"]["text"],
            "Refatoracao terminada.",
        )
        self.assertEqual(len(self.factory.instances), 1)
        self.assertEqual(
            self.factory.instances[0].messages.sent_texts,
            [("Eu", "Refatoracao terminada.")],
        )

    def test_notify_completion_requires_chat_when_server_default_is_missing(self) -> None:
        self._initialize_server()

        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "notify_completion",
                    "arguments": {},
                },
            }
        )

        assert isinstance(response, dict)
        self.assertEqual(response["result"]["isError"], True)
        self.assertEqual(response["result"]["structuredContent"]["ok"], False)
        self.assertIn(
            "--notify-chat",
            response["result"]["structuredContent"]["error"]["message"],
        )

    def test_tools_list_respects_allowed_tool_whitelist(self) -> None:
        server = build_server(
            ServerOptions(allowed_tools=("messages_send_text",)),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )

        assert isinstance(response, dict)
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(
            names,
            {"auth_status", "auth_ensure_ready", "messages_send_text"},
        )

    def test_allow_tool_whitelist_auto_includes_auth_tools(self) -> None:
        server = build_server(
            ServerOptions(allowed_tools=("notify_completion",)),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )

        assert isinstance(response, dict)
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("auth_status", names)
        self.assertIn("auth_ensure_ready", names)
        self.assertIn("notify_completion", names)
        self.assertNotIn("messages_send_text", names)

    def test_disallowed_tool_is_blocked_before_api_start(self) -> None:
        server = build_server(
            ServerOptions(allowed_tools=("auth_ensure_ready",)),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "messages_send_text",
                    "arguments": {
                        "chat": "Equipe",
                        "text": "oi",
                    },
                },
            }
        )

        assert isinstance(response, dict)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(len(self.factory.instances), 0)

    def test_required_approval_blocks_execution_until_confirmed(self) -> None:
        server = build_server(
            ServerOptions(require_approval_for=("messages_send_text",)),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        denied = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "messages_send_text",
                    "arguments": {
                        "chat": "Equipe",
                        "text": "oi",
                    },
                },
            }
        )

        assert isinstance(denied, dict)
        self.assertTrue(denied["result"]["isError"])
        self.assertEqual(len(self.factory.instances), 0)

        allowed = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "messages_send_text",
                    "arguments": {
                        "chat": "Equipe",
                        "text": "oi",
                        "approval": {
                            "confirm": True,
                            "reason": "envio autorizado",
                        },
                    },
                },
            }
        )

        assert isinstance(allowed, dict)
        self.assertEqual(allowed["result"]["structuredContent"]["ok"], True)
        self.assertEqual(len(self.factory.instances), 1)
        self.assertEqual(
            self.factory.instances[0].messages.sent_texts,
            [("Equipe", "oi")],
        )

    def test_headless_server_bootstraps_auth_visibly_then_restarts_headless(self) -> None:
        factory = BootstrapAPIFactory()
        server = build_server(ServerOptions(headless=True), api_factory=factory)
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "auth_status",
                    "arguments": {},
                },
            }
        )

        assert isinstance(response, dict)
        self.assertEqual(response["result"]["structuredContent"]["ok"], True)
        self.assertEqual(response["result"]["structuredContent"]["status"]["state"], "ready")
        self.assertEqual(len(factory.instances), 3)
        self.assertEqual(factory.instances[0].kwargs["headless"], True)
        self.assertEqual(factory.instances[1].kwargs["headless"], False)
        self.assertEqual(factory.instances[2].kwargs["headless"], True)
        self.assertTrue(factory.instances[0].closed)
        self.assertTrue(factory.instances[1].closed)
        self.assertFalse(factory.instances[2].closed)
        self.assertEqual(factory.instances[0].auth.ensure_ready_calls, [30000])
        self.assertEqual(factory.instances[1].auth.ensure_ready_calls, [0])
        self.assertEqual(factory.instances[2].auth.ensure_ready_calls, [30000])

    def test_write_chat_allowlist_blocks_unauthorized_chat_before_api_start(self) -> None:
        server = build_server(
            ServerOptions(write_chat_allowlist=("Eu",)),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "messages_send_text",
                    "arguments": {
                        "chat": "Equipe",
                        "text": "oi",
                    },
                },
            }
        )

        assert isinstance(response, dict)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(len(self.factory.instances), 0)

    def test_read_chat_allowlist_filters_chats_list_and_inbox_poll_targets(self) -> None:
        server = build_server(
            ServerOptions(read_chat_allowlist=("Equipe",)),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        chats_list = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "chats_list",
                    "arguments": {},
                },
            }
        )

        assert isinstance(chats_list, dict)
        self.assertEqual(chats_list["result"]["structuredContent"]["count"], 1)
        self.assertEqual(
            chats_list["result"]["structuredContent"]["chats"][0]["name"],
            "Equipe",
        )

        inbox_poll = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "inbox_poll",
                    "arguments": {},
                },
            }
        )

        assert isinstance(inbox_poll, dict)
        self.assertEqual(inbox_poll["result"]["structuredContent"]["ok"], True)
        self.assertEqual(len(self.factory.instances), 1)
        self.assertEqual(
            self.factory.instances[0].inbox.calls,
            [(["Equipe"], 50)],
        )

    def test_deny_unfiltered_inbox_poll_blocks_before_api_start(self) -> None:
        server = build_server(
            ServerOptions(
                read_chat_allowlist=("Equipe",),
                deny_unfiltered_inbox_poll=True,
            ),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "inbox_poll",
                    "arguments": {},
                },
            }
        )

        assert isinstance(response, dict)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(len(self.factory.instances), 0)

    def test_deny_fuzzy_chat_match_blocks_before_api_start(self) -> None:
        server = build_server(
            ServerOptions(deny_fuzzy_chat_match=True),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "chats_get",
                    "arguments": {
                        "target": "Equi",
                        "exact_match": False,
                    },
                },
            }
        )

        assert isinstance(response, dict)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(len(self.factory.instances), 0)

    def test_allowed_image_dir_blocks_paths_outside_whitelist(self) -> None:
        server = build_server(
            ServerOptions(
                allowed_image_dirs=(Path("/tmp/allowed"),),
                write_chat_allowlist=("Equipe",),
            ),
            api_factory=self.factory,
        )
        self._initialize_server(server)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "messages_send_image",
                    "arguments": {
                        "chat": "Equipe",
                        "image_path": "/tmp/blocked/image.png",
                    },
                },
            }
        )

        assert isinstance(response, dict)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(len(self.factory.instances), 0)


if __name__ == "__main__":
    unittest.main()
