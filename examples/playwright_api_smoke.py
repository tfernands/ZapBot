from __future__ import annotations

import argparse
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from zapapi import ChatImageMessage, ChatRef, MessageDirection, ZapAPI


DEFAULT_PROFILE_DIR = "./userdata/profile/wpp-playwright"
DEFAULT_CHAT_NAME = "Eu"


def find_token(messages, token: str):
    for message in messages:
        if token in message.text:
            return message
    return None


def find_message_by_id(messages, message_id: str):
    for message in messages:
        if message.id == message_id:
            return message
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test da API Playwright contra um chat do WhatsApp Web.")
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR, help="Diretorio do perfil persistente.")
    parser.add_argument("--chat", default=DEFAULT_CHAT_NAME, help="Nome do chat a ser usado no smoke test.")
    parser.add_argument("--image", help="Caminho de uma imagem para validar upload e parsing de mensagens com imagem.")
    parser.add_argument("--visible", action="store_true", help="Executa com browser visivel em vez de headless.")
    parser.add_argument("--timeout-ms", type=int, default=30000, help="Timeout para autenticacao.")
    args = parser.parse_args()

    token_prefix = datetime.now().strftime("%Y%m%d-%H%M%S")
    poll_token = f"[zapapi-poll {token_prefix}]"
    listen_token = f"[zapapi-listen {token_prefix}]"

    with ZapAPI(
        user_data_dir=args.profile_dir,
        headless=not args.visible,
    ) as api:
        auth_status = api.auth.status()
        print("auth.status():", asdict(auth_status))
        auth_status = api.auth.ensure_ready(timeout_ms=args.timeout_ms)
        print("auth.ensure_ready():", asdict(auth_status))

        chats = api.chats.list(limit=5)
        print("chats.list(limit=5):", [chat.name for chat in chats])

        chat = api.chats.get(args.chat)
        print("chats.get():", chat)
        current_chat = api.chats.current()
        print("chats.current():", current_chat)
        if current_chat.name != args.chat:
            raise RuntimeError(f"Chat atual divergente: esperado {args.chat!r}, obtido {current_chat.name!r}")

        history_page = api.messages.history(chat, limit=5, max_scroll_steps=5)
        print("messages.history(limit=5):", len(history_page.messages), history_page.cursor, history_page.has_more)
        for message in history_page.messages:
            print("history.message:", message.direction.value, repr(message.text))

        older_page = api.messages.history(
            chat,
            limit=5,
            before=history_page.cursor,
            max_scroll_steps=5,
        )
        print("messages.history(before=cursor):", len(older_page.messages), older_page.cursor, older_page.has_more)

        baseline = api.inbox.poll(chats=[chat], limit_per_chat=20)
        print("inbox.poll() baseline:", len(baseline.messages))

        api.messages.send_text(chat, poll_token)
        print("messages.send_text() poll token:", poll_token)

        poll_match = None
        for attempt in range(1, 11):
            result = api.inbox.poll(chats=[chat], limit_per_chat=20)
            print("inbox.poll() attempt:", attempt, "count:", len(result.messages))
            poll_match = find_token(result.messages, poll_token)
            if poll_match is not None:
                break
            time.sleep(1)

        if poll_match is None:
            raise RuntimeError("poll() nao retornou a mensagem enviada.")
        print("poll match:", poll_match.direction.value, poll_match.sender, repr(poll_match.text))

        history_after_send = api.messages.history(chat, limit=10, max_scroll_steps=5)
        if find_token(history_after_send.messages, poll_token) is None:
            raise RuntimeError("history() nao retornou a mensagem enviada.")
        print("history after send count:", len(history_after_send.messages))

        if args.image:
            image_path = Path(args.image).expanduser()
            if not image_path.is_file():
                raise FileNotFoundError(f"Imagem nao encontrada: {image_path}")

            api.inbox.poll(chats=[chat], limit_per_chat=20)
            api.messages.send_image(chat, str(image_path))
            print("messages.send_image():", str(image_path))

            image_match = None
            for attempt in range(1, 16):
                result = api.inbox.poll(chats=[chat], limit_per_chat=20)
                print("inbox.poll() image attempt:", attempt, "count:", len(result.messages))
                image_match = next(
                    (
                        message
                        for message in result.messages
                        if isinstance(message, ChatImageMessage)
                        and message.direction is MessageDirection.OUTBOUND
                    ),
                    None,
                )
                if image_match is not None:
                    break
                time.sleep(1)

            if image_match is None:
                raise RuntimeError("poll() nao retornou a imagem enviada.")
            print(
                "image poll match:",
                image_match.direction.value,
                image_match.sender,
                image_match.id,
                image_match.preview_url,
                f"{image_match.width}x{image_match.height}",
                repr(image_match.caption),
            )

            history_after_image = api.messages.history(chat, limit=10, max_scroll_steps=5)
            history_image_match = find_message_by_id(history_after_image.messages, image_match.id)
            if not isinstance(history_image_match, ChatImageMessage):
                raise RuntimeError("history() nao retornou a imagem enviada.")
            print("image history match:", history_image_match.id, repr(history_image_match.caption))

        api.inbox.poll(chats=[chat], limit_per_chat=20)
        listener = api.inbox.listen(chats=[chat], limit_per_chat=20, interval_seconds=0.5)
        api.messages.send_text(chat, listen_token)
        print("messages.send_text() listen token:", listen_token)

        listen_start = time.time()
        listen_batch = next(listener)
        listen_match = find_token(listen_batch.messages, listen_token)
        print("inbox.listen() batch:", len(listen_batch.messages), "elapsed:", round(time.time() - listen_start, 2))
        if listen_match is None:
            raise RuntimeError("listen() nao retornou a mensagem enviada.")
        print("listen match:", listen_match.direction.value, listen_match.sender, repr(listen_match.text))

        if listen_match.direction is not MessageDirection.OUTBOUND:
            raise RuntimeError("A mensagem enviada em 'Eu' deveria aparecer como outbound.")

        if not isinstance(chat, ChatRef):
            raise RuntimeError("chats.get() nao retornou ChatRef.")

    print("smoke test concluido com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
