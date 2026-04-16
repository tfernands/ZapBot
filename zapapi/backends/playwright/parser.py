from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from typing import Sequence

from ...models import ChatRef, ChatSummary, ChatTextMessage, MessageDirection


class WhatsAppParser:

    @staticmethod
    def parse_chat_summary(payload: dict | None) -> ChatSummary | None:
        if payload is None:
            return None

        name = (payload.get("name") or "").strip()
        if not name:
            return None

        return ChatSummary(
            name=name,
            key=None,
            preview=payload.get("preview"),
            timestamp=payload.get("timestamp"),
            unread_count=int(payload.get("unread_count") or 0),
        )

    @classmethod
    def parse_message(cls, chat: ChatRef, payload: dict | None) -> ChatTextMessage | None:
        if payload is None:
            return None

        message_text = (payload.get("message_text") or "").strip()
        metadata = payload.get("pre_plain_text")
        if not message_text:
            return None

        parsed_datetime = cls._parse_message_datetime(metadata)
        sender = cls._parse_message_sender(metadata)
        outgoing = bool(payload.get("outgoing"))

        if not sender:
            sender = chat.name if outgoing else "unknown"

        key = payload.get("message_id") or f"{metadata or ''}|{message_text}"
        return ChatTextMessage(
            id=key,
            chat=chat,
            text=message_text,
            sender=sender,
            direction=MessageDirection.OUTBOUND if outgoing else MessageDirection.INBOUND,
            sent_at=parsed_datetime,
            metadata=metadata,
        )

    @staticmethod
    def mark_new_messages(
        chat: ChatRef,
        messages: Sequence[ChatTextMessage],
        last_message_id_by_chat: dict[str, str],
    ) -> list[ChatTextMessage]:
        if not messages:
            return []

        last_id = last_message_id_by_chat.get(chat.name)
        last_message_id_by_chat[chat.name] = messages[-1].id

        if last_id is None:
            return []

        start_index = next((index for index, message in enumerate(messages) if message.id == last_id), None)
        if start_index is None:
            return [replace(message, is_new=True) for message in messages]

        return [replace(message, is_new=True) for message in messages[start_index + 1 :]]

    @staticmethod
    def dedupe_messages(messages: Sequence[ChatTextMessage]) -> list[ChatTextMessage]:
        deduped: list[ChatTextMessage] = []
        seen: set[str] = set()
        for message in messages:
            if message.id in seen:
                continue
            deduped.append(message)
            seen.add(message.id)
        return deduped

    @staticmethod
    def _parse_message_sender(metadata: str | None) -> str:
        if not metadata:
            return ""
        match = re.search(r"(?<=\] ).+?:", metadata)
        return match.group(0)[:-1] if match else ""

    @staticmethod
    def _parse_message_datetime(metadata: str | None) -> datetime | None:
        if not metadata:
            return None

        match = re.search(r"\[(.*?)\]", metadata)
        if match is None:
            return None

        raw_value = match.group(1)
        for fmt in ("%H:%M, %d/%m/%Y", "%H:%M, %m/%d/%Y", "%H:%M, %d/%m/%y", "%H:%M, %m/%d/%y"):
            try:
                return datetime.strptime(raw_value, fmt)
            except ValueError:
                continue
        return None
