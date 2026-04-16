from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AuthState(str, Enum):
    LOADING = "loading"
    QR_REQUIRED = "qr_required"
    READY = "ready"


class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


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
class ChatMessage:
    id: str
    chat: ChatRef
    text: str


@dataclass(frozen=True, slots=True)
class ChatTextMessage(ChatMessage):
    sender: str
    direction: MessageDirection
    is_new: bool = False
    sent_at: datetime | None = None
    metadata: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryPage:
    chat: ChatRef
    messages: tuple[ChatTextMessage, ...]
    cursor: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class PollResult:
    chats: tuple[ChatRef, ...]
    messages: tuple[ChatTextMessage, ...]
