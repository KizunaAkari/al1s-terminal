"""One in-flight operation, no executor backlog; exceptions return to the owner."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor


class SingleFlight:
    def __init__(self, name: str) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self._future: Future[object] | None = None

    def poll(self, operation: Callable[[], object]) -> None:
        if self._future is not None:
            if not self._future.done():
                return
            completed, self._future = self._future, None
            completed.result()
        self._future = self._pool.submit(operation)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
