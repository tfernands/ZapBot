import logging

from zapapi import ChatImageMessage, ChatSummary, MessageDirection, ZapAPI


PROFILE_DIR = "./userdata/profile/wpp-playwright"


def prompt_chat_choice(chats: list[ChatSummary]) -> ChatSummary:
    if not chats:
        raise RuntimeError("Nenhum chat visivel foi encontrado na lista lateral.")

    print("Selecione um chat para escutar:")
    for index, chat in enumerate(chats, start=1):
        unread = f" ({chat.unread_count} nao lidas)" if chat.unread_count else ""
        preview = f" - {chat.preview}" if chat.preview else ""
        print(f"{index}. {chat.name}{unread}{preview}")

    while True:
        raw_value = input(f"Escolha um chat [1-{len(chats)}]: ").strip()
        normalized_value = raw_value.removesuffix(".").strip()
        if not normalized_value.isdigit():
            print("Digite apenas o numero do chat.")
            continue

        selected_index = int(normalized_value)
        if 1 <= selected_index <= len(chats):
            return chats[selected_index - 1]

        print("Numero fora do intervalo listado.")


if __name__ == "__main__":
    with ZapAPI(
        user_data_dir=PROFILE_DIR,
        headless=True,
        debug_level=logging.INFO,
    ) as api:
        api.auth.ensure_ready(timeout_ms=30000)
        chats = api.chats.list(limit=5)
        selected_chat = prompt_chat_choice(chats)
        print(f"Escutando: {selected_chat.name}")

        for result in api.inbox.listen(chats=[selected_chat], interval_seconds=1.0):
            for message in result.messages:
                if message.direction is MessageDirection.OUTBOUND:
                    continue
                if isinstance(message, ChatImageMessage):
                    caption = f" {message.caption}" if message.caption else ""
                    print(f"[{message.chat.name}] {message.sender}: [imagem]{caption}")
                    continue
                print(f"[{message.chat.name}] {message.sender}: {message.text}")
