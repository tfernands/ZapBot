import logging

from zapapi import ZapAPI


PROFILE_DIR = "./userdata/profile/wpp-playwright"


if __name__ == "__main__":
    with ZapAPI(
        user_data_dir=PROFILE_DIR,
        headless=False,
        launch_timeout_ms=0,
        debug_level=logging.INFO,
    ) as api:
        status = api.auth.ensure_ready(timeout_ms=0)
        print(status.state.value)
        print("Sessao persistente pronta em", PROFILE_DIR)
