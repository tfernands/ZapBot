"""ZapAPI MCP Server — facade that composes the modular components.

Exposes five agent-friendly tools:

* ``whatsapp_status``  — diagnostics, auth state, session close
* ``whatsapp_inbox``   — unified view of chats + recent messages (1 call)
* ``whatsapp_read``    — read full history of a specific chat
* ``whatsapp_send``    — send text or image
* ``whatsapp_search``  — search messages by text across chats
"""

from __future__ import annotations

import json
import logging
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable, TextIO

from zapapi import ChatRef, ZapAPI
from zapapi.errors import (
    AuthenticationRequiredException,
    ChatNotFoundException,
    NoOpenChatException,
    WhatsAppWebTimeoutException,
)

from .errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    JSONRPC_VERSION,
    LATEST_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    MCPProtocolError,
    ToolAccessError,
    ToolInputError,
)
from .handlers import ToolHandlers
from .options import ServerOptions, ToolDefinition
from .parsing import encode_json
from .schemas import CHAT_REF_SCHEMA
from .security import SecurityPolicy


LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Runtime — orchestrates tools, security, and the ZapAPI lifecycle
# ---------------------------------------------------------------------------


class ZapAPIMCPRuntime:
    def __init__(
        self,
        options: ServerOptions,
        api_factory: Callable[..., ZapAPI] = ZapAPI,
    ) -> None:
        self._options = options
        self._api_factory = api_factory
        self._api: ZapAPI | None = None

        self._security = SecurityPolicy(options)
        self._handlers = ToolHandlers(
            options=options,
            security=self._security,
            api_getter=self._get_api,
        )

        tools = self._build_tools()
        self._tools = {tool.name: tool for tool in tools}
        self._security.validate_configuration(set(self._tools))

    # -- Public interface ----------------------------------------------------

    @property
    def tool_descriptors(self) -> list[dict[str, Any]]:
        return [
            tool.descriptor()
            for tool in self._tools.values()
            if self._security.is_tool_allowed(tool.name)
        ]

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        import time as _time

        tool = self._tools.get(name)
        if tool is None:
            raise MCPProtocolError(METHOD_NOT_FOUND, f"Ferramenta desconhecida: {name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise MCPProtocolError(INVALID_PARAMS, "O campo arguments deve ser um objeto JSON.")

        arg_summary = {k: v for k, v in arguments.items() if k != "approval"}
        LOGGER.info("tools/call %s — args=%s", name, arg_summary)
        t0 = _time.monotonic()

        try:
            self._security.authorize_tool(name, arguments)
            sanitized = self._security.sanitize_arguments(arguments)
        except (ToolInputError, ToolAccessError) as exc:
            LOGGER.warning("tools/call %s — autorização negada: %s (%.1fs)", name, exc, _time.monotonic() - t0)
            return self._tool_error(str(exc))

        try:
            result = tool.handler(sanitized)
        except (ToolInputError, ToolAccessError) as exc:
            LOGGER.warning("tools/call %s — erro de input: %s (%.1fs)", name, exc, _time.monotonic() - t0)
            return self._tool_error(str(exc))
        except (
            AuthenticationRequiredException,
            ChatNotFoundException,
            FileNotFoundError,
            NoOpenChatException,
            ValueError,
            WhatsAppWebTimeoutException,
        ) as exc:
            LOGGER.warning("tools/call %s — %s: %s (%.1fs)", name, type(exc).__name__, exc, _time.monotonic() - t0)
            return self._tool_error(str(exc))
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("tools/call %s — erro inesperado (%.1fs)", name, _time.monotonic() - t0)
            return self._tool_error(f"{type(exc).__name__}: {exc}")

        elapsed = _time.monotonic() - t0

        # Handle session_close action from whatsapp_status.
        if name == "whatsapp_status" and result.get("action") == "close":
            had_session = self._api is not None
            self.close()
            result["closed"] = had_session

        LOGGER.info("tools/call %s — ok em %.1fs", name, elapsed)
        return self._tool_success(result)

    def close(self) -> None:
        if self._api is None:
            return
        self._api.close()
        self._api = None

    # -- API lifecycle -------------------------------------------------------

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
            "Sessao nao autenticada em headless; abrindo Chromium visivel para autenticar."
        )
        self._api.close()

        bootstrap_api = self._start_api(headless=False)
        try:
            bootstrap_api.auth.ensure_ready(timeout_ms=0)
        finally:
            bootstrap_api.close()

        self._api = self._start_api(headless=self._options.headless)
        self._api.auth.ensure_ready(timeout_ms=self._options.launch_timeout_ms)

    # -- Tool definition registry --------------------------------------------

    def _build_tools(self) -> list[ToolDefinition]:
        h = self._handlers
        s = self._security
        return [
            ToolDefinition(
                name="whatsapp_status",
                title="WhatsApp Status",
                description=(
                    "Retorna o status da sessao do WhatsApp (autenticacao, policies de acesso). "
                    "Use action='close' para encerrar a sessao do navegador. "
                    "A autenticacao e transparente — qualquer outra ferramenta autentica automaticamente ao ser chamada."
                ),
                input_schema=s.schema_for_tool(
                    "whatsapp_status",
                    {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["close"],
                                "description": "Acao opcional. Use 'close' para encerrar a sessao.",
                            },
                        },
                        "additionalProperties": False,
                    },
                ),
                handler=h.whatsapp_status,
                annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
            ),
            ToolDefinition(
                name="whatsapp_inbox",
                title="WhatsApp Inbox",
                description=(
                    "Visao unificada da inbox: lista chats com suas mensagens recentes em uma unica chamada. "
                    "Substitui o fluxo de listar chats + abrir cada um + ler historico. "
                    "Cada chat na resposta inclui 'readable' e 'writable' indicando permissoes. "
                    "Chats que nao puderam ser abertos aparecem com skipped=true e o motivo. "
                    "Use sem parametros para ver tudo, ou passe 'chats' para filtrar."
                ),
                input_schema=s.schema_for_tool(
                    "whatsapp_inbox",
                    {
                        "type": "object",
                        "properties": {
                            "chats": {
                                "type": "array",
                                "items": CHAT_REF_SCHEMA,
                                "description": "Lista opcional de chats para consultar. Se omitido, retorna todos os visiveis.",
                            },
                            "limit_per_chat": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 10,
                                "description": "Quantidade de mensagens recentes por chat. Padrao: 10.",
                            },
                            "scroll_steps": {
                                "type": "integer",
                                "minimum": 0,
                                "default": 3,
                                "description": (
                                    "Scrolls na sidebar para carregar mais chats. "
                                    "O WhatsApp virtualiza a lista. Padrao: 3."
                                ),
                            },
                            "max_chats": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 15,
                                "description": (
                                    "Maximo de chats a processar quando 'chats' nao e informado. "
                                    "Evita timeout em inboxes grandes. Padrao: 15. "
                                    "Ignorado quando 'chats' e fornecido."
                                ),
                            },
                        },
                        "additionalProperties": False,
                    },
                ),
                handler=h.whatsapp_inbox,
                annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
            ),
            ToolDefinition(
                name="whatsapp_read",
                title="WhatsApp Read",
                description=(
                    "Le o historico completo de um chat especifico com paginacao por cursor. "
                    "Use quando precisar de mais mensagens do que o inbox retornou, "
                    "ou para paginar para tras no historico."
                ),
                input_schema=s.schema_for_tool(
                    "whatsapp_read",
                    {
                        "type": "object",
                        "properties": {
                            "chat": CHAT_REF_SCHEMA,
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 50,
                                "description": "Quantidade maxima de mensagens. Padrao: 50.",
                            },
                            "before": {
                                "type": ["string", "null"],
                                "description": "Cursor retornado pela chamada anterior para paginar.",
                            },
                            "max_scroll_steps": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 10,
                                "description": "Scrolls retroativos maximos para carregar historico.",
                            },
                        },
                        "required": ["chat"],
                        "additionalProperties": False,
                    },
                ),
                handler=h.whatsapp_read,
                annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
            ),
            ToolDefinition(
                name="whatsapp_send",
                title="WhatsApp Send",
                description=(
                    "Envia uma mensagem para um chat. "
                    "Informe 'text' para texto ou 'image_path' para imagem (ou ambos para imagem com legenda). "
                    "O chat e aberto automaticamente — nao precisa chamar outra ferramenta antes."
                ),
                input_schema=s.schema_for_tool(
                    "whatsapp_send",
                    {
                        "type": "object",
                        "properties": {
                            "chat": CHAT_REF_SCHEMA,
                            "text": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Texto da mensagem.",
                            },
                            "image_path": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Caminho absoluto do arquivo de imagem.",
                            },
                        },
                        "required": ["chat"],
                        "additionalProperties": False,
                    },
                ),
                handler=h.whatsapp_send,
                annotations={"destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
            ),
            ToolDefinition(
                name="whatsapp_search",
                title="WhatsApp Search",
                description=(
                    "Busca mensagens contendo um texto em um ou mais chats. "
                    "Se 'chats' nao for informado, busca em todos os chats visiveis. "
                    "Retorna os hits com a mensagem completa e o chat de origem."
                ),
                input_schema=s.schema_for_tool(
                    "whatsapp_search",
                    {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Texto a buscar nas mensagens.",
                            },
                            "chats": {
                                "type": "array",
                                "items": CHAT_REF_SCHEMA,
                                "description": "Lista opcional de chats para limitar a busca.",
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 20,
                                "description": "Numero maximo de resultados. Padrao: 20.",
                            },
                            "scroll_steps": {
                                "type": "integer",
                                "minimum": 0,
                                "default": 3,
                                "description": "Scrolls na sidebar para descobrir mais chats.",
                            },
                            "max_chats": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 15,
                                "description": (
                                    "Maximo de chats a varrer quando 'chats' nao e informado. "
                                    "Padrao: 15. Ignorado quando 'chats' e fornecido."
                                ),
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                ),
                handler=h.whatsapp_search,
                annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
            ),
        ]

    # -- Response formatting -------------------------------------------------

    @staticmethod
    def _tool_success(payload: dict[str, Any]) -> dict[str, Any]:
        content = {"ok": True, **payload}
        return {
            "content": [{"type": "text", "text": encode_json(content)}],
            "structuredContent": content,
        }

    @staticmethod
    def _tool_error(message: str) -> dict[str, Any]:
        content = {"ok": False, "error": {"message": message}}
        return {
            "content": [{"type": "text", "text": encode_json(content)}],
            "structuredContent": content,
            "isError": True,
        }


