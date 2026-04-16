from __future__ import annotations

import logging

from .config import ZapAPIConfig
from .errors import (
    AuthenticationRequiredException,
    ChatNotFoundException,
    NoOpenChatException,
    WhatsAppWebTimeoutException,
)
from .api import ZapAPI
from .models import (
    AuthState,
    AuthStatus,
    ChatMessage,
    ChatRef,
    ChatSummary,
    ChatTextMessage,
    HistoryPage,
    MessageDirection,
    PollResult,
)
from .services import AuthService, ChatService, InboxService, MessageService

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "AuthenticationRequiredException",
    "AuthService",
    "AuthState",
    "AuthStatus",
    "ChatNotFoundException",
    "ChatMessage",
    "ChatRef",
    "ChatSummary",
    "ChatTextMessage",
    "ChatService",
    "HistoryPage",
    "InboxService",
    "MessageDirection",
    "MessageService",
    "NoOpenChatException",
    "PollResult",
    "ZapAPI",
    "ZapAPIConfig",
    "WhatsAppWebTimeoutException",
]
