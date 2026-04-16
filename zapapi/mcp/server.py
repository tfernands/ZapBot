from __future__ import annotations

import argparse
import json
import logging
import sys
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, TextIO

from zapapi import ChatRef, ZapAPI
from zapapi.config import DEFAULT_PROFILE_PATH
from zapapi.errors import (
    AuthenticationRequiredException,
    ChatNotFoundException,
    NoOpenChatException,
    WhatsAppWebTimeoutException,
)


LOGGER = logging.getLogger(__name__)
JSONRPC_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

CHAT_REF_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "string",
            "description": "Nome do chat, contato ou grupo no WhatsApp.",
        },
        {
            "type": "object",
            "description": "Objeto de chat retornado por outras ferramentas.",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nome visivel do chat.",
                },
                "key": {
                    "type": ["string", "null"],
                    "description": "Identificador interno quando disponivel.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ]
}

APPROVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Aprovacao explicita exigida pela politica do servidor antes de executar a ferramenta."
    ),
    "properties": {
        "confirm": {
            "type": "boolean",
            "description": "Deve ser true para autorizar a execucao.",
        },
        "reason": {
            "type": ["string", "null"],
            "description": "Motivo opcional registrado pelo cliente.",
        },
    },
    "required": ["confirm"],
    "additionalProperties": False,
}

READ_CHAT_TOOLS = frozenset({"messages_history", "inbox_poll"})
WRITE_CHAT_TOOLS = frozenset(
    {
        "notify_completion",
        "messages_send_text",
        "messages_send_image",
    }
)
CHAT_SELECTION_TOOLS = frozenset({"chats_get"})
DEFAULT_ALLOWED_TOOLS = frozenset({"auth_status", "auth_ensure_ready"})


class MCPProtocolError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ToolInputError(Exception):
    pass


class ToolAccessError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ServerOptions:
    user_data_dir: Path = DEFAULT_PROFILE_PATH
    headless: bool = True
    debug_level: int = logging.INFO
    base_url: str = "https://web.whatsapp.com/"
    launch_timeout_ms: int = 30000
    action_timeout_ms: int = 5000
    poll_interval_seconds: float = 0.5
    slow_mo: int = 0
    notify_chat: str | None = None
    notify_message: str = "Refatoracao concluida."
    allowed_tools: tuple[str, ...] | None = None
    read_chat_allowlist: tuple[str, ...] | None = None
    write_chat_allowlist: tuple[str, ...] | None = None
    allowed_image_dirs: tuple[Path, ...] | None = None
    require_approval_for: tuple[str, ...] | None = None
    deny_unfiltered_inbox_poll: bool = False
    deny_fuzzy_chat_match: bool = False


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    annotations: dict[str, Any] | None = None

    def descriptor(self) -> dict[str, Any]:
        tool = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.annotations:
            tool["annotations"] = self.annotations
        return tool


