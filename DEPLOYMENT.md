# Deployment Information

## Public URL
https://terrific-eagerness-production.up.railway.app/

## Platform
Railway

## Test Commands

### Health Check
```bash
curl https://terrific-eagerness-production.up.railway.app/health
# Expected: {"status":"ok", ...}
```

### API Test (with authentication)
```bash
curl -X POST https://terrific-eagerness-production.up.railway.app/ask \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test-user","question":"Hello"}'
```

## Environment Variables Set
- PORT
- REDIS_URL
- AGENT_API_KEY
- JWT_SECRET
- RATE_LIMIT_PER_MINUTE
- MONTHLY_BUDGET_USD
- LOG_LEVEL

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
