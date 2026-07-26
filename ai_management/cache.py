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
        renewal_stop = threading.Event()
        renewal_thread: threading.Thread | None = None
        production = get_settings().app_env == "production"
        use_memory = not bool(self.client)
        try:
            if self.client:
                try:
                    acquired = bool(self.client.set(key, token, nx=True, ex=ttl))
                    redis_acquired = acquired
                except Exception:
                    use_memory = not production
            if use_memory and not production:
                with self._memory_lock:
                    existing = self._memory.get(key)
                    if not existing or (existing[0] and existing[0] <= time.time()):
                        self._memory[key] = (time.time() + ttl, token)
                        acquired = True
            if redis_acquired:
                def renew() -> None:
                    interval = max(1.0, ttl / 3)
                    while not renewal_stop.wait(interval):
                        try:
                            renewed = self.client.eval(
                                "if redis.call('get', KEYS[1]) == ARGV[1] "
                                "then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
                                1,
                                key,
                                token,
                                ttl,
                            )
                            if not renewed:
                                return
                        except Exception:
                            return

                renewal_thread = threading.Thread(target=renew, daemon=True)
                renewal_thread.start()
            yield acquired
        finally:
            renewal_stop.set()
            if renewal_thread is not None:
                renewal_thread.join(timeout=1)
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
        dirty_key = "ai:click-dirty"
        dirty_member = f"{day}|{course_id}"
        ttl = 60 * 60 * 24 * get_settings().click_counter_retention_days
        try:
            if self.client:
                pipe = self.client.pipeline()
                pipe.incr(count_key)
                pipe.sadd(users_key, user_hash)
                pipe.sadd(dirty_key, dirty_member)
                pipe.expire(count_key, ttl)
                pipe.expire(users_key, ttl)
                pipe.execute()
                return
        except Exception:
            pass
        with self._memory_lock:
            count = self._memory.get(count_key, (None, 0))[1]
            users = self._memory.get(users_key, (None, set()))[1]
            self._memory[count_key] = (None, int(count) + 1)
            self._memory[users_key] = (None, set(users) | {user_hash})
            dirty = self._memory.get(dirty_key, (None, set()))[1]
            self._memory[dirty_key] = (None, set(dirty) | {dirty_member})

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

    def dirty_click_counters(self) -> list[tuple[str, str]]:
        dirty_key = "ai:click-dirty"
        values: set[str]
        try:
            if self.client:
                values = {str(item) for item in self.client.smembers(dirty_key)}
            else:
                values = set()
        except Exception:
            values = set()
        if not values:
            with self._memory_lock:
                values = set(self._memory.get(dirty_key, (None, set()))[1])
        result: list[tuple[str, str]] = []
        for value in values:
            day, separator, course_id = value.partition("|")
            if separator and day and course_id:
                result.append((day, course_id))
        return sorted(result)

    def mark_click_counter_flushed(self, day: str, course_id: str) -> None:
        dirty_key = "ai:click-dirty"
        member = f"{day}|{course_id}"
        try:
            if self.client:
                self.client.srem(dirty_key, member)
        except Exception:
            pass
        with self._memory_lock:
            dirty = set(self._memory.get(dirty_key, (None, set()))[1])
            dirty.discard(member)
            self._memory[dirty_key] = (None, dirty)


cache = Cache()
