from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator

from .config import get_settings

try:
    import redis
except ImportError:  # pragma: no cover - dependency is required in deployment
    redis = None


@dataclass(frozen=True)
class LockState:
    acquired: bool
    available: bool

    def __bool__(self) -> bool:
        return self.acquired


class Cache:
    def __init__(self) -> None:
        self._client = None
        self._memory: dict[str, tuple[float | None, Any]] = {}
        self._memory_lock = threading.RLock()
        # A failed atomic swap may leave Redis with an older snapshot while
        # this process has a newer DB-derived memory copy.  Never let a later
        # Redis recovery silently resurrect that older value.
        self._pending_redis_sync: set[str] = set()

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
        with self._memory_lock:
            pending = key in self._pending_redis_sync
            memory_item = self._memory.get(key)
            if memory_item and memory_item[0] and memory_item[0] <= time.time():
                self._memory.pop(key, None)
                memory_item = None
        if pending and memory_item is not None:
            _, memory_value = memory_item
            temporary = f"{key}:tmp:{uuid.uuid4().hex}"
            encoded, _normalized = _encode_json_value(memory_value)
            try:
                if self.client:
                    pipe = self.client.pipeline()
                    pipe.set(temporary, encoded)
                    pipe.rename(temporary, key)
                    pipe.execute()
                    with self._memory_lock:
                        self._pending_redis_sync.discard(key)
                    return memory_value
            except Exception:
                return memory_value
            return memory_value
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
        encoded, normalized = _encode_json_value(value)
        try:
            if self.client:
                self.client.set(key, encoded, ex=ttl)
                return
        except Exception:
            pass
        with self._memory_lock:
            self._memory[key] = (time.time() + ttl if ttl else None, normalized)

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
        encoded, _normalized = _encode_json_value(value)
        try:
            if self.client:
                pipe = self.client.pipeline()
                pipe.set(temporary, encoded)
                pipe.rename(temporary, key)
                pipe.execute()
                return
        except Exception:
            pass
        # Do not retry through set_json here: the cache may have recovered
        # between calls and then accept an unrelated stale value first.  Keep
        # the known DB-derived snapshot and sync it atomically on recovery.
        with self._memory_lock:
            self._memory[key] = (None, _normalized)
            self._pending_redis_sync.add(key)

    @contextmanager
    def lock(self, key: str, ttl: int = 300) -> Iterator[LockState]:
        token = uuid.uuid4().hex
        acquired = False
        redis_acquired = False
        renewal_stop = threading.Event()
        renewal_thread: threading.Thread | None = None
        production = get_settings().app_env == "production"
        use_memory = not bool(self.client)
        available = bool(self.client) or not production
        try:
            if self.client:
                try:
                    acquired = bool(self.client.set(key, token, nx=True, ex=ttl))
                    redis_acquired = acquired
                except Exception:
                    available = False
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
            yield LockState(acquired=acquired, available=available)
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
        version_key = f"ai:click-version:{day}:{course_id}"
        dirty_member = f"{day}|{course_id}"
        ttl = 60 * 60 * 24 * get_settings().click_counter_retention_days
        try:
            if self.client:
                pipe = self.client.pipeline()
                pipe.incr(count_key)
                pipe.sadd(users_key, user_hash)
                pipe.sadd(dirty_key, dirty_member)
                pipe.incr(version_key)
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
            version = int(self._memory.get(version_key, (None, 0))[1]) + 1
            self._memory[version_key] = (None, version)
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

    def click_snapshot(self, course_id: str, day: str) -> tuple[int, int, int]:
        clicks, users = self.click_counts(course_id, day)
        key = f"ai:click-version:{day}:{course_id}"
        try:
            if self.client:
                return clicks, users, int(self.client.get(key) or 0)
        except Exception:
            pass
        with self._memory_lock:
            return clicks, users, int(self._memory.get(key, (None, 0))[1])

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

    def mark_click_counter_flushed(
        self, day: str, course_id: str, version: int | None = None
    ) -> None:
        dirty_key = "ai:click-dirty"
        member = f"{day}|{course_id}"
        try:
            if self.client:
                if version is None:
                    self.client.srem(dirty_key, member)
                else:
                    self.client.eval(
                        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('srem', KEYS[2], ARGV[2]) else return 0 end",
                        2,
                        f"ai:click-version:{day}:{course_id}",
                        dirty_key,
                        str(version),
                        member,
                    )
        except Exception:
            pass
        with self._memory_lock:
            dirty = set(self._memory.get(dirty_key, (None, set()))[1])
            current = int(self._memory.get(f"ai:click-version:{day}:{course_id}", (None, 0))[1])
            if version is None or current == version:
                dirty.discard(member)
            self._memory[dirty_key] = (None, dirty)


cache = Cache()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _encode_json_value(value: Any) -> tuple[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, default=_json_default)
    return encoded, json.loads(encoded)
