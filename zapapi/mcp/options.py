from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zapapi.config import DEFAULT_PROFILE_PATH


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
    allowed_tools: tuple[str, ...] | None = None
    read_chat_allowlist: tuple[str, ...] | None = None
    write_chat_allowlist: tuple[str, ...] | None = None
    allowed_image_dirs: tuple[Path, ...] | None = None
    require_approval_for: tuple[str, ...] | None = None


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
