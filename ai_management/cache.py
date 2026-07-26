from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import get_settings

try:
    import redis
except ImportError:  # pragma: no cover - dependency is required in deployment
    redis = None


class Cache:
    def __init__(self) -> None:
        self._client = None
        self._memory: dict[str, tuple[float | None, Any]] = {}
        self._memory_lock = threading.RLock()

    @property
    def client(self):
        if self._client is None and redis is not None:
            self._client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        return self._client

    def ping(self) -> bool:
        try:
            return bool(self.client and self.client.ping())
        except Exception:
            return False

    def get_json(self, key: str) -> Any | None:
        try:
            if self.client:
                value = self.client.get(key)
                return json.loads(value) if value else None
        except Exception:
            pass
        with self._memory_lock:
            item = self._memory.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at and expires_at <= time.time():
                self._memory.pop(key, None)
                return None
            return value

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        try:
            if self.client:
                self.client.set(key, encoded, ex=ttl)
                return
        except Exception:
            pass
        with self._memory_lock:
            self._memory[key] = (time.time() + ttl if ttl else None, value)

    def delete(self, key: str) -> None:
        try:
            if self.client:
                self.client.delete(key)
        except Exception:
            pass
        with self._memory_lock:
            self._memory.pop(key, None)

    def atomic_json_swap(self, key: str, value: Any) -> None:
        temporary = f"{key}:tmp:{uuid.uuid4().hex}"
        encoded = json.dumps(value, ensure_ascii=False)
        try:
            if self.client:
                pipe = self.client.pipeline()
                pipe.set(temporary, encoded)
                pipe.rename(temporary, key)
                pipe.execute()
                return
        except Exception:
            pass
        self.set_json(key, value)

    @contextmanager
    def lock(self, key: str, ttl: int = 300) -> Iterator[bool]:
        token = uuid.uuid4().hex
        acquired = False
        redis_acquired = False
        use_memory = not bool(self.client)
        try:
            if self.client:
                try:
                    acquired = bool(self.client.set(key, token, nx=True, ex=ttl))
                    redis_acquired = acquired
                except Exception:
                    use_memory = True
            if use_memory:
                with self._memory_lock:
                    existing = self._memory.get(key)
                    if not existing or (existing[0] and existing[0] <= time.time()):
                        self._memory[key] = (time.time() + ttl, token)
                        acquired = True
            yield acquired
        finally:
            if acquired:
                try:
                    if redis_acquired and self.client:
                        self.client.eval(
                            "if redis.call('get', KEYS[1]) == ARGV[1] "
                            "then return redis.call('del', KEYS[1]) else return 0 end",
                            1,
                            key,
                            token,
                        )
                    else:
                        with self._memory_lock:
                            if self._memory.get(key, (None, None))[1] == token:
                                self._memory.pop(key, None)
                except Exception:
                    pass

    def increment_click(self, course_id: str, day: str, user_hash: str) -> None:
        count_key = f"ai:clicks:{day}:{course_id}"
        users_key = f"ai:click-users:{day}:{course_id}"
        try:
            if self.client:
                pipe = self.client.pipeline()
                pipe.incr(count_key)
                pipe.sadd(users_key, user_hash)
                pipe.expire(count_key, 60 * 60 * 24 * 14)
                pipe.expire(users_key, 60 * 60 * 24 * 14)
                pipe.execute()
                return
        except Exception:
            pass
        with self._memory_lock:
            count = self._memory.get(count_key, (None, 0))[1]
            users = self._memory.get(users_key, (None, set()))[1]
            self._memory[count_key] = (None, int(count) + 1)
            self._memory[users_key] = (None, set(users) | {user_hash})

    def click_counts(self, course_id: str, day: str) -> tuple[int, int]:
        count_key = f"ai:clicks:{day}:{course_id}"
        users_key = f"ai:click-users:{day}:{course_id}"
        try:
            if self.client:
                count, users = self.client.mget(count_key), self.client.scard(users_key)
                return int(count[0] or 0), int(users or 0)
        except Exception:
            pass
        with self._memory_lock:
            return (
                int(self._memory.get(count_key, (None, 0))[1]),
                len(self._memory.get(users_key, (None, set()))[1]),
            )


cache = Cache()
