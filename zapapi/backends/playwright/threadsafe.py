from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from typing import Any, Callable, Sequence

from ...config import ZapAPIConfig
from ...models import AuthStatus, ChatRef, ChatSummary, HistoryPage, InboxEntry, PollResult, SearchHit
from .client import SyncPlaywrightZapAPI


class ThreadBoundPlaywrightZapAPI:
    """Run the sync Playwright backend on a dedicated worker thread.

    Playwright's sync API cannot be started on a thread that already has a
    running asyncio loop. By pinning the real backend to its own thread, the
    public ZapAPI surface stays synchronous while remaining callable from async
    hosts such as MCP runtimes.
    """

    def __init__(
        self,
        config: ZapAPIConfig,
        *,
        client_factory: Callable[[], SyncPlaywrightZapAPI] | None = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or (lambda: SyncPlaywrightZapAPI(config=self.config))
        self._executor: ThreadPoolExecutor | None = None
        self._client_future: Future[SyncPlaywrightZapAPI] | None = None
        self._lock = threading.RLock()

    def start(self) -> "ThreadBoundPlaywrightZapAPI":
        self._invoke("start")
        return self

    def close(self) -> None:
        with self._lock:
            executor = self._executor
            client_future = self._client_future
            self._executor = None
            self._client_future = None

        if executor is None or client_future is None:
            return

        close_future = executor.submit(self._close_client, client_future)
        try:
            close_future.result()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    def auth_status(self) -> AuthStatus:
        return self._invoke("auth_status")

    def ensure_ready(self, timeout_ms: int | None = None) -> AuthStatus:
        return self._invoke("ensure_ready", timeout_ms=timeout_ms)

    def current_chat(self) -> ChatRef:
        return self._invoke("current_chat")

    def list_chats(
        self,
        unread_only: bool = False,
        limit: int | None = None,
        scroll_steps: int = 0,
    ) -> list[ChatSummary]:
        return self._invoke(
            "list_chats",
            unread_only=unread_only,
            limit=limit,
            scroll_steps=scroll_steps,
        )

    def select_chat(self, target: str | ChatRef, exact_match: bool = True) -> ChatRef:
        return self._invoke("select_chat", target, exact_match=exact_match)

    def find_chats(
        self,
        query: str,
        *,
        limit: int = 10,
        scroll_steps: int = 2,
    ) -> list[ChatSummary]:
        return self._invoke(
            "find_chats",
            query,
            limit=limit,
            scroll_steps=scroll_steps,
        )

    def send_text(self, chat: str | ChatRef, text: str) -> ChatRef:
        return self._invoke("send_text", chat, text)

    def send_image(self, chat: str | ChatRef, image_path: str) -> ChatRef:
        return self._invoke("send_image", chat, image_path)

    def history(
        self,
        chat: str | ChatRef,
        *,
        limit: int = 50,
        before: str | None = None,
        max_scroll_steps: int = 10,
    ) -> HistoryPage:
        return self._invoke(
            "history",
            chat,
            limit=limit,
            before=before,
            max_scroll_steps=max_scroll_steps,
        )

    def poll(
        self,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit_per_chat: int = 50,
    ) -> PollResult:
        return self._invoke("poll", chats=chats, limit_per_chat=limit_per_chat)

    def inbox(
        self,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit_per_chat: int = 10,
        scroll_steps: int = 3,
        max_chats: int | None = None,
    ) -> list[InboxEntry]:
        return self._invoke(
            "inbox",
            chats=chats,
            limit_per_chat=limit_per_chat,
            scroll_steps=scroll_steps,
            max_chats=max_chats,
        )

    def search_messages(
        self,
        query: str,
        *,
        chats: Sequence[str | ChatRef] | None = None,
        limit: int = 20,
        scroll_steps: int = 3,
        max_chats: int | None = None,
    ) -> list[SearchHit]:
        return self._invoke(
            "search_messages",
            query,
            chats=chats,
            limit=limit,
            scroll_steps=scroll_steps,
            max_chats=max_chats,
        )

    def _ensure_worker(self) -> tuple[ThreadPoolExecutor, Future[SyncPlaywrightZapAPI]]:
        with self._lock:
            executor = self._executor
            client_future = self._client_future
            if executor is None or client_future is None:
                executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="zapapi-playwright",
                )
                client_future = executor.submit(self._client_factory)
                self._executor = executor
                self._client_future = client_future

        try:
            client_future.result()
        except BaseException:
            with self._lock:
                if self._executor is executor:
                    self._executor = None
                    self._client_future = None
            executor.shutdown(wait=True, cancel_futures=True)
            raise

        return executor, client_future

    def _invoke(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            executor, client_future = self._ensure_worker()
            future = executor.submit(
                self._call_method,
                client_future,
                method_name,
                args,
                kwargs,
            )
            return future.result()

    @staticmethod
    def _call_method(
        client_future: Future[SyncPlaywrightZapAPI],
        method_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        client = client_future.result()
        method = getattr(client, method_name)
        return method(*args, **kwargs)

    @staticmethod
    def _close_client(client_future: Future[SyncPlaywrightZapAPI]) -> None:
        client = client_future.result()
        client.close()
