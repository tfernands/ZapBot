from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from zapapi import ChatRef

from .errors import ToolAccessError, ToolInputError
from .options import ServerOptions
from .parsing import ArgumentParser
from .schemas import (
    APPROVAL_SCHEMA,
    DEFAULT_ALLOWED_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
)


class SecurityPolicy:
    """Encapsulates all allowlist normalization, tool authorization, and chat filtering logic."""

    def __init__(self, options: ServerOptions) -> None:
        self._options = options
        self._allowed_tools = self._normalized_allowed_tool_names(options.allowed_tools)
        self._require_approval_for = self._normalized_tool_names(options.require_approval_for)
        self._read_chat_allowlist = self._normalized_chat_names(options.read_chat_allowlist)
        self._write_chat_allowlist = self._normalized_chat_names(options.write_chat_allowlist)
        self._visible_chat_allowlist = self._build_visible_chat_allowlist()
        self._allowed_image_dirs = self._normalized_image_dirs(options.allowed_image_dirs)

    # -- Public properties ---------------------------------------------------

    @property
    def read_chat_allowlist(self) -> set[str] | None:
        return self._read_chat_allowlist

    @property
    def write_chat_allowlist(self) -> set[str] | None:
        return self._write_chat_allowlist

    @property
    def visible_chat_allowlist(self) -> set[str] | None:
        return self._visible_chat_allowlist

    # -- Tool visibility -----------------------------------------------------

    def is_tool_allowed(self, name: str) -> bool:
        return self._allowed_tools is None or name in self._allowed_tools

    def requires_approval(self, name: str) -> bool:
        return self._require_approval_for is not None and name in self._require_approval_for

    def schema_for_tool(self, tool_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not self.requires_approval(tool_name):
            return schema

        configured = deepcopy(schema)
        properties = dict(configured.get("properties") or {})
        properties["approval"] = deepcopy(APPROVAL_SCHEMA)
        configured["properties"] = properties

        required = list(configured.get("required") or [])
        if "approval" not in required:
            required.append("approval")
        configured["required"] = required
        return configured

    # -- Authorization -------------------------------------------------------

    def authorize_tool(self, name: str, arguments: dict[str, Any]) -> None:
        if not self.is_tool_allowed(name):
            raise ToolAccessError(f"A ferramenta '{name}' nao esta na whitelist do servidor.")

        self._enforce_chat_allowlists(name, arguments)
        self._enforce_image_path_policy(name, arguments)
        self._enforce_explicit_approval(name, arguments)

    def sanitize_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in arguments.items() if key != "approval"}

    # -- Chat filtering & accessibility --------------------------------------

    def filter_visible_chats(self, chats: list[Any]) -> list[Any]:
        if self._visible_chat_allowlist is None:
            return chats
        return [
            chat
            for chat in chats
            if self._is_chat_target_allowed(chat, self._visible_chat_allowlist)
        ]

    def is_chat_accessible(self, name: str, allowlist: set[str] | None) -> bool:
        if allowlist is None:
            return True
        return name.casefold() in allowlist

    # -- Validation (called once at init) ------------------------------------

    def validate_configuration(self, known_tools: set[str]) -> None:
        referenced_tools = set(self._options.allowed_tools or ()) | set(
            self._options.require_approval_for or ()
        )
        unknown_tools = sorted(referenced_tools - known_tools)
        if unknown_tools:
            raise ValueError(
                "Ferramentas desconhecidas na configuracao de seguranca: "
                + ", ".join(unknown_tools)
                + "."
            )


    # -- Private enforcement -------------------------------------------------

    def _enforce_explicit_approval(self, name: str, arguments: dict[str, Any]) -> None:
        if not self.requires_approval(name):
            return

        approval = arguments.get("approval")
        if not isinstance(approval, dict):
            raise ToolAccessError(
                f"A ferramenta '{name}' exige approval.confirm=true antes da execucao."
            )

        unexpected = sorted(set(approval) - {"confirm", "reason"})
        if unexpected:
            raise ToolInputError(
                "Campos inesperados em approval: " + ", ".join(unexpected) + "."
            )

        if approval.get("confirm") is not True:
            raise ToolAccessError(
                f"A ferramenta '{name}' exige approval.confirm=true antes da execucao."
            )

        reason = approval.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ToolInputError("O campo 'approval.reason' precisa ser uma string nao vazia ou null.")

    def _enforce_chat_allowlists(self, name: str, arguments: dict[str, Any]) -> None:
        if name in WRITE_TOOLS:
            targets = self._extract_chat_targets(arguments)
            self._ensure_targets_allowed(name, targets, self._write_chat_allowlist, "write")
        elif name in READ_TOOLS:
            targets = self._extract_chat_targets(arguments)
            self._ensure_targets_allowed(name, targets, self._read_chat_allowlist, "read")

    def _enforce_image_path_policy(self, name: str, arguments: dict[str, Any]) -> None:
        if name != "whatsapp_send" or self._allowed_image_dirs is None:
            return

        image_path = arguments.get("image_path")
        if image_path is None:
            return

        resolved_path = Path(image_path).expanduser().resolve(strict=False)
        if any(resolved_path.is_relative_to(allowed_dir) for allowed_dir in self._allowed_image_dirs):
            return

        allowed_dirs = ", ".join(str(path) for path in self._allowed_image_dirs)
        raise ToolAccessError(
            "O caminho da imagem nao esta na allowlist de diretorios permitidos: "
            f"{allowed_dirs}."
        )

    def _extract_chat_targets(self, arguments: dict[str, Any]) -> list[str | ChatRef]:
        """Extract chat targets from arguments, supporting both single 'chat' and list 'chats'."""
        targets: list[str | ChatRef] = []
        chat = arguments.get("chat")
        if chat is not None:
            if isinstance(chat, str):
                targets.append(chat)
            elif isinstance(chat, dict) and "name" in chat:
                targets.append(ChatRef(name=chat["name"], key=chat.get("key")))
        chats = arguments.get("chats")
        if isinstance(chats, list):
            for item in chats:
                if isinstance(item, str):
                    targets.append(item)
                elif isinstance(item, dict) and "name" in item:
                    targets.append(ChatRef(name=item["name"], key=item.get("key")))
        return targets

    def _ensure_targets_allowed(
        self,
        tool_name: str,
        targets: list[str | ChatRef],
        allowed_names: set[str] | None,
        label: str,
    ) -> None:
        if allowed_names is None:
            return
        for target in targets:
            if self._is_chat_target_allowed(target, allowed_names):
                continue
            chat_name = self._chat_name(target)
            raise ToolAccessError(
                f"A ferramenta '{tool_name}' nao pode acessar o chat '{chat_name}' fora da {label} chat allowlist."
            )

    # -- Normalization helpers -----------------------------------------------

    def _build_visible_chat_allowlist(self) -> set[str] | None:
        # If either allowlist is unrestricted (None), all chats should be visible.
        if self._read_chat_allowlist is None or self._write_chat_allowlist is None:
            return None
        # Both have explicit lists — visible is the union.
        return self._read_chat_allowlist | self._write_chat_allowlist

    @staticmethod
    def _normalized_allowed_tool_names(values: tuple[str, ...] | None) -> set[str] | None:
        normalized = SecurityPolicy._normalized_tool_names(values)
        if normalized is None:
            return None
        normalized.update(DEFAULT_ALLOWED_TOOLS)
        return normalized

    @staticmethod
    def _normalized_tool_names(values: tuple[str, ...] | None) -> set[str] | None:
        if not values:
            return None
        return {value.strip() for value in values if value.strip()}

    @staticmethod
    def _normalized_chat_names(values: tuple[str, ...] | None) -> set[str] | None:
        if not values:
            return None
        # "ALL" means unrestricted — treat as if no allowlist was provided.
        names = [v.strip() for v in values if v.strip()]
        if any(name.upper() == "ALL" for name in names):
            return None
        return {name.casefold() for name in names}

    @staticmethod
    def _normalized_image_dirs(values: tuple[Path, ...] | None) -> tuple[Path, ...] | None:
        if not values:
            return None
        return tuple(Path(value).expanduser().resolve(strict=False) for value in values)

    @staticmethod
    def _is_chat_target_allowed(value: str | ChatRef | Any, allowed_names: set[str]) -> bool:
        return SecurityPolicy._chat_name(value).casefold() in allowed_names

    @staticmethod
    def _is_chat_name_allowed(name: str, allowed_names: set[str]) -> bool:
        return name.casefold() in allowed_names

    @staticmethod
    def _chat_name(value: str | ChatRef | Any) -> str:
        if isinstance(value, ChatRef):
            return value.name
        if isinstance(value, str):
            return value
        if hasattr(value, "name") and isinstance(value.name, str):
            return value.name
        raise ToolInputError("Nao foi possivel determinar o nome do chat para aplicar a politica.")
