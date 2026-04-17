from __future__ import annotations

import asyncio
import threading
import unittest

from zapapi.api import ZapAPI
from zapapi.backends.playwright.threadsafe import ThreadBoundPlaywrightZapAPI
from zapapi.config import ZapAPIConfig
from zapapi.models import AuthState, AuthStatus


class FakeThreadClient:
    def __init__(self, factory_calls: list[int]) -> None:
        self.factory_calls = factory_calls
        self.factory_calls.append(threading.get_ident())
        self.call_threads: list[tuple[str, int]] = []
        self.close_calls = 0

    def start(self) -> "FakeThreadClient":
        self.call_threads.append(("start", threading.get_ident()))
        return self

    def close(self) -> None:
        self.close_calls += 1
        self.call_threads.append(("close", threading.get_ident()))

    def auth_status(self) -> AuthStatus:
        self.call_threads.append(("auth_status", threading.get_ident()))
        return AuthStatus(
            state=AuthState.READY,
            authenticated=True,
            detail="ready",
        )


class ThreadBoundPlaywrightTests(unittest.TestCase):
    def _config(self) -> ZapAPIConfig:
        return ZapAPIConfig.from_kwargs(
            user_data_dir="./userdata/profile/wpp-playwright",
            launch_timeout_ms=1234,
            action_timeout_ms=100,
            poll_interval_seconds=0.01,
        )

    def test_runs_backend_methods_on_dedicated_thread_inside_asyncio(self) -> None:
        factory_threads: list[int] = []
        created_clients: list[FakeThreadClient] = []
        caller_thread = threading.get_ident()

        def factory() -> FakeThreadClient:
            client = FakeThreadClient(factory_threads)
            created_clients.append(client)
            return client

        adapter = ThreadBoundPlaywrightZapAPI(
            config=self._config(),
            client_factory=factory,
        )

        async def scenario() -> AuthStatus:
            adapter.start()
            return adapter.auth_status()

        status = asyncio.run(scenario())
        adapter.close()

        self.assertTrue(status.authenticated)
        self.assertEqual(len(created_clients), 1)
        client = created_clients[0]
        worker_threads = {thread_id for _, thread_id in client.call_threads}
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(next(iter(worker_threads)), caller_thread)
        self.assertEqual(factory_threads, [next(iter(worker_threads))])

    def test_can_restart_after_close(self) -> None:
        factory_threads: list[int] = []

        def factory() -> FakeThreadClient:
            return FakeThreadClient(factory_threads)

        adapter = ThreadBoundPlaywrightZapAPI(
            config=self._config(),
            client_factory=factory,
        )

        adapter.start()
        adapter.close()
        adapter.start()
        status = adapter.auth_status()
        adapter.close()

        self.assertTrue(status.authenticated)
        self.assertEqual(len(factory_threads), 2)

    def test_zapapi_uses_thread_bound_backend(self) -> None:
        api = ZapAPI(user_data_dir="./userdata/profile/wpp-playwright")
        self.assertIsInstance(api._client, ThreadBoundPlaywrightZapAPI)
        api.close()


if __name__ == "__main__":
    unittest.main()
