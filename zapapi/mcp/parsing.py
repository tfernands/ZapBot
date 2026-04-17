from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from zapapi import ChatRef

from .errors import ToolInputError


class ArgumentParser:
    """Validates and extracts typed values from raw MCP tool arguments."""

    @staticmethod
    def chat_value(arguments: dict[str, Any], field_name: str) -> str | ChatRef:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            raise ToolInputError(f"O campo '{field_name}' e obrigatorio.")
        if isinstance(raw_value, str):
            if raw_value.strip():
                return raw_value
            raise ToolInputError(f"O campo '{field_name}' nao pode ser vazio.")
        if isinstance(raw_value, dict):
            name = raw_value.get("name")
            key = raw_value.get("key")
            if not isinstance(name, str) or not name.strip():
                raise ToolInputError(
                    f"O objeto '{field_name}' precisa conter um campo 'name' nao vazio."
                )
            if key is not None and not isinstance(key, str):
                raise ToolInputError(
                    f"O campo '{field_name}.key' precisa ser string ou null."
                )
            return ChatRef(name=name, key=key)
        raise ToolInputError(
            f"O campo '{field_name}' precisa ser uma string ou um objeto de chat."
        )

    @staticmethod
    def optional_chat_value(
        arguments: dict[str, Any],
        field_name: str,
    ) -> str | ChatRef | None:
        if field_name not in arguments or arguments.get(field_name) is None:
            return None
        return ArgumentParser.chat_value(arguments, field_name)

    @staticmethod
    def optional_chat_list(
        arguments: dict[str, Any],
        field_name: str,
    ) -> list[str | ChatRef] | None:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return None
        if not isinstance(raw_value, list):
            raise ToolInputError(f"O campo '{field_name}' precisa ser uma lista de chats.")
        return [ArgumentParser.chat_value({field_name: item}, field_name) for item in raw_value]

    @staticmethod
    def required_string(arguments: dict[str, Any], field_name: str) -> str:
        raw_value = arguments.get(field_name)
        if not isinstance(raw_value, str):
            raise ToolInputError(f"O campo '{field_name}' precisa ser uma string.")
        if not raw_value.strip():
            raise ToolInputError(f"O campo '{field_name}' nao pode ser vazio.")
        return raw_value

    @staticmethod
    def optional_string(arguments: dict[str, Any], field_name: str) -> str | None:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            raise ToolInputError(f"O campo '{field_name}' precisa ser uma string ou null.")
        return raw_value

    @staticmethod
    def optional_bool(
        arguments: dict[str, Any],
        field_name: str,
        *,
        default: bool,
    ) -> bool:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return default
        if not isinstance(raw_value, bool):
            raise ToolInputError(f"O campo '{field_name}' precisa ser booleano.")
        return raw_value

    @staticmethod
    def optional_int(
        arguments: dict[str, Any],
        field_name: str,
        *,
        minimum: int | None = None,
        default: int | None = None,
    ) -> int | None:
        raw_value = arguments.get(field_name)
        if raw_value is None:
            return default
        if isinstance(raw_value, bool):
            raise ToolInputError(f"O campo '{field_name}' precisa ser um inteiro.")
        if isinstance(raw_value, float):
            if raw_value.is_integer():
                raw_value = int(raw_value)
            else:
                raise ToolInputError(f"O campo '{field_name}' precisa ser um inteiro.")
        if not isinstance(raw_value, int):
            raise ToolInputError(f"O campo '{field_name}' precisa ser um inteiro.")
        if minimum is not None and raw_value < minimum:
            raise ToolInputError(
                f"O campo '{field_name}' precisa ser maior ou igual a {minimum}."
            )
        return raw_value

    @staticmethod
    def expect_no_arguments(arguments: dict[str, Any]) -> None:
        if arguments:
            unexpected = ", ".join(sorted(arguments))
            raise ToolInputError(f"Esta ferramenta nao aceita argumentos: {unexpected}.")


def serialize(value: Any) -> Any:
    """Recursively serialize dataclasses, enums, and other types to JSON-safe dicts."""
    if value is None:
        return None
    if is_dataclass(value):
        return {
            field.name: serialize(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): serialize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [serialize(item) for item in value]
    return value


def encode_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
