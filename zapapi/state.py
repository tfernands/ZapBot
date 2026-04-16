from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InboxState:
    last_message_id_by_chat: dict[str, str] = field(default_factory=dict)
