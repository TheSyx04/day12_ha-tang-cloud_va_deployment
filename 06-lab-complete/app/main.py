"""Production AI Agent — Part 6 complete implementation."""
import json
import logging
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import CostGuard
from app.rate_limiter import RateLimiter
from utils.mock_llm import ask as llm_ask

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0
_error_count = 0

redis_client: redis.Redis | None = None
rate_limiter: RateLimiter | None = None
cost_guard: CostGuard | None = None


class AskRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    question: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    user_id: str
    question: str
    answer: str
    model: str
    history_messages: int
    timestamp: str


def _estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1000) * 0.00015 + (output_tokens / 1000) * 0.0006


def _history_key(user_id: str) -> str:
    return f"conversation:{user_id}"


def _get_clients() -> tuple[redis.Redis, RateLimiter, CostGuard]:
    if not redis_client or not rate_limiter or not cost_guard:
        raise HTTPException(status_code=503, detail="Service dependencies not ready")
    return redis_client, rate_limiter, cost_guard


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready, redis_client, rate_limiter, cost_guard
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "redis_url": settings.redis_url,
    }))

    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        redis_client.ping()
        rate_limiter = RateLimiter(redis_client)
        cost_guard = CostGuard(redis_client)
        _is_ready = True
        logger.info(json.dumps({"event": "ready"}))
    except Exception as exc:
        _is_ready = False
        logger.error(json.dumps({"event": "startup_failed", "error": str(exc)}))

    yield

    _is_ready = False
    if redis_client:
        redis_client.close()
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count, _error_count
    start = time.time()
    _request_count += 1
    try:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if "server" in response.headers:
            del response.headers["server"]

        logger.info(json.dumps({
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": round((time.time() - start) * 1000, 1),
        }))
        return response
    except Exception:
        _error_count += 1
        raise


@app.get("/")
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "endpoints": {
            "ask": "POST /ask (X-API-Key required)",
            "history": "GET /history/{user_id} (X-API-Key required)",
            "health": "GET /health",
            "ready": "GET /ready",
        },
    }


@app.post("/ask", response_model=AskResponse)
def ask_agent(
    body: AskRequest,
    request: Request,
    _api_key: str = Depends(verify_api_key),
):
    client, limiter, budget_guard = _get_clients()

    limiter_state = limiter.check(body.user_id)

    input_tokens = max(len(body.question.split()) * 2, 1)
    estimated_cost = _estimate_cost_usd(input_tokens, 64)
    budget_state = budget_guard.check_budget(body.user_id, estimated_cost)

    history_key = _history_key(body.user_id)
    history_raw = client.lrange(history_key, 0, -1)
    history = [json.loads(item) for item in history_raw]

    context_lines = []
    for msg in history[-6:]:
        context_lines.append(f"{msg['role']}: {msg['content']}")
    context_text = "\n".join(context_lines)
    prompt = body.question if not context_text else f"Conversation:\n{context_text}\n\nUser: {body.question}"

    answer = llm_ask(prompt)
    output_tokens = max(len(answer.split()) * 2, 1)
    actual_cost = _estimate_cost_usd(input_tokens, output_tokens)
    new_total = budget_guard.record_cost(body.user_id, actual_cost)

    client.rpush(
        history_key,
        json.dumps({"role": "user", "content": body.question, "ts": datetime.now(timezone.utc).isoformat()}),
        json.dumps({"role": "assistant", "content": answer, "ts": datetime.now(timezone.utc).isoformat()}),
    )
    client.expire(history_key, settings.conversation_ttl_seconds)

    logger.info(json.dumps({
        "event": "agent_call",
        "user_id": body.user_id,
        "client": str(request.client.host) if request.client else "unknown",
        "question_len": len(body.question),
        "remaining_requests": limiter_state["remaining"],
        "monthly_budget_limit": budget_state["limit"],
        "monthly_spending": round(new_total, 4),
    }))

    return AskResponse(
        user_id=body.user_id,
        question=body.question,
        answer=answer,
        model=settings.llm_model,
        history_messages=len(history) + 2,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/history/{user_id}")
def get_history(user_id: str, _api_key: str = Depends(verify_api_key)):
    client, _, _ = _get_clients()
    items = [json.loads(item) for item in client.lrange(_history_key(user_id), 0, -1)]
    return {"user_id": user_id, "messages": items, "count": len(items)}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def ready():
    if not _is_ready or not redis_client:
        raise HTTPException(status_code=503, detail="Not ready")

    try:
        redis_client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis not ready: {exc}") from exc

    return {"ready": True}


@app.get("/metrics")
def metrics(_api_key: str = Depends(verify_api_key)):
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
    }


def _handle_signal(signum, _frame):
    logger.info(json.dumps({"event": "signal", "signal": signum, "action": "graceful_shutdown"}))


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


if __name__ == "__main__":
    logger.info(json.dumps({"event": "boot", "app": settings.app_name, "port": settings.port}))
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )
