from __future__ import annotations

import math
import threading
import time

from .cache import cache
from .config import get_settings


class ModelRateLimiter:
    """Coordinate a conservative fixed-window RPM/TPM gate through Redis."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local_window = -1
        self._local_requests = 0
        self._local_tokens = 0

    @staticmethod
    def _window(now: float) -> tuple[int, int]:
        minute = int(now // 60)
        wait_seconds = max(1, math.ceil(60 - (now % 60)))
        return minute, wait_seconds

    def acquire(self, *, model: str, token_cost: int) -> int:
        """Return zero when admitted, otherwise seconds until the next window."""

        settings = get_settings()
        rpm = settings.cleaning_realtime_rpm
        tpm = settings.cleaning_realtime_tpm
        token_cost = max(1, int(token_cost))
        if token_cost > tpm:
            raise ValueError("单次模型请求的预计token超过实时通道TPM上限")
        now = time.time()
        minute, local_wait = self._window(now)
        safe_model = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in model
        )[:80]
        request_key = f"ai:model-rate:{safe_model}:{minute}:requests"
        token_key = f"ai:model-rate:{safe_model}:{minute}:tokens"
        client = cache.client
        if client:
            script = (
                "local r=tonumber(redis.call('get',KEYS[1]) or '0');"
                "local t=tonumber(redis.call('get',KEYS[2]) or '0');"
                "if r+1>tonumber(ARGV[1]) or t+tonumber(ARGV[2])>tonumber(ARGV[3]) then "
                "local ttl=redis.call('ttl',KEYS[1]);"
                "if ttl<1 then ttl=redis.call('ttl',KEYS[2]); end;"
                "if ttl<1 then ttl=1; end; return ttl; end;"
                "redis.call('incr',KEYS[1]);"
                "redis.call('incrby',KEYS[2],ARGV[2]);"
                "redis.call('expire',KEYS[1],70);"
                "redis.call('expire',KEYS[2],70); return 0;"
            )
            try:
                return int(
                    client.eval(
                        script,
                        2,
                        request_key,
                        token_key,
                        rpm,
                        token_cost,
                        tpm,
                    )
                    or 0
                )
            except Exception as exc:
                if settings.app_env == "production":
                    raise RuntimeError("实时模型限流依赖的Redis不可用") from exc
        with self._lock:
            if self._local_window != minute:
                self._local_window = minute
                self._local_requests = 0
                self._local_tokens = 0
            if self._local_requests + 1 > rpm or self._local_tokens + token_cost > tpm:
                return local_wait
            self._local_requests += 1
            self._local_tokens += token_cost
            return 0


model_rate_limiter = ModelRateLimiter()


__all__ = ["ModelRateLimiter", "model_rate_limiter"]