class ZapAPIMCPRuntime:
    def __init__(
        self,
        options: ServerOptions,
        api_factory: Callable[..., ZapAPI] = ZapAPI,
    ) -> None:
        self._options = options
        self._api_factory = api_factory
        self._api: ZapAPI | None = None
        self._allowed_tools = self._normalized_allowed_tool_names(options.allowed_tools)
        self._require_approval_for = self._normalized_tool_names(options.require_approval_for)
        self._read_chat_allowlist = self._normalized_chat_names(options.read_chat_allowlist)
        self._write_chat_allowlist = self._normalized_chat_names(options.write_chat_allowlist)
        self._visible_chat_allowlist = self._build_visible_chat_allowlist()
        self._allowed_image_dirs = self._normalized_image_dirs(options.allowed_image_dirs)
        tools = self._build_tools()
        self._tools = {tool.name: tool for tool in tools}
        self._validate_security_configuration()

    @property
    def tool_descriptors(self) -> list[dict[str, Any]]:
        return [tool.descriptor() for tool in self._tools.values() if self._is_tool_allowed(tool.name)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise MCPProtocolError(METHOD_NOT_FOUND, f"Ferramenta desconhecida: {name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise MCPProtocolError(INVALID_PARAMS, "O campo arguments deve ser um objeto JSON.")

        try:
            self._authorize_tool(name, arguments)
            sanitized_arguments = self._sanitize_arguments(arguments)
        except (ToolInputError, ToolAccessError) as exc:
            return self._tool_error(str(exc))

        try:
            result = tool.handler(sanitized_arguments)
        except (ToolInputError, ToolAccessError) as exc:
            return self._tool_error(str(exc))
        except (
            AuthenticationRequiredException,
            ChatNotFoundException,
            FileNotFoundError,
            NoOpenChatException,
            ValueError,
            WhatsAppWebTimeoutException,
        ) as exc:
            return self._tool_error(str(exc))
        except Exception as exc:  # pragma: no cover - defensive path
            LOGGER.exception("Falha ao executar a ferramenta %s", name)
            return self._tool_error(f"{type(exc).__name__}: {exc}")

        return self._tool_success(result)

    def close(self) -> None:
        if self._api is None:
            return
        self._api.close()
        self._api = None

    def _tool_success(self, payload: dict[str, Any]) -> dict[str, Any]:
        content = {"ok": True, **payload}
        return {
            "content": [{"type": "text", "text": self._encode_json(content)}],
            "structuredContent": content,
        }

    def _tool_error(self, message: str) -> dict[str, Any]:
        content = {
            "ok": False,
            "error": {
                "message": message,
            },
        }
        return {
            "content": [{"type": "text", "text": self._encode_json(content)}],
            "structuredContent": content,
            "isError": True,
        }

    def _get_api(self) -> ZapAPI:
        if self._api is None:
            self._api = self._start_api(headless=self._options.headless)
            self._ensure_authenticated_session()
        return self._api

    def _start_api(self, *, headless: bool) -> ZapAPI:
        api = self._api_factory(
            user_data_dir=self._options.user_data_dir,
            headless=headless,
            debug_level=self._options.debug_level,
            base_url=self._options.base_url,
            launch_timeout_ms=self._options.launch_timeout_ms,
            action_timeout_ms=self._options.action_timeout_ms,
            poll_interval_seconds=self._options.poll_interval_seconds,
            slow_mo=self._options.slow_mo,
        )
        api.start()
        return api

    def _ensure_authenticated_session(self) -> None:
        if self._api is None:
            raise RuntimeError("A sessao MCP precisa ser iniciada antes da autenticacao.")

        try:
            self._api.auth.ensure_ready(timeout_ms=self._options.launch_timeout_ms)
            return
        except AuthenticationRequiredException:
            if not self._options.headless:
                raise

        LOGGER.info(
            "Sessao nao autenticada em headless; abrindo Chromium visivel para autenticar o WhatsApp Web."
        )
        self._api.close()

        bootstrap_api = self._start_api(headless=False)
        try:
            bootstrap_api.auth.ensure_ready(timeout_ms=0)
        finally:
            bootstrap_api.close()

        self._api = self._start_api(headless=self._options.headless)
        self._api.auth.ensure_ready(timeout_ms=self._options.launch_timeout_ms)

    def _build_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="auth_status",
                title="Auth Status",
                description=(
                    "Abre a sessao do WhatsApp Web se necessario e retorna o status atual da autenticacao."
                ),
                input_schema=self._schema_for_tool("auth_status", self._empty_schema()),
                handler=self._auth_status,
                annotations={
                    "readOnlyHint": True,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="auth_ensure_ready",
                title="Ensure Auth Ready",
                description=(
                    "Garante que a sessao esteja pronta para uso. "
                    "Se a sessao nao estiver autenticada em headless, o servidor abre uma autenticacao visivel temporaria."
                ),
                input_schema=self._schema_for_tool(
                    "auth_ensure_ready",
                    {
                    "type": "object",
                    "properties": {
                        "timeout_ms": {
                            "type": ["integer", "null"],
                            "minimum": 0,
                            "description": "Timeout opcional em milissegundos.",
                        }
                    },
                    "additionalProperties": False,
                    },
                ),
                handler=self._auth_ensure_ready,
                annotations={
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="chats_current",
                title="Current Chat",
                description="Retorna o chat atualmente aberto no WhatsApp Web.",
                input_schema=self._schema_for_tool("chats_current", self._empty_schema()),
                handler=self._chats_current,
                annotations={
                    "readOnlyHint": True,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="chats_list",
                title="List Chats",
                description="Lista os chats visiveis na barra lateral do WhatsApp Web.",
                input_schema=self._schema_for_tool(
                    "chats_list",
                    {
                    "type": "object",
                    "properties": {
                        "unread_only": {
                            "type": "boolean",
                            "description": "Se verdadeiro, retorna apenas chats com mensagens nao lidas.",
                            "default": False,
                        },
                        "limit": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "description": "Limite opcional de chats retornados.",
                        },
                    },
                    "additionalProperties": False,
                    },
                ),
                handler=self._chats_list,
                annotations={
                    "readOnlyHint": True,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="chats_get",
                title="Select Chat",
                description=(
                    "Seleciona um chat pelo nome ou por um objeto de chat retornado anteriormente e o deixa aberto na UI."
                ),
                input_schema=self._schema_for_tool(
                    "chats_get",
                    {
                    "type": "object",
                    "properties": {
                        "target": CHAT_REF_SCHEMA,
                        "exact_match": {
                            "type": "boolean",
                            "description": "Se verdadeiro, exige correspondencia exata no nome do chat.",
                            "default": True,
                        },
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                    },
                ),
                handler=self._chats_get,
                annotations={
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="notify_completion",
                title="Notify Completion",
                description=(
                    "Envia uma notificacao de conclusao para o chat configurado no servidor "
                    "ou para o chat informado na chamada."
                ),
                input_schema=self._schema_for_tool(
                    "notify_completion",
                    {
                    "type": "object",
                    "properties": {
                        "chat": CHAT_REF_SCHEMA,
                        "text": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Texto opcional da notificacao. "
                                "Se omitido, usa a mensagem padrao configurada no servidor."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    },
                ),
                handler=self._notify_completion,
                annotations={
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="messages_send_text",
                title="Send Text",
                description="Envia uma mensagem de texto para um chat.",
                input_schema=self._schema_for_tool(
                    "messages_send_text",
                    {
                    "type": "object",
                    "properties": {
                        "chat": CHAT_REF_SCHEMA,
                        "text": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Texto a ser enviado.",
                        },
                    },
                    "required": ["chat", "text"],
                    "additionalProperties": False,
                    },
                ),
                handler=self._messages_send_text,
                annotations={
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="messages_send_image",
                title="Send Image",
                description="Envia uma imagem para um chat usando o fluxo de anexo do WhatsApp Web.",
                input_schema=self._schema_for_tool(
                    "messages_send_image",
                    {
                    "type": "object",
                    "properties": {
                        "chat": CHAT_REF_SCHEMA,
                        "image_path": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Caminho local para a imagem.",
                        },
                    },
                    "required": ["chat", "image_path"],
                    "additionalProperties": False,
                    },
                ),
                handler=self._messages_send_image,
                annotations={
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="messages_history",
                title="Message History",
                description="Busca o historico de um chat com suporte a paginacao por cursor.",
                input_schema=self._schema_for_tool(
                    "messages_history",
                    {
                    "type": "object",
                    "properties": {
                        "chat": CHAT_REF_SCHEMA,
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Quantidade maxima de mensagens a retornar.",
                            "default": 50,
                        },
                        "before": {
                            "type": ["string", "null"],
                            "description": "Cursor retornado por uma chamada anterior.",
                        },
                        "max_scroll_steps": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Numero maximo de scrolls retroativos ao buscar historico.",
                            "default": 10,
                        },
                    },
                    "required": ["chat"],
                    "additionalProperties": False,
                    },
                ),
                handler=self._messages_history,
                annotations={
                    "readOnlyHint": True,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="inbox_poll",
                title="Poll Inbox",
                description=(
                    "Faz uma leitura pontual da inbox e retorna novas mensagens dos chats informados."
                ),
                input_schema=self._schema_for_tool(
                    "inbox_poll",
                    {
                    "type": "object",
                    "properties": {
                        "chats": {
                            "type": ["array", "null"],
                            "description": "Lista opcional de chats para filtrar o polling.",
                            "items": CHAT_REF_SCHEMA,
                        },
                        "limit_per_chat": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Numero maximo de mensagens por chat.",
                            "default": 50,
                        },
                    },
                    "additionalProperties": False,
                    },
                ),
                handler=self._inbox_poll,
                annotations={
                    "readOnlyHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            ),
            ToolDefinition(
                name="session_close",
                title="Close Session",
                description="Fecha o browser e libera a sessao Playwright aberta por este servidor MCP.",
                input_schema=self._schema_for_tool("session_close", self._empty_schema()),
                handler=self._session_close,
                annotations={
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            ),
        ]

    def _auth_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_no_arguments(arguments)
        status = self._get_api().auth.status()
        return {"status": self._serialize(status)}

    def _auth_ensure_ready(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout_ms = self._optional_int(arguments, "timeout_ms", minimum=0)
        status = self._get_api().auth.ensure_ready(timeout_ms=timeout_ms)
        return {"status": self._serialize(status)}

    def _chats_current(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_no_arguments(arguments)
        chat = self._get_api().chats.current()
        self._ensure_visible_chat_allowed(chat, tool_name="chats_current")
        return {"chat": self._serialize(chat)}

    def _chats_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unread_only = self._optional_bool(arguments, "unread_only", default=False)
        limit = self._optional_int(arguments, "limit", minimum=1)
        query_limit = limit if self._visible_chat_allowlist is None else None
        chats = self._get_api().chats.list(unread_only=unread_only, limit=query_limit)
        chats = self._filter_visible_chats(chats)
        if limit is not None:
            chats = chats[:limit]
        return {
            "count": len(chats),
            "chats": self._serialize(chats),
        }

    def _chats_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._chat_value(arguments, "target")
        exact_match = self._optional_bool(arguments, "exact_match", default=True)
        chat = self._get_api().chats.get(target, exact_match=exact_match)
        return {"chat": self._serialize(chat)}

    def _messages_send_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chat = self._chat_value(arguments, "chat")
        text = self._required_string(arguments, "text")
        selected_chat = self._get_api().messages.send_text(chat, text)
        return {
            "chat": self._serialize(selected_chat),
            "sent": {
                "message_type": "text",
                "text": text,
            },
        }

    def _notify_completion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chat = self._optional_chat_value(arguments, "chat")
        if chat is None:
            configured_chat = self._options.notify_chat
            if configured_chat is None:
                raise ToolInputError(
                    "Nenhum chat de notificacao configurado. "
                    "Inicie o servidor com --notify-chat ou informe o campo 'chat'."
                )
            chat = configured_chat
        text = self._optional_string(arguments, "text") or self._options.notify_message
        selected_chat = self._get_api().messages.send_text(chat, text)
        return {
            "chat": self._serialize(selected_chat),
            "sent": {
                "message_type": "text",
                "text": text,
                "notification": "completion",
            },
        }

    def _messages_send_image(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chat = self._chat_value(arguments, "chat")
        image_path = self._required_string(arguments, "image_path")
        normalized_path = str(Path(image_path).expanduser())
        selected_chat = self._get_api().messages.send_image(chat, normalized_path)
        return {
            "chat": self._serialize(selected_chat),
            "sent": {
                "message_type": "image",
                "image_path": normalized_path,
            },
        }

    def _messages_history(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chat = self._chat_value(arguments, "chat")
        limit = self._optional_int(arguments, "limit", minimum=1, default=50)
        before = self._optional_string(arguments, "before")
        max_scroll_steps = self._optional_int(
            arguments,
            "max_scroll_steps",
            minimum=1,
            default=10,
        )
        page = self._get_api().messages.history(
            chat,
            limit=limit,
            before=before,
            max_scroll_steps=max_scroll_steps,
        )
        return {
            "count": len(page.messages),
            "page": self._serialize(page),
        }

    def _inbox_poll(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chats = self._optional_chat_list(arguments, "chats")
        limit_per_chat = self._optional_int(
            arguments,
            "limit_per_chat",
            minimum=1,
            default=50,
        )
        result = self._get_api().inbox.poll(chats=chats, limit_per_chat=limit_per_chat)
        return {
            "count": len(result.messages),
            "result": self._serialize(result),
        }

    def _session_close(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_no_arguments(arguments)
        had_open_session = self._api is not None
        self.close()
        return {"closed": had_open_session}

    def _validate_security_configuration(self) -> None:
        known_tools = set(self._tools)
        referenced_tools = set(self._options.allowed_tools or ()) | set(
            self._options.require_approval_for or ()
        )
        unknown_tools = sorted(referenced_tools - known_tools)
        if unknown_tools:
            raise ValueError(
                "Ferramentas desconhecidas na configuracao de seguranca: "
                + ", ".join(unknown_tools)
                + "."
            )

        if (
            self._options.notify_chat is not None
            and self._write_chat_allowlist is not None
            and not self._is_chat_name_allowed(self._options.notify_chat, self._write_chat_allowlist)
        ):
            raise ValueError(
                "O chat configurado em --notify-chat precisa estar na write chat allowlist."
            )

    def _authorize_tool(self, name: str, arguments: dict[str, Any]) -> None:
        if not self._is_tool_allowed(name):
            raise ToolAccessError(f"A ferramenta '{name}' nao esta na whitelist do servidor.")

        self._enforce_chat_selection_policy(name, arguments)
        self._enforce_inbox_poll_policy(name, arguments)
        self._enforce_chat_allowlists(name, arguments)
        self._enforce_image_path_policy(name, arguments)
        self._enforce_explicit_approval(name, arguments)

    def _sanitize_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in arguments.items()
            if key != "approval"
        }

    def _is_tool_allowed(self, name: str) -> bool:
        return self._allowed_tools is None or name in self._allowed_tools

    def _requires_approval(self, name: str) -> bool:
        return self._require_approval_for is not None and name in self._require_approval_for

    def _schema_for_tool(self, tool_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not self._requires_approval(tool_name):
            return schema

        configured = deepcopy(schema)
        properties = dict(configured.get("properties") or {})
        properties["approval"] = deepcopy(APPROVAL_SCHEMA)
        configured["properties"] = properties

        required = list(configured.get("required") or [])
        if "approval" not in required:
            required.append("approval")
        configured["required"] = required
        return configured

    def _enforce_explicit_approval(self, name: str, arguments: dict[str, Any]) -> None:
        if not self._requires_approval(name):
            return

        approval = arguments.get("approval")
        if not isinstance(approval, dict):
            raise ToolAccessError(
                f"A ferramenta '{name}' exige approval.confirm=true antes da execucao."
            )

        unexpected = sorted(set(approval) - {"confirm", "reason"})
        if unexpected:
            raise ToolInputError(
                "Campos inesperados em approval: " + ", ".join(unexpected) + "."
            )

        if approval.get("confirm") is not True:
            raise ToolAccessError(
                f"A ferramenta '{name}' exige approval.confirm=true antes da execucao."
            )

        reason = approval.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ToolInputError("O campo 'approval.reason' precisa ser uma string nao vazia ou null.")

    def _enforce_chat_selection_policy(self, name: str, arguments: dict[str, Any]) -> None:
        if name != "chats_get" or not self._options.deny_fuzzy_chat_match:
            return

        if self._optional_bool(arguments, "exact_match", default=True) is False:
            raise ToolAccessError(
                "A politica do servidor bloqueia chats_get com exact_match=false."
            )

    def _enforce_inbox_poll_policy(self, name: str, arguments: dict[str, Any]) -> None:
        if name != "inbox_poll":
            return

        if arguments.get("chats") is None:
            if self._options.deny_unfiltered_inbox_poll:
                raise ToolAccessError(
                    "A politica do servidor bloqueia inbox_poll sem a lista explicita de chats."
                )
            if self._options.read_chat_allowlist:
                arguments["chats"] = list(self._options.read_chat_allowlist)

    def _enforce_chat_allowlists(self, name: str, arguments: dict[str, Any]) -> None:
        if name in WRITE_CHAT_TOOLS:
            self._ensure_tool_chat_targets_allowed(
                name=name,
                targets=self._chat_targets_for_write_tool(name, arguments),
                allowed_names=self._write_chat_allowlist,
                label="write",
            )
            return

        if name in READ_CHAT_TOOLS:
            self._ensure_tool_chat_targets_allowed(
                name=name,
                targets=self._chat_targets_for_read_tool(name, arguments),
                allowed_names=self._read_chat_allowlist,
                label="read",
            )
            return

        if name in CHAT_SELECTION_TOOLS:
            target = self._chat_value(arguments, "target")
            allowed_names = self._visible_chat_allowlist
            self._ensure_tool_chat_targets_allowed(
                name=name,
                targets=[target],
                allowed_names=allowed_names,
                label="visible",
            )

    def _enforce_image_path_policy(self, name: str, arguments: dict[str, Any]) -> None:
        if name != "messages_send_image" or self._allowed_image_dirs is None:
            return

        image_path = self._required_string(arguments, "image_path")
        resolved_path = Path(image_path).expanduser().resolve(strict=False)
        if any(resolved_path.is_relative_to(allowed_dir) for allowed_dir in self._allowed_image_dirs):
            return

        allowed_dirs = ", ".join(str(path) for path in self._allowed_image_dirs)
        raise ToolAccessError(
            "O caminho da imagem nao esta na allowlist de diretorios permitidos: "
            f"{allowed_dirs}."
        )

    def _chat_targets_for_write_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> list[str | ChatRef]:
        if name == "notify_completion":
            chat = self._optional_chat_value(arguments, "chat")
            if chat is not None:
                return [chat]
            if self._options.notify_chat is not None:
                return [self._options.notify_chat]
            return []
        return [self._chat_value(arguments, "chat")]

    def _chat_targets_for_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> list[str | ChatRef]:
        if name == "inbox_poll":
            chats = self._optional_chat_list(arguments, "chats")
            return chats or []
        return [self._chat_value(arguments, "chat")]

    def _ensure_tool_chat_targets_allowed(
        self,
        *,
        name: str,
        targets: list[str | ChatRef],
        allowed_names: set[str] | None,
        label: str,
    ) -> None:
        if allowed_names is None:
            return
        for target in targets:
            if self._is_chat_target_allowed(target, allowed_names):
                continue
            chat_name = self._chat_name(target)
            raise ToolAccessError(
                f"A ferramenta '{name}' nao pode acessar o chat '{chat_name}' fora da {label} chat allowlist."
            )

    def _ensure_visible_chat_allowed(self, chat: ChatRef, *, tool_name: str) -> None:
        if self._visible_chat_allowlist is None:
            return
        if self._is_chat_target_allowed(chat, self._visible_chat_allowlist):
            return
        raise ToolAccessError(
            f"A ferramenta '{tool_name}' nao pode expor o chat atual fora da visible chat allowlist."
        )

    def _filter_visible_chats(self, chats: list[Any]) -> list[Any]:
        if self._visible_chat_allowlist is None:
            return chats
        return [
            chat
            for chat in chats
            if self._is_chat_target_allowed(chat, self._visible_chat_allowlist)
        ]

    def _build_visible_chat_allowlist(self) -> set[str] | None:
        if self._read_chat_allowlist is None and self._write_chat_allowlist is None:
            return None

        visible = set()
        if self._read_chat_allowlist is not None:
            visible.update(self._read_chat_allowlist)
        if self._write_chat_allowlist is not None:
            visible.update(self._write_chat_allowlist)
        return visible

    @staticmethod
    def _normalized_allowed_tool_names(values: tuple[str, ...] | None) -> set[str] | None:
        normalized = ZapAPIMCPRuntime._normalized_tool_names(values)
        if normalized is None:
            return None
        normalized.update(DEFAULT_ALLOWED_TOOLS)
        return normalized

    @staticmethod
    def _normalized_tool_names(values: tuple[str, ...] | None) -> set[str] | None:
        if not values:
            return None
        return {value.strip() for value in values if value.strip()}

    @staticmethod
    def _normalized_chat_names(values: tuple[str, ...] | None) -> set[str] | None:
        if not values:
            return None
        return {value.casefold() for value in values if value.strip()}

    @staticmethod
    def _normalized_image_dirs(values: tuple[Path, ...] | None) -> tuple[Path, ...] | None:
        if not values:
            return None
        return tuple(Path(value).expanduser().resolve(strict=False) for value in values)

    def _is_chat_target_allowed(self, value: str | ChatRef | Any, allowed_names: set[str]) -> bool:
        return self._chat_name(value).casefold() in allowed_names

    def _is_chat_name_allowed(self, name: str, allowed_names: set[str]) -> bool:
        return name.casefold() in allowed_names

    @staticmethod
    def _chat_name(value: str | ChatRef | Any) -> str:
        if isinstance(value, ChatRef):
            return value.name
        if isinstance(value, str):
            return value
        if hasattr(value, "name") and isinstance(value.name, str):
            return value.name
        raise ToolInputError("Nao foi possivel determinar o nome do chat para aplicar a politica.")

    def _serialize(self, value: Any) -> Any:
        if value is None:
            return None
        if is_dataclass(value):
            return {
                field.name: self._serialize(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): self._serialize(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self._serialize(item) for item in value]
        return value

    def _empty_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def _encode_json(self, value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _expect_no_arguments(self, arguments: dict[str, Any]) -> None:
        if arguments:
            unexpected = ", ".join(sorted(arguments))
            raise ToolInputError(f"Esta ferramenta nao aceita argumentos: {unexpected}.")

    def _chat_value(self, arguments: dict[str, Any], field_name: str) -> str | ChatRef:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            raise ToolInputError(f"O campo '{field_name}' e obrigatorio.")
        if isinstance(raw_value, str):
            if raw_value.strip():
                return raw_value
            raise ToolInputError(f"O campo '{field_name}' nao pode ser vazio.")
        if isinstance(raw_value, dict):
            name = raw_value.get("name")
            key = raw_value.get("key")
            if not isinstance(name, str) or not name.strip():
                raise ToolInputError(
                    f"O objeto '{field_name}' precisa conter um campo 'name' nao vazio."
                )
            if key is not None and not isinstance(key, str):
                raise ToolInputError(
                    f"O campo '{field_name}.key' precisa ser string ou null."
                )
            return ChatRef(name=name, key=key)
        raise ToolInputError(
            f"O campo '{field_name}' precisa ser uma string ou um objeto de chat."
        )

    def _optional_chat_value(
        self,
        arguments: dict[str, Any],
        field_name: str,
    ) -> str | ChatRef | None:
        if field_name not in arguments or arguments.get(field_name) is None:
            return None
        return self._chat_value(arguments, field_name)

    def _optional_chat_list(
        self,
        arguments: dict[str, Any],
        field_name: str,
    ) -> list[str | ChatRef] | None:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return None
        if not isinstance(raw_value, list):
            raise ToolInputError(f"O campo '{field_name}' precisa ser uma lista de chats.")
        return [self._chat_value({field_name: item}, field_name) for item in raw_value]

    def _required_string(self, arguments: dict[str, Any], field_name: str) -> str:
        raw_value = arguments.get(field_name)
        if not isinstance(raw_value, str):
            raise ToolInputError(f"O campo '{field_name}' precisa ser uma string.")
        if not raw_value.strip():
            raise ToolInputError(f"O campo '{field_name}' nao pode ser vazio.")
        return raw_value

    def _optional_string(self, arguments: dict[str, Any], field_name: str) -> str | None:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            raise ToolInputError(f"O campo '{field_name}' precisa ser uma string ou null.")
        return raw_value

    def _optional_bool(
        self,
        arguments: dict[str, Any],
        field_name: str,
        *,
        default: bool,
    ) -> bool:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return default
        if not isinstance(raw_value, bool):
            raise ToolInputError(f"O campo '{field_name}' precisa ser booleano.")
        return raw_value

    def _optional_int(
        self,
        arguments: dict[str, Any],
        field_name: str,
        *,
        minimum: int | None = None,
        default: int | None = None,
    ) -> int | None:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return default
        if not isinstance(raw_value, int) or isinstance(raw_value, bool):
            raise ToolInputError(f"O campo '{field_name}' precisa ser um inteiro.")
        if minimum is not None and raw_value < minimum:
            raise ToolInputError(
                f"O campo '{field_name}' precisa ser maior ou igual a {minimum}."
            )
        return raw_value


class ZapAPIMCPServer:
    def __init__(self, runtime: ZapAPIMCPRuntime) -> None:
        self._runtime = runtime
        self._initialized = False
        self._client_initialized = False
        self._protocol_version = LATEST_PROTOCOL_VERSION
        self._server_version = _package_version()

    def handle_message(self, message: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        if isinstance(message, list):
            if not message:
                return self._error_response(
                    request_id=None,
                    code=INVALID_REQUEST,
                    message="JSON-RPC batch vazio.",
                )
            responses: list[dict[str, Any]] = []
            for item in message:
                response = self._handle_single_message(item)
                if response is None:
                    continue
                if isinstance(response, list):
                    responses.extend(response)
                else:
                    responses.append(response)
            return responses or None
        return self._handle_single_message(message)

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout

        try:
            for line in stdin:
                raw_line = line.strip()
                if not raw_line:
                    continue

                try:
                    message = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    response = self._error_response(
                        request_id=None,
                        code=PARSE_ERROR,
                        message=f"JSON invalido: {exc.msg}",
                    )
                    self._write_message(stdout, response)
                    continue

                response = self.handle_message(message)
                if response is None:
                    continue
                self._write_message(stdout, response)
        finally:
            self._runtime.close()

    def _handle_single_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return self._error_response(
                request_id=None,
                code=INVALID_REQUEST,
                message="A mensagem JSON-RPC precisa ser um objeto.",
            )
        if message.get("jsonrpc") != JSONRPC_VERSION:
            return self._error_response(
                request_id=message.get("id"),
                code=INVALID_REQUEST,
                message="A mensagem precisa usar jsonrpc=2.0.",
            )

        method = message.get("method")
        if not isinstance(method, str):
            return self._error_response(
                request_id=message.get("id"),
                code=INVALID_REQUEST,
                message="O campo method e obrigatorio e precisa ser string.",
            )

        is_request = "id" in message
        request_id = message.get("id")
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._error_response(
                request_id=request_id,
                code=INVALID_PARAMS,
                message="O campo params precisa ser um objeto JSON.",
            )

        try:
            result = self._dispatch(method, params)
        except MCPProtocolError as exc:
            if not is_request:
                return None
            return self._error_response(
                request_id=request_id,
                code=exc.code,
                message=exc.message,
                data=exc.data,
            )
        except Exception as exc:  # pragma: no cover - defensive path
            LOGGER.exception("Erro inesperado ao processar %s", method)
            if not is_request:
                return None
            return self._error_response(
                request_id=request_id,
                code=INTERNAL_ERROR,
                message=f"{type(exc).__name__}: {exc}",
            )

        if not is_request:
            return None
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "result": result,
        }

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "notifications/initialized":
            self._client_initialized = True
            return {}
        if method == "notifications/cancelled":
            return {}
        if method == "ping":
            return {}
        if not self._initialized:
            raise MCPProtocolError(
                INVALID_REQUEST,
                "O cliente precisa chamar initialize antes de usar o servidor.",
            )
        if method == "tools/list":
            return {"tools": self._runtime.tool_descriptors}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name.strip():
                raise MCPProtocolError(
                    INVALID_PARAMS,
                    "O campo params.name e obrigatorio em tools/call.",
                )
            arguments = params.get("arguments")
            return self._runtime.call_tool(name=name, arguments=arguments)
        if method == "resources/list":
            return {"resources": []}
        if method == "prompts/list":
            return {"prompts": []}
        raise MCPProtocolError(METHOD_NOT_FOUND, f"Metodo desconhecido: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested_version = params.get("protocolVersion")
        if not isinstance(requested_version, str):
            raise MCPProtocolError(
                INVALID_PARAMS,
                "initialize exige params.protocolVersion.",
            )

        negotiated_version = self._negotiate_protocol_version(requested_version)
        self._initialized = True
        self._protocol_version = negotiated_version

        return {
            "protocolVersion": negotiated_version,
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": "whatsapp",
                "title": "WhatsApp MCP Server",
                "version": self._server_version,
                "description": (
                    "Servidor MCP stdio para operar a ZapAPI sobre WhatsApp Web + Playwright."
                ),
            },
            "instructions": (
                "Use auth_status ou auth_ensure_ready antes de enviar mensagens. "
                "Se a sessao ainda nao existir e o servidor estiver em headless, "
                "uma autenticacao visivel temporaria sera aberta automaticamente. "
                "Reutilize o mesmo user_data_dir nas proximas execucoes."
            ),
        }

    def _negotiate_protocol_version(self, requested_version: str) -> str:
        if requested_version in SUPPORTED_PROTOCOL_VERSIONS:
            return requested_version
        return LATEST_PROTOCOL_VERSION

    def _write_message(
        self,
        stdout: TextIO,
        message: dict[str, Any] | list[dict[str, Any]],
    ) -> None:
        stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
        stdout.write("\n")
        stdout.flush()

    def _error_response(
        self,
        *,
        request_id: Any,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
        if data is not None:
            payload["error"]["data"] = data
        return payload


def build_server(
    options: ServerOptions,
    api_factory: Callable[..., ZapAPI] = ZapAPI,
) -> ZapAPIMCPServer:
    return ZapAPIMCPServer(ZapAPIMCPRuntime(options=options, api_factory=api_factory))


def parse_args(argv: list[str] | None = None) -> ServerOptions:
    parser = argparse.ArgumentParser(
        description="Servidor MCP stdio para a ZapAPI.",
    )
    parser.add_argument(
        "--user-data-dir",
        default=str(DEFAULT_PROFILE_PATH),
        help=(
            "Diretorio persistente do perfil do WhatsApp Web. "
            f"Padrao: {DEFAULT_PROFILE_PATH}"
        ),
    )
    parser.add_argument(
        "--headless",
        type=_parse_bool,
        default=True,
        help="Executa o Chromium em modo headless (true/false).",
    )
    parser.add_argument(
        "--debug-level",
        default="INFO",
        help="Nivel de log Python: DEBUG, INFO, WARNING, ERROR ou CRITICAL.",
    )
    parser.add_argument(
        "--base-url",
        default="https://web.whatsapp.com/",
        help="URL base do WhatsApp Web.",
    )
    parser.add_argument(
        "--launch-timeout-ms",
        type=int,
        default=30000,
        help="Timeout de abertura do browser em milissegundos.",
    )
    parser.add_argument(
        "--action-timeout-ms",
        type=int,
        default=5000,
        help="Timeout de interacoes do Playwright em milissegundos.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=0.5,
        help="Intervalo padrao de polling da inbox em segundos.",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        help="Atraso artificial do Playwright em milissegundos.",
    )
    parser.add_argument(
        "--notify-chat",
        default=None,
        help=(
            "Chat padrao usado pela ferramenta notify_completion. "
            "Se omitido, o chat deve ser informado na chamada da ferramenta."
        ),
    )
    parser.add_argument(
        "--notify-message",
        default="Refatoracao concluida.",
        help="Mensagem padrao usada pela ferramenta notify_completion.",
    )
    parser.add_argument(
        "--allow-tool",
        action="append",
        default=None,
        help=(
            "Ferramenta permitida na whitelist. Repita a flag para liberar varias. "
            "auth_status e auth_ensure_ready sao incluidos automaticamente."
        ),
    )
    parser.add_argument(
        "--read-chat-allowlist",
        action="append",
        default=None,
        help="Chat permitido para leitura. Repita a flag ou use valores separados por virgula.",
    )
    parser.add_argument(
        "--write-chat-allowlist",
        action="append",
        default=None,
        help="Chat permitido para escrita. Repita a flag ou use valores separados por virgula.",
    )
    parser.add_argument(
        "--allow-image-dir",
        action="append",
        default=None,
        help="Diretorio permitido para envio de imagens. Repita a flag se necessario.",
    )
    parser.add_argument(
        "--require-approval-for",
        action="append",
        default=None,
        help="Ferramenta que exige approval.confirm=true. Repita a flag para varias.",
    )
    parser.add_argument(
        "--deny-unfiltered-inbox-poll",
        type=_parse_bool,
        default=False,
        help="Bloqueia inbox_poll sem a lista explicita de chats.",
    )
    parser.add_argument(
        "--deny-fuzzy-chat-match",
        type=_parse_bool,
        default=False,
        help="Bloqueia chats_get com exact_match=false.",
    )
    args = parser.parse_args(argv)

    return ServerOptions(
        user_data_dir=Path(args.user_data_dir),
        headless=args.headless,
        debug_level=_parse_log_level(args.debug_level),
        base_url=args.base_url,
        launch_timeout_ms=args.launch_timeout_ms,
        action_timeout_ms=args.action_timeout_ms,
        poll_interval_seconds=args.poll_interval_seconds,
        slow_mo=args.slow_mo,
        notify_chat=args.notify_chat,
        notify_message=args.notify_message,
        allowed_tools=_split_cli_values(args.allow_tool),
        read_chat_allowlist=_split_cli_values(args.read_chat_allowlist),
        write_chat_allowlist=_split_cli_values(args.write_chat_allowlist),
        allowed_image_dirs=_split_path_values(args.allow_image_dir),
        require_approval_for=_split_cli_values(args.require_approval_for),
        deny_unfiltered_inbox_poll=args.deny_unfiltered_inbox_poll,
        deny_fuzzy_chat_match=args.deny_fuzzy_chat_match,
    )


def main(argv: list[str] | None = None) -> None:
    options = parse_args(argv)
    logging.basicConfig(
        level=options.debug_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = build_server(options)
    server.serve()


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("Use true ou false.")


def _parse_log_level(raw_value: str) -> int:
    normalized = raw_value.strip().upper()
    if normalized.isdigit():
        return int(normalized)
    if normalized not in logging._nameToLevel:
        raise argparse.ArgumentTypeError(
            "Use DEBUG, INFO, WARNING, ERROR, CRITICAL ou um numero inteiro."
        )
    return logging._nameToLevel[normalized]


def _split_cli_values(raw_values: list[str] | None) -> tuple[str, ...] | None:
    if not raw_values:
        return None

    values: list[str] = []
    for raw_value in raw_values:
        for chunk in raw_value.split(","):
            normalized = chunk.strip()
            if normalized:
                values.append(normalized)

    return tuple(values) or None


def _split_path_values(raw_values: list[str] | None) -> tuple[Path, ...] | None:
    values = _split_cli_values(raw_values)
    if values is None:
        return None
    return tuple(Path(value) for value in values)


def _package_version() -> str:
    try:
        return version("zapapi")
    except PackageNotFoundError:
        return "0.1.0"
