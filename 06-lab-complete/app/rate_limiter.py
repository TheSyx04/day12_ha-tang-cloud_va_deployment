"""Redis sliding-window rate limiting."""
import time
from uuid import uuid4

import redis
from fastapi import HTTPException

from app.config import settings


class RateLimiter:
    def __init__(self, redis_client: redis.Redis):
        self.redis_client = redis_client

    def check(self, user_id: str) -> dict[str, int]:
        """Limit requests per user in a rolling 60-second window."""
        now = time.time()
        window_start = now - 60
        key = f"rate:{user_id}"

        pipe = self.redis_client.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        removed, current_count = pipe.execute()

        if current_count >= settings.rate_limit_per_minute:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded: {settings.rate_limit_per_minute} "
                    "requests/minute"
                ),
                headers={"Retry-After": "60"},
            )

        member = f"{int(now * 1000)}:{uuid4().hex}"
        pipe = self.redis_client.pipeline()
        pipe.zadd(key, {member: now})
        pipe.expire(key, 61)
        pipe.execute()

        remaining = max(settings.rate_limit_per_minute - (current_count + 1), 0)
        return {"remaining": remaining, "limit": settings.rate_limit_per_minute}
