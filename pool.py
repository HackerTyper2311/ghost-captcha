"""Pre-generation pool for CAPTCHA videos.

Rendering an MP4 on demand is CPU intensive and a DoS vector.  Instead, a
background worker pre-renders videos into a bounded queue and HTTP requests
pop a ready-made video instantly.  When the queue dips below ``POOL_MIN`` the
worker refills it (up to ``POOL_MAX``).

Each pooled entry is a tuple of ``(code, mp4_bytes)`` so the request handler
can atomically pair the code with the video that encodes it.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

from captcha import generate_code, render_video_bytes
from config import POOL_MAX, POOL_MIN, POOL_REFILL_INTERVAL


@dataclass(frozen=True)
class PooledChallenge:
    code: str
    video: bytes


class ChallengePool:
    """A bounded, thread-safe pool of pre-rendered CAPTCHA videos."""

    def __init__(
        self,
        min_size: int = POOL_MIN,
        max_size: int = POOL_MAX,
        refill_interval: float = POOL_REFILL_INTERVAL,
    ) -> None:
        self._min = min_size
        self._max = max_size
        self._refill_interval = refill_interval
        self._q: queue.Queue[PooledChallenge] = queue.Queue(maxsize=max_size)
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------- #
    def start(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run, name="ghost-captcha-pool", daemon=True
        )
        self._worker.start()
        print(
            f"[ghost-captcha] pool: started (min={self._min}, max={self._max})"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=1.0)
            self._worker = None

    # -- public API ------------------------------------------------------- #
    def get(self, timeout: float = 5.0) -> PooledChallenge | None:
        """Pop a ready challenge, or None if the pool is temporarily empty."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def size(self) -> int:
        return self._q.qsize()

    def ensure_running(self) -> None:
        """Start (or restart) the worker if it is not alive.

        This is important when running under WSGI servers that fork workers
        after importing the module (e.g., gunicorn --preload); the original
        worker thread does not survive the fork, so we restart it on demand.
        """
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._stop.clear()
                self._worker = threading.Thread(
                    target=self._run, name="ghost-captcha-pool", daemon=True
                )
                self._worker.start()

    # -- worker ----------------------------------------------------------- #
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                # Only render when below the low-water mark.
                if self._q.qsize() < self._min:
                    needed = self._max - self._q.qsize()
                    for _ in range(needed):
                        if self._stop.is_set():
                            break
                        code = generate_code()
                        try:
                            video = render_video_bytes(code)
                        except Exception as exc:  # pragma: no cover - defensive
                            print(f"[ghost-captcha] pool: render failed: {exc}")
                            break
                        # put_nowait avoids blocking when another thread
                        # refilled the queue concurrently.
                        try:
                            self._q.put_nowait(PooledChallenge(code=code, video=video))
                        except queue.Full:
                            break
                self._stop.wait(self._refill_interval)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[ghost-captcha] pool: worker error: {exc}")
                self._stop.wait(self._refill_interval)


# Module-level singleton, started lazily by app.py.
pool = ChallengePool()
