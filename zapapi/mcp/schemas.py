from __future__ import annotations

from typing import Any


CHAT_REF_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "string",
            "description": "Nome do chat, contato ou grupo no WhatsApp.",
        },
        {
            "type": "object",
            "description": "Objeto de chat retornado por outras ferramentas.",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nome visivel do chat.",
                },
                "key": {
                    "type": ["string", "null"],
                    "description": "Identificador interno quando disponivel.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ]
}

APPROVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Aprovacao explicita exigida pela politica do servidor antes de executar a ferramenta."
    ),
    "properties": {
        "confirm": {
            "type": "boolean",
            "description": "Deve ser true para autorizar a execucao.",
        },
        "reason": {
            "type": ["string", "null"],
            "description": "Motivo opcional registrado pelo cliente.",
        },
    },
    "required": ["confirm"],
    "additionalProperties": False,
}

# Tool sets used by SecurityPolicy to route allowlist checks.
READ_TOOLS = frozenset({"whatsapp_find_chat", "whatsapp_inbox", "whatsapp_read", "whatsapp_search"})
WRITE_TOOLS = frozenset({"whatsapp_send"})
DEFAULT_ALLOWED_TOOLS = frozenset({"whatsapp_status"})
