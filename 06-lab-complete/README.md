# Lab 12 — Complete Production Agent

Part 6 final project: build một AI agent production-ready, stateless, có auth, rate limiting, cost guard và cloud deployment config.

## Checklist Part 6

- [x] Dockerfile multi-stage, non-root
- [x] docker-compose stack: nginx + agent + redis
- [x] `/health` và `/ready`
- [x] API key authentication
- [x] Rate limit 10 req/min/user (Redis sliding window)
- [x] Cost guard 10 USD/tháng/user (Redis)
- [x] Conversation history lưu Redis (stateless)
- [x] Structured JSON logging
- [x] Graceful shutdown (SIGTERM)
- [x] Config từ environment variables
- [x] Railway/Render deployment config

## Cấu trúc

```
06-lab-complete/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── auth.py
│   ├── rate_limiter.py
│   └── cost_guard.py
├── nginx/
│   └── nginx.conf
├── Dockerfile
├── docker-compose.yml
├── railway.toml
├── render.yaml
├── .env.example
├── .dockerignore
└── check_production_ready.py
```

## Chạy local

```bash
# 1) Tạo file env
cp .env.example .env

# 2) Chạy stack (nginx + agent + redis)
docker compose up --build

# 3) Test health
curl http://localhost/health

# 4) Test ask endpoint
curl -X POST http://localhost/ask \
     -H "X-API-Key: dev-key-change-me-in-production" \
     -H "Content-Type: application/json" \
     -d '{"user_id":"student-01","question":"What is deployment?"}'

# 5) Test conversation history
curl -X GET http://localhost/history/student-01 \
     -H "X-API-Key: dev-key-change-me-in-production"
```

## Test scale/load balancing

```bash
docker compose up --build --scale agent=3 -d

for i in {1..12}; do
     curl -X POST http://localhost/ask \
          -H "X-API-Key: dev-key-change-me-in-production" \
          -H "Content-Type: application/json" \
          -d '{"user_id":"ratelimit-user","question":"test"}'
done
# Expect 429 after hitting limit
```

## Deploy Railway

```bash
npm i -g @railway/cli
railway login
railway init

railway variables set AGENT_API_KEY=your-secret-key
railway variables set JWT_SECRET=your-jwt-secret
railway variables set REDIS_URL=redis://<redis-host>:6379/0
railway variables set RATE_LIMIT_PER_MINUTE=10
railway variables set MONTHLY_BUDGET_USD=10.0

railway up
railway domain
```

## Deploy Render

1. Push repo lên GitHub.
2. Render Dashboard -> New -> Blueprint.
3. Render đọc `render.yaml`.
4. Set secret envs: `OPENAI_API_KEY` (optional), `AGENT_API_KEY`, `JWT_SECRET`.
5. Deploy và lấy public URL.

## Production readiness check

```bash
python check_production_ready.py
```

Script sẽ check file bắt buộc, API endpoints, bảo mật cơ bản, và cấu hình Docker.