# ---------------------------------------------------------------------------
# MCP JSON-RPC Server — protocol layer
# ---------------------------------------------------------------------------


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
                        request_id=None, code=PARSE_ERROR, message=f"JSON invalido: {exc.msg}",
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
        LOGGER.debug("recv: %s", message)
        if not isinstance(message, dict):
            return self._error_response(
                request_id=None, code=INVALID_REQUEST,
                message="A mensagem JSON-RPC precisa ser um objeto.",
            )
        if message.get("jsonrpc") != JSONRPC_VERSION:
            return self._error_response(
                request_id=message.get("id"), code=INVALID_REQUEST,
                message="A mensagem precisa usar jsonrpc=2.0.",
            )

        method = message.get("method")
        if not isinstance(method, str):
            return self._error_response(
                request_id=message.get("id"), code=INVALID_REQUEST,
                message="O campo method e obrigatorio e precisa ser string.",
            )

        is_request = "id" in message
        request_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return self._error_response(
                request_id=request_id, code=INVALID_PARAMS,
                message="O campo params precisa ser um objeto JSON.",
            )

        try:
            result = self._dispatch(method, params)
        except MCPProtocolError as exc:
            if not is_request:
                return None
            return self._error_response(
                request_id=request_id, code=exc.code, message=exc.message, data=exc.data,
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Erro inesperado ao processar %s", method)
            if not is_request:
                return None
            return self._error_response(
                request_id=request_id, code=INTERNAL_ERROR,
                message=f"{type(exc).__name__}: {exc}",
            )

        if not is_request:
            return None
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        LOGGER.debug("dispatch: method=%s", method)
        if method == "initialize":
            return self._initialize(params)
        if method in ("notifications/initialized", "notifications/cancelled", "ping"):
            if method == "notifications/initialized":
                self._client_initialized = True
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
                    INVALID_PARAMS, "O campo params.name e obrigatorio em tools/call.",
                )
            return self._runtime.call_tool(name=name, arguments=params.get("arguments"))
        if method == "resources/list":
            return {"resources": []}
        if method == "prompts/list":
            return {"prompts": []}
        raise MCPProtocolError(METHOD_NOT_FOUND, f"Metodo desconhecido: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested_version = params.get("protocolVersion")
        if not isinstance(requested_version, str):
            raise MCPProtocolError(INVALID_PARAMS, "initialize exige params.protocolVersion.")

        negotiated = (
            requested_version
            if requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        self._initialized = True
        self._protocol_version = negotiated

        return {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "whatsapp",
                "title": "WhatsApp MCP Server",
                "version": self._server_version,
                "description": "Servidor MCP para WhatsApp Web via Playwright.",
            },
            "instructions": (
                "Use whatsapp_inbox para ver conversas recentes. "
                "Use whatsapp_read para historico completo de um chat. "
                "Use whatsapp_send para enviar mensagens. "
                "Use whatsapp_search para buscar por texto. "
                "A autenticacao e transparente — todas as ferramentas autenticam automaticamente."
            ),
        }

    @staticmethod
    def _write_message(stdout: TextIO, message: dict[str, Any] | list[dict[str, Any]]) -> None:
        stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
        stdout.write("\n")
        stdout.flush()

    @staticmethod
    def _error_response(
        *, request_id: Any, code: int, message: str, data: Any | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        if data is not None:
            payload["error"]["data"] = data
        return payload


# ---------------------------------------------------------------------------
# Helpers & factory
# ---------------------------------------------------------------------------


def _package_version() -> str:
    try:
        return version("zapapi")
    except PackageNotFoundError:
        return "0.1.0"


def build_server(
    options: ServerOptions,
    api_factory: Callable[..., ZapAPI] = ZapAPI,
) -> ZapAPIMCPServer:
    return ZapAPIMCPServer(ZapAPIMCPRuntime(options=options, api_factory=api_factory))


# Backward-compatible re-exports.
from .cli import main, parse_args  # noqa: E402, F401
