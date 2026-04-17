"""Redis-backed monthly budget guard."""
from datetime import datetime

import redis
from fastapi import HTTPException

from app.config import settings


class CostGuard:
    def __init__(self, redis_client: redis.Redis):
        self.redis_client = redis_client

    @staticmethod
    def _month_key() -> str:
        return datetime.utcnow().strftime("%Y-%m")

    def _budget_key(self, user_id: str) -> str:
        return f"budget:{user_id}:{self._month_key()}"

    def check_budget(self, user_id: str, estimated_cost_usd: float) -> dict[str, float]:
        key = self._budget_key(user_id)
        current = float(self.redis_client.get(key) or 0.0)
        projected = current + estimated_cost_usd

        if projected > settings.monthly_budget_usd:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly budget exceeded. Current=${current:.4f}, "
                    f"Limit=${settings.monthly_budget_usd:.2f}"
                ),
            )

        return {
            "current": current,
            "projected": projected,
            "limit": settings.monthly_budget_usd,
        }

    def record_cost(self, user_id: str, actual_cost_usd: float) -> float:
        key = self._budget_key(user_id)
        total = self.redis_client.incrbyfloat(key, actual_cost_usd)
        # Keep long enough to cover month boundaries and late reads.
        self.redis_client.expire(key, 40 * 24 * 3600)
        return float(total)
