import logging

from zapapi import MessageDirection, ZapAPI


PROFILE_DIR = "./userdata/profile/wpp-playwright"
WATCH_CHATS = ["Eu"]


if __name__ == "__main__":
    with ZapAPI(
        user_data_dir=PROFILE_DIR,
        headless=True,
        debug_level=logging.INFO,
    ) as api:
        api.auth.ensure_ready()

        for result in api.inbox.listen(chats=WATCH_CHATS, interval_seconds=1.0):
            for message in result.messages:
                if message.direction is MessageDirection.OUTBOUND:
                    continue
                print(f"[{message.chat.name}] {message.sender}: {message.text}")
