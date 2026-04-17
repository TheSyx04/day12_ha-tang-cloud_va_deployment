"""
Cost Guard — Budget protection

Exercise 4.4 yêu cầu:
- Mỗi user có budget $10/tháng
- Track spending trong Redis
- Tự reset theo tháng (key đổi theo YYYY-MM)

Module này ưu tiên Redis, nhưng có fallback in-memory để dễ chạy local demo.
"""
import os
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

try:
    import redis
except ImportError:  # pragma: no cover - optional in local demo
    redis = None

logger = logging.getLogger(__name__)


# Giá token (tham khảo, thay đổi theo model)
PRICE_PER_1K_INPUT_TOKENS = 0.00015   # GPT-4o-mini: $0.15/1M input
PRICE_PER_1K_OUTPUT_TOKENS = 0.0006   # GPT-4o-mini: $0.60/1M output


@dataclass
class UsageRecord:
    user_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    request_count: int = 0
    day: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))

    @property
    def total_cost_usd(self) -> float:
        input_cost = (self.input_tokens / 1000) * PRICE_PER_1K_INPUT_TOKENS
        output_cost = (self.output_tokens / 1000) * PRICE_PER_1K_OUTPUT_TOKENS
        return round(input_cost + output_cost, 6)


class CostGuard:
    def __init__(
        self,
        daily_budget_usd: float = 1.0,
        global_daily_budget_usd: float = 10.0,
        warn_at_pct: float = 0.8,              # Cảnh báo khi dùng 80%
    ):
        # Giữ lại các field cũ để tương thích endpoint /admin/stats
        self.daily_budget_usd = daily_budget_usd
        self.global_daily_budget_usd = global_daily_budget_usd
        self.warn_at_pct = warn_at_pct

        # Requirement của Exercise 4.4
        self.monthly_budget_usd = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

        self._records: dict[str, UsageRecord] = {}
        self._monthly_spending_fallback: dict[str, float] = {}
        self._global_today = time.strftime("%Y-%m-%d")
        self._global_cost = 0.0

        self._redis = None
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        if redis is not None:
            try:
                client = redis.from_url(redis_url, decode_responses=True)
                client.ping()
                self._redis = client
                logger.info("CostGuard using Redis: %s", redis_url)
            except Exception as exc:  # pragma: no cover - runtime environment dependent
                logger.warning("Redis unavailable, fallback to in-memory spending: %s", exc)

    def _month_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _spending_key(self, user_id: str) -> str:
        return f"budget:{user_id}:{self._month_key()}"

    def _get_monthly_spending(self, user_id: str) -> float:
        key = self._spending_key(user_id)
        if self._redis is not None:
            value = self._redis.get(key)
            return float(value or 0.0)
        return float(self._monthly_spending_fallback.get(key, 0.0))

    def _add_monthly_spending(self, user_id: str, amount_usd: float) -> None:
        if amount_usd <= 0:
            return

        key = self._spending_key(user_id)
        if self._redis is not None:
            self._redis.incrbyfloat(key, amount_usd)
            # TTL ~ 32 ngày, đủ để key tự dọn sau khi qua tháng
            self._redis.expire(key, 32 * 24 * 3600)
            return

        self._monthly_spending_fallback[key] = self._get_monthly_spending(user_id) + amount_usd

    def _get_record(self, user_id: str) -> UsageRecord:
        today = time.strftime("%Y-%m-%d")
        record = self._records.get(user_id)
        if not record or record.day != today:
            self._records[user_id] = UsageRecord(user_id=user_id, day=today)
        return self._records[user_id]

    def check_budget(self, user_id: str, estimated_cost: float = 0.0) -> bool:
        """
        Exercise 4.4 contract:
            Return True nếu còn budget, False nếu vượt.

        Budget được tính theo THÁNG / user.
        """
        used = self._get_monthly_spending(user_id)
        allowed = used + max(0.0, estimated_cost) <= self.monthly_budget_usd

        if used >= self.monthly_budget_usd * self.warn_at_pct:
            logger.warning(
                "User %s reached %.1f%% monthly budget",
                user_id,
                (used / self.monthly_budget_usd) * 100,
            )

        return allowed

    def record_usage(
        self, user_id: str, input_tokens: int, output_tokens: int
    ) -> UsageRecord:
        """Ghi nhận usage sau khi gọi LLM xong."""
        record = self._get_record(user_id)
        record.input_tokens += input_tokens
        record.output_tokens += output_tokens
        record.request_count += 1

        cost = (input_tokens / 1000 * PRICE_PER_1K_INPUT_TOKENS +
                output_tokens / 1000 * PRICE_PER_1K_OUTPUT_TOKENS)
        self._global_cost += cost
        self._add_monthly_spending(user_id, cost)

        logger.info(
            f"Usage: user={user_id} req={record.request_count} "
            f"cost=${record.total_cost_usd:.4f}/{self.daily_budget_usd}"
        )
        return record

    def get_usage(self, user_id: str) -> dict:
        record = self._get_record(user_id)
        monthly_used = self._get_monthly_spending(user_id)
        return {
            "user_id": user_id,
            "date": record.day,
            "requests": record.request_count,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cost_usd": record.total_cost_usd,
            "budget_usd": self.daily_budget_usd,
            "budget_remaining_usd": max(0, self.daily_budget_usd - record.total_cost_usd),
            "budget_used_pct": round(record.total_cost_usd / self.daily_budget_usd * 100, 1),
            "monthly_budget_usd": self.monthly_budget_usd,
            "monthly_used_usd": round(monthly_used, 6),
            "monthly_remaining_usd": round(max(0, self.monthly_budget_usd - monthly_used), 6),
        }


# Singleton
cost_guard = CostGuard(daily_budget_usd=1.0, global_daily_budget_usd=10.0)
