"""Token store for the Ghost Font CAPTCHA service.

Two kinds of records are stored, both short-lived and single-use:

* **challenges** - created when the widget asks for a video.  They bind a
  random ``challenge_id`` to the secret code the user must type.  Verified
  (or expired) challenges are deleted so they can never be replayed.
* **tokens** - created after the user types the correct code.  The widget
  hands this token to the parent page, which submits it with the form.  The
  publisher's backend then calls ``/api/siteverify`` to redeem it.

The store auto-detects Redis via ``REDIS_URL``.  If Redis is unavailable it
falls back to a thread-safe in-memory dictionary so the service still runs
with zero external dependencies (single-instance only).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from config import CHALLENGE_TTL, REDIS_URL, TOKEN_TTL

# --------------------------------------------------------------------------- #
# Record types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Challenge:
    code: str
    origin: str
    created_at: float


@dataclass(frozen=True)
class Token:
    origin: str
    created_at: float


# --------------------------------------------------------------------------- #
# In-memory backend (fallback)
# --------------------------------------------------------------------------- #
class _MemoryStore:
    """Thread-safe TTL store used when Redis is not configured."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, tuple[str, float, Any]] = {}  # key -> (kind, expires_at, value)

    def _purge(self) -> None:
        now = time.time()
        for key, (_, exp, _) in list(self._data.items()):
            if exp <= now:
                self._data.pop(key, None)

    def set(self, key: str, value: Any, ttl: int, kind: str) -> None:
        with self._lock:
            self._purge()
            self._data[key] = (kind, time.time() + ttl, value)

    def pop(self, key: str, kind: str) -> Any | None:
        with self._lock:
            self._purge()
            entry = self._data.pop(key, None)
            if entry is None:
                return None
            k, exp, value = entry
            if k != kind or exp <= time.time():
                return None
            return value

    def peek(self, key: str, kind: str) -> Any | None:
        with self._lock:
            self._purge()
            entry = self._data.get(key)
            if entry is None:
                return None
            k, exp, value = entry
            if k != kind or exp <= time.time():
                return None
            return value


# --------------------------------------------------------------------------- #
# Unified token store
# --------------------------------------------------------------------------- #
class TokenStore:
    """Facade over Redis or the in-memory backend."""

    _PREFIX = "ghost:captcha:"

    def __init__(self) -> None:
        self._redis = None
        if REDIS_URL:
            try:
                import redis  # type: ignore

                self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
                self._redis.ping()
                print(f"[ghost-captcha] token store: Redis connected ({REDIS_URL})")
            except Exception as exc:  # pragma: no cover - environment dependent
                print(f"[ghost-captcha] token store: Redis unavailable ({exc}), falling back to memory")
                self._redis = None

        if self._redis is None:
            print("[ghost-captcha] token store: in-memory (single-instance only)")
        self._mem = _MemoryStore()

    # -- challenges ------------------------------------------------------- #
    def put_challenge(self, challenge_id: str, challenge: Challenge) -> None:
        payload = json.dumps(
            {
                "code": challenge.code,
                "origin": challenge.origin,
                "created_at": challenge.created_at,
            }
        )
        if self._redis:
            self._redis.setex(self._PREFIX + "c:" + challenge_id, CHALLENGE_TTL, payload)
        else:
            self._mem.set(challenge_id, payload, CHALLENGE_TTL, kind="challenge")

    def peek_challenge(self, challenge_id: str) -> Challenge | None:
        """Return the challenge without consuming it (used to stream the video)."""
        raw = (
            self._redis.get(self._PREFIX + "c:" + challenge_id)
            if self._redis
            else self._mem.peek(challenge_id, "challenge")
        )
        if not raw:
            return None
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return Challenge(
            code=d["code"],
            origin=d.get("origin", ""),
            created_at=d.get("created_at", time.time()),
        )

    def pop_challenge(self, challenge_id: str) -> Challenge | None:
        # Use GET+DELETE instead of GETDEL so older Redis versions (<6.2) work.
        if self._redis:
            key = self._PREFIX + "c:" + challenge_id
            pipe = self._redis.pipeline()
            pipe.get(key)
            pipe.delete(key)
            raw, _ = pipe.execute()
        else:
            raw = self._mem.pop(challenge_id, "challenge")
        if not raw:
            return None
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return Challenge(
            code=d["code"],
            origin=d.get("origin", ""),
            created_at=d.get("created_at", time.time()),
        )

    # -- tokens ----------------------------------------------------------- #
    def put_token(self, token: str, record: Token) -> None:
        payload = json.dumps(
            {
                "origin": record.origin,
                "created_at": record.created_at,
            }
        )
        if self._redis:
            self._redis.setex(self._PREFIX + "t:" + token, TOKEN_TTL, payload)
        else:
            self._mem.set(token, payload, TOKEN_TTL, kind="token")

    def pop_token(self, token: str) -> Token | None:
        # Use GET+DELETE instead of GETDEL so older Redis versions (<6.2) work.
        if self._redis:
            key = self._PREFIX + "t:" + token
            pipe = self._redis.pipeline()
            pipe.get(key)
            pipe.delete(key)
            raw, _ = pipe.execute()
        else:
            raw = self._mem.pop(token, "token")
        if not raw:
            return None
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return Token(
            origin=d.get("origin", ""),
            created_at=d.get("created_at", time.time()),
        )


# Module-level singleton used by app.py.
store = TokenStore()
