from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from datetime import datetime
from typing import Sequence

from ...models import (
    ChatImageMessage,
    ChatMessage,
    ChatRef,
    ChatSummary,
    ChatTextMessage,
    MessageDirection,
    ReplyReference,
)


class WhatsAppParser:
    _TIME_ONLY_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")

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

    @staticmethod
    def chat_match_key(value: str | None) -> str:
        """Normalize chat names for matching user input against WhatsApp labels.

        WhatsApp contact titles often include emoji, variation selectors, or
        accents. Agents usually ask for the human name, so matching should not
        fail because the visible title is "Vitoria 💚" and the target is
        "Vitoria".
        """
        if not value:
            return ""

        normalized = unicodedata.normalize("NFKD", value)
        parts: list[str] = []
        last_was_space = False
        for char in normalized:
            category = unicodedata.category(char)
            if category.startswith("M") or category in {"Cf", "So", "Sk"}:
                continue
            if char.isalnum():
                parts.append(char.casefold())
                last_was_space = False
                continue
            if char.isspace() and not last_was_space:
                parts.append(" ")
                last_was_space = True

        return "".join(parts).strip()

    @classmethod
    def parse_message(cls, chat: ChatRef, payload: dict | None) -> ChatMessage | None:
        if payload is None:
            return None

        message_text = cls._normalize_message_text(payload.get("message_text"))
        metadata = payload.get("pre_plain_text")
        has_image = bool(payload.get("has_image"))
        sender = cls._parse_message_sender(metadata) or cls._normalize_sender_label(payload.get("sender_label"))
        outgoing = bool(payload.get("outgoing"))
        parsed_datetime = cls._parse_message_datetime(metadata)
        reply_to = cls._parse_reply_reference(payload)

        if not sender:
            sender = chat.name if outgoing else "unknown"

        if has_image:
            caption = cls._normalize_image_caption(payload.get("caption_text"), fallback=message_text)
            key = payload.get("message_id") or f"{metadata or payload.get('preview_url') or ''}|image|{caption or ''}"
            return ChatImageMessage(
                id=key,
                chat=chat,
                sender=sender,
                direction=MessageDirection.OUTBOUND if outgoing else MessageDirection.INBOUND,
                text=caption or "",
                sent_at=parsed_datetime,
                metadata=metadata,
                reply_to=reply_to,
                caption=caption or None,
                preview_url=payload.get("preview_url"),
                width=cls._coerce_optional_int(payload.get("image_width")),
                height=cls._coerce_optional_int(payload.get("image_height")),
            )

        if not message_text:
            return None
        if metadata is None and cls._TIME_ONLY_PATTERN.fullmatch(message_text):
            return None

        key = payload.get("message_id") or f"{metadata or ''}|{message_text}"
        return ChatTextMessage(
            id=key,
            chat=chat,
            sender=sender,
            direction=MessageDirection.OUTBOUND if outgoing else MessageDirection.INBOUND,
            text=message_text,
            sent_at=parsed_datetime,
            metadata=metadata,
            reply_to=reply_to,
        )

    @classmethod
    def _normalize_message_text(cls, value: str | None) -> str:
        if not value:
            return ""

        lines = [line.strip() for line in str(value).splitlines()]
        normalized: list[str] = []
        previous_line = None
        for line in lines:
            if not line:
                continue
            if line == previous_line:
                continue
            normalized.append(line)
            previous_line = line
        return "\n".join(normalized).strip()

    @classmethod
    def _normalize_image_caption(cls, value: str | None, *, fallback: str | None = None) -> str:
        caption = cls._normalize_message_text(value)
        if not caption:
            caption = cls._normalize_message_text(fallback)
        if cls._TIME_ONLY_PATTERN.fullmatch(caption):
            return ""
        return caption

    @classmethod
    def _parse_reply_reference(cls, payload: dict | None) -> ReplyReference | None:
        if payload is None:
            return None

        sender = cls._normalize_sender_label(payload.get("quoted_sender"))
        text = cls._normalize_message_text(payload.get("quoted_text"))
        if not sender and not text:
            return None
        return ReplyReference(sender=sender or None, text=text)

    @staticmethod
    def mark_new_messages(
        chat: ChatRef,
        messages: Sequence[ChatMessage],
        last_message_id_by_chat: dict[str, str],
    ) -> list[ChatMessage]:
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
    def dedupe_messages(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
        deduped: list[ChatMessage] = []
        seen: set[str] = set()
        for message in messages:
            if message.id in seen:
                continue
            deduped.append(message)
            seen.add(message.id)
        return deduped

    @staticmethod
    def _normalize_sender_label(value: str | None) -> str:
        if not value:
            return ""
        return value.removesuffix(":").strip()

    @staticmethod
    def _coerce_optional_int(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
