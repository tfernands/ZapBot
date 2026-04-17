from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from zapapi.config import DEFAULT_PROFILE_PATH

from .options import ServerOptions


def parse_args(argv: list[str] | None = None) -> ServerOptions:
    parser = argparse.ArgumentParser(
        description="Servidor MCP stdio para a ZapAPI.",
    )
    parser.add_argument(
        "--user-data-dir",
        default=str(DEFAULT_PROFILE_PATH),
        help=(
            "Diretorio persistente do perfil do WhatsApp Web. "
            f"Padrao: {DEFAULT_PROFILE_PATH}"
        ),
    )
    parser.add_argument(
        "--headless",
        type=_parse_bool,
        default=True,
        help="Executa o Chromium em modo headless (true/false).",
    )
    parser.add_argument(
        "--debug-level",
        default="INFO",
        help="Nivel de log Python: DEBUG, INFO, WARNING, ERROR ou CRITICAL.",
    )
    parser.add_argument(
        "--base-url",
        default="https://web.whatsapp.com/",
        help="URL base do WhatsApp Web.",
    )
    parser.add_argument(
        "--launch-timeout-ms",
        type=int,
        default=30000,
        help="Timeout de abertura do browser em milissegundos.",
    )
    parser.add_argument(
        "--action-timeout-ms",
        type=int,
        default=5000,
        help="Timeout de interacoes do Playwright em milissegundos.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=0.5,
        help="Intervalo padrao de polling da inbox em segundos.",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        help="Atraso artificial do Playwright em milissegundos.",
    )
    parser.add_argument(
        "--allow-tool",
        action="append",
        default=None,
        help=(
            "Ferramenta permitida na whitelist. Repita a flag para liberar varias. "
            "auth_status e auth_ensure_ready sao incluidos automaticamente."
        ),
    )
    parser.add_argument(
        "--read-chat-allowlist",
        action="append",
        default=None,
        help="Chat permitido para leitura. Repita a flag ou use valores separados por virgula. Use ALL para liberar todos.",
    )
    parser.add_argument(
        "--write-chat-allowlist",
        action="append",
        default=None,
        help="Chat permitido para escrita. Repita a flag ou use valores separados por virgula. Use ALL para liberar todos.",
    )
    parser.add_argument(
        "--allow-image-dir",
        action="append",
        default=None,
        help="Diretorio permitido para envio de imagens. Repita a flag se necessario.",
    )
    parser.add_argument(
        "--require-approval-for",
        action="append",
        default=None,
        help="Ferramenta que exige approval.confirm=true. Repita a flag para varias.",
    )
    args = parser.parse_args(argv)

    return ServerOptions(
        user_data_dir=Path(args.user_data_dir),
        headless=args.headless,
        debug_level=_parse_log_level(args.debug_level),
        base_url=args.base_url,
        launch_timeout_ms=args.launch_timeout_ms,
        action_timeout_ms=args.action_timeout_ms,
        poll_interval_seconds=args.poll_interval_seconds,
        slow_mo=args.slow_mo,
        allowed_tools=_split_cli_values(args.allow_tool),
        read_chat_allowlist=_split_cli_values(args.read_chat_allowlist),
        write_chat_allowlist=_split_cli_values(args.write_chat_allowlist),
        allowed_image_dirs=_split_path_values(args.allow_image_dir),
        require_approval_for=_split_cli_values(args.require_approval_for),
    )


def main(argv: list[str] | None = None) -> None:
    from .server import build_server

    options = parse_args(argv)
    logging.basicConfig(
        level=options.debug_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = build_server(options)
    server.serve()


# -- Private CLI helpers -----------------------------------------------------


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("Use true ou false.")


def _parse_log_level(raw_value: str) -> int:
    normalized = raw_value.strip().upper()
    if normalized.isdigit():
        return int(normalized)
    if normalized not in logging._nameToLevel:
        raise argparse.ArgumentTypeError(
            "Use DEBUG, INFO, WARNING, ERROR, CRITICAL ou um numero inteiro."
        )
    return logging._nameToLevel[normalized]


def _split_cli_values(raw_values: list[str] | None) -> tuple[str, ...] | None:
    if not raw_values:
        return None

    values: list[str] = []
    for raw_value in raw_values:
        for chunk in raw_value.split(","):
            normalized = chunk.strip()
            if normalized:
                values.append(normalized)

    return tuple(values) or None


def _split_path_values(raw_values: list[str] | None) -> tuple[Path, ...] | None:
    values = _split_cli_values(raw_values)
    if values is None:
        return None
    return tuple(Path(value) for value in values)
