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
    ChatImageMessage,
    ChatMessage,
    ChatRef,
    ChatSummary,
    ChatTextMessage,
    HistoryPage,
    MessageDirection,
    MessageType,
    PollResult,
    ReplyReference,
)
from .services import AuthService, ChatService, InboxService, MessageService

logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.1.0"

__all__ = [
    "AuthenticationRequiredException",
    "AuthService",
    "AuthState",
    "AuthStatus",
    "ChatNotFoundException",
    "ChatImageMessage",
    "ChatMessage",
    "ChatRef",
    "ChatSummary",
    "ChatTextMessage",
    "ChatService",
    "HistoryPage",
    "InboxService",
    "MessageDirection",
    "MessageType",
    "MessageService",
    "NoOpenChatException",
    "PollResult",
    "ReplyReference",
    "ZapAPI",
    "ZapAPIConfig",
    "WhatsAppWebTimeoutException",
    "__version__",
]
