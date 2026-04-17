from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from zapapi import ChatImageMessage, ChatMessage, ChatRef, MessageDirection, ZapAPI
from zapapi.errors import ChatNotFoundException


PROFILE_DIR = Path(os.getenv("ZAPAPI_PROFILE_DIR", "./userdata/profile/wpp-playwright"))
STATE_FILE = Path(os.getenv("ZAPBOT_STATE_FILE", "./userdata/grilo_listener_state.json"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("ZAPBOT_OPENAI_MODEL", "gpt-4.1-mini")
POLL_INTERVAL_SECONDS = float(os.getenv("ZAPBOT_POLL_INTERVAL_SECONDS", "2.0"))
HISTORY_LIMIT = int(os.getenv("ZAPBOT_HISTORY_LIMIT", "12"))
CHAT_ALIASES = [
    alias.strip()
    for alias in os.getenv("ZAPBOT_CHAT_ALIASES", "Grilo,Sabrina (Gri),Sabrina (Grilo)").split(",")
    if alias.strip()
]


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"handled_ids": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"handled_ids": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def resolve_target_chat(api: ZapAPI) -> ChatRef:
    for alias in CHAT_ALIASES:
        try:
            return api.chats.get(alias, exact_match=True)
        except ChatNotFoundException:
            continue

    visible = api.chats.list(limit=40, scroll_steps=6)
    lowered_aliases = [alias.casefold() for alias in CHAT_ALIASES]
    for chat in visible:
        chat_name = chat.name.casefold()
        if any(alias in chat_name or chat_name in alias for alias in lowered_aliases):
            return api.chats.get(chat.name, exact_match=True)

    names = ", ".join(chat.name for chat in visible[:20])
    raise RuntimeError(f"Nao encontrei o chat alvo. Chats visiveis: {names}")


def format_message(message: ChatMessage) -> str:
    direction = "eu" if message.direction is MessageDirection.OUTBOUND else message.sender
    if isinstance(message, ChatImageMessage):
        text = message.caption or "[imagem sem legenda]"
    else:
        text = message.text or "[mensagem vazia]"
    return f"{direction}: {text}"


def build_input(history: list[ChatMessage], incoming: ChatMessage) -> str:
    lines = [format_message(message) for message in history[-HISTORY_LIMIT:]]
    latest = format_message(incoming)
    return (
        "Voce esta respondendo mensagens de WhatsApp em portugues do Brasil no chat de uma pessoa chamada Grilo.\n"
        "Objetivo: responder naturalmente, de forma curta e util, como se fosse o Thales.\n"
        "Regras:\n"
        "- Pense antes de responder.\n"
        "- Seja casual e humano, sem soar como assistente.\n"
        "- Priorize respostas curtas, normalmente 1 ou 2 frases.\n"
        "- Nao invente fatos nem combinados que nao apareceram no contexto.\n"
        "- Se a mensagem pedir algo pratico, confirme ou responda diretamente.\n"
        "- Nao use emojis a menos que o contexto puxe isso claramente.\n"
        "- Nao diga que voce e IA.\n"
        "- Responda apenas com o texto final que deve ser enviado.\n\n"
        "Contexto recente do chat:\n"
        + "\n".join(lines)
        + "\n\nUltima mensagem recebida:\n"
        + latest
    )


def generate_reply(history: list[ChatMessage], incoming: ChatMessage) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY nao configurada.")

    payload = {
        "model": OPENAI_MODEL,
        "input": build_input(history, incoming),
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=data,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Falha na OpenAI API: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede ao chamar OpenAI API: {exc.reason}") from exc

    text = (body.get("output_text") or "").strip()
    if not text:
        raise RuntimeError(f"Resposta vazia da OpenAI API: {body}")
    return text


def should_reply(message: ChatMessage, handled_ids: set[str]) -> bool:
    if message.direction is not MessageDirection.INBOUND:
        return False
    if message.id in handled_ids:
        return False
    if not (message.text or (isinstance(message, ChatImageMessage) and message.caption)):
        return False
    return True


def prime_listener(api: ZapAPI, chat: ChatRef) -> None:
    api.inbox.poll(chats=[chat], limit_per_chat=HISTORY_LIMIT)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    state = load_state(STATE_FILE)
    handled_ids = set(state.get("handled_ids", []))

    with ZapAPI(
        user_data_dir=PROFILE_DIR,
        headless=False,
        debug_level=logging.INFO,
    ) as api:
        api.auth.ensure_ready(timeout_ms=30000)
        chat = resolve_target_chat(api)
        logging.info("Escutando chat: %s", chat.name)
        prime_listener(api, chat)

        while True:
            try:
                poll = api.inbox.poll(chats=[chat], limit_per_chat=HISTORY_LIMIT)
                for message in poll.messages:
                    if not should_reply(message, handled_ids):
                        continue

                    history = list(api.messages.history(chat, limit=HISTORY_LIMIT).messages)
                    logging.info("Mensagem nova de %s: %s", message.sender, message.text or "[imagem]")
                    reply = generate_reply(history, message)
                    logging.info("Resposta gerada: %s", reply)
                    api.messages.send_text(chat, reply)
                    handled_ids.add(message.id)
                    state["handled_ids"] = sorted(handled_ids)
                    save_state(STATE_FILE, state)
            except KeyboardInterrupt:
                logging.info("Encerrado pelo usuario.")
                break
            except Exception as exc:  # noqa: BLE001
                logging.exception("Falha no loop de escuta: %s", exc)
                time.sleep(max(POLL_INTERVAL_SECONDS, 5.0))
                continue

            time.sleep(POLL_INTERVAL_SECONDS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
