from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AuthState(str, Enum):
    LOADING = "loading"
    QR_REQUIRED = "qr_required"
    READY = "ready"


class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class AuthStatus:
    state: AuthState
    authenticated: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ChatRef:
    name: str
    key: str | None = None


@dataclass(frozen=True, slots=True)
class ChatSummary(ChatRef):
    preview: str | None = None
    timestamp: str | None = None
    unread_count: int = 0


@dataclass(frozen=True, slots=True)
class ReplyReference:
    sender: str | None = None
    text: str = ""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    id: str
    chat: ChatRef
    sender: str
    direction: MessageDirection
    text: str = ""
    is_new: bool = False
    sent_at: datetime | None = None
    metadata: str | None = None
    reply_to: ReplyReference | None = None


@dataclass(frozen=True, slots=True)
class ChatTextMessage(ChatMessage):
    message_type: MessageType = field(default=MessageType.TEXT, init=False)


@dataclass(frozen=True, slots=True)
class ChatImageMessage(ChatMessage):
    caption: str | None = None
    preview_url: str | None = None
    width: int | None = None
    height: int | None = None
    message_type: MessageType = field(default=MessageType.IMAGE, init=False)


@dataclass(frozen=True, slots=True)
class HistoryPage:
    chat: ChatRef
    messages: tuple[ChatMessage, ...]
    cursor: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class PollResult:
    chats: tuple[ChatRef, ...]
    messages: tuple[ChatMessage, ...]
