from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from zapapi import ChatRef, ZapAPI

from .errors import ToolInputError
from .options import ServerOptions
from .parsing import ArgumentParser, serialize
from .security import SecurityPolicy

LOGGER = logging.getLogger(__name__)


class ToolHandlers:
    """Implements the business logic for each MCP tool.

    Five tools designed for agent consumption:
    - whatsapp_status  — diagnostics, auth, session management
    - whatsapp_inbox   — unified view of chats + recent messages
    - whatsapp_read    — read history of a specific chat
    - whatsapp_send    — send text or image to a chat
    - whatsapp_search  — search messages by text across chats
    """

    def __init__(
        self,
        options: ServerOptions,
        security: SecurityPolicy,
        api_getter: callable,
    ) -> None:
        self._options = options
        self._security = security
        self._get_api = api_getter

    # -- whatsapp_status -----------------------------------------------------

    def whatsapp_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = ArgumentParser.optional_string(arguments, "action")

        if action == "close":
            # Handled by the runtime wrapper — we just signal intent.
            return {"action": "close", "closed": True}

        api = self._get_api()
        status = api.auth.status()
        result: dict[str, Any] = {
            "auth": serialize(status),
        }

        # Include accessible chats info for diagnostics.
        read_list = self._security.read_chat_allowlist
        write_list = self._security.write_chat_allowlist
        result["policies"] = {
            "read_chat_allowlist": sorted(read_list) if read_list else None,
            "write_chat_allowlist": sorted(write_list) if write_list else None,
            "unrestricted": read_list is None and write_list is None,
        }
        return result

    # -- whatsapp_inbox ------------------------------------------------------

    def whatsapp_inbox(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chats = ArgumentParser.optional_chat_list(arguments, "chats")
        limit_per_chat = ArgumentParser.optional_int(
            arguments, "limit_per_chat", minimum=1, default=10,
        )
        scroll_steps = ArgumentParser.optional_int(
            arguments, "scroll_steps", minimum=0, default=3,
        )
        max_chats = ArgumentParser.optional_int(
            arguments, "max_chats", minimum=1, default=15,
        )

        LOGGER.debug(
            "whatsapp_inbox: chats=%s limit_per_chat=%d scroll_steps=%d max_chats=%s",
            chats, limit_per_chat, scroll_steps, max_chats if chats is None else "N/A",
        )

        api = self._get_api()
        entries = api.inbox.inbox(
            chats=chats,
            limit_per_chat=limit_per_chat,
            scroll_steps=scroll_steps,
            max_chats=max_chats if chats is None else None,
        )

        LOGGER.debug("whatsapp_inbox: backend retornou %d entries", len(entries))

        # Filter by allowlist and enrich with access info.
        result_entries: list[dict[str, Any]] = []
        filtered_out = 0
        for entry in entries:
            serialized = serialize(entry)
            chat_name = entry.chat.name
            readable = self._security.is_chat_accessible(chat_name, self._security.read_chat_allowlist)
            writable = self._security.is_chat_accessible(chat_name, self._security.write_chat_allowlist)

            if self._security.visible_chat_allowlist is not None:
                if not self._security.is_chat_accessible(chat_name, self._security.visible_chat_allowlist):
                    filtered_out += 1
                    continue

            serialized["readable"] = readable
            serialized["writable"] = writable
            result_entries.append(serialized)

        result: dict[str, Any] = {
            "count": len(result_entries),
            "entries": result_entries,
        }
        if filtered_out > 0:
            result["filtered_out"] = filtered_out
            result["hint"] = (
                f"{filtered_out} chat(s) foram omitidos pela allowlist do servidor. "
                "Use --read-chat-allowlist ou --write-chat-allowlist para liberar mais chats."
            )
        return result

    # -- whatsapp_read -------------------------------------------------------

    def whatsapp_read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chat = ArgumentParser.chat_value(arguments, "chat")
        limit = ArgumentParser.optional_int(arguments, "limit", minimum=1, default=50)
        before = ArgumentParser.optional_string(arguments, "before")
        max_scroll_steps = ArgumentParser.optional_int(
            arguments, "max_scroll_steps", minimum=1, default=10,
        )

        LOGGER.debug("whatsapp_read: chat=%s limit=%d before=%s", chat, limit, before)

        api = self._get_api()
        page = api.messages.history(
            chat,
            limit=limit,
            before=before,
            max_scroll_steps=max_scroll_steps,
        )
        LOGGER.debug("whatsapp_read: retornou %d mensagens, has_more=%s", len(page.messages), page.has_more)
        return {
            "count": len(page.messages),
            "chat": serialize(page.chat),
            "messages": serialize(page.messages),
            "cursor": page.cursor,
            "has_more": page.has_more,
        }

    # -- whatsapp_send -------------------------------------------------------

    def whatsapp_send(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chat = ArgumentParser.chat_value(arguments, "chat")
        text = ArgumentParser.optional_string(arguments, "text")
        image_path = ArgumentParser.optional_string(arguments, "image_path")

        if text is None and image_path is None:
            raise ToolInputError(
                "Informe 'text' para enviar uma mensagem de texto ou 'image_path' para enviar uma imagem."
            )

        LOGGER.debug("whatsapp_send: chat=%s text=%s image=%s", chat, text is not None, image_path)

        api = self._get_api()

        if image_path is not None:
            normalized_path = str(Path(image_path).expanduser())
            selected_chat = api.messages.send_image(chat, normalized_path)
            return {
                "chat": serialize(selected_chat),
                "sent": {"type": "image", "image_path": normalized_path},
            }

        selected_chat = api.messages.send_text(chat, text)
        return {
            "chat": serialize(selected_chat),
            "sent": {"type": "text", "text": text},
        }

    # -- whatsapp_search -----------------------------------------------------

    def whatsapp_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = ArgumentParser.required_string(arguments, "query")
        chats = ArgumentParser.optional_chat_list(arguments, "chats")
        limit = ArgumentParser.optional_int(arguments, "limit", minimum=1, default=20)
        scroll_steps = ArgumentParser.optional_int(
            arguments, "scroll_steps", minimum=0, default=3,
        )
        max_chats = ArgumentParser.optional_int(
            arguments, "max_chats", minimum=1, default=15,
        )

        LOGGER.debug(
            "whatsapp_search: query='%s' chats=%s limit=%d max_chats=%s",
            query, chats, limit, max_chats if chats is None else "N/A",
        )

        api = self._get_api()
        hits = api.inbox.search(
            query,
            chats=chats,
            limit=limit,
            scroll_steps=scroll_steps,
            max_chats=max_chats if chats is None else None,
        )

        LOGGER.debug("whatsapp_search: retornou %d hit(s)", len(hits))
        return {
            "query": query,
            "count": len(hits),
            "hits": serialize(hits),
        }
