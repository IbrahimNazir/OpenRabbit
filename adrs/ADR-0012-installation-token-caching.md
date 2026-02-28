# ADR-0012: GitHub Installation Token Caching in Redis

| Field | Value |
|-------|-------|
| **ID** | ADR-0012 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 2 — GitHub Client & Diff Fetching |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | caching, github, authentication, redis, performance |

---

## Context and Problem Statement

Every GitHub API call requires an Installation Access Token scoped to the specific GitHub App installation (organization or user). These tokens are obtained by: generating a short-lived App JWT (valid 10 minutes), exchanging the JWT for an Installation Token at `POST /app/installations/{id}/access_tokens` (valid 60 minutes).

Without caching, each of the ~10 GitHub API calls per PR review would trigger a new token exchange — 10 × 200ms = 2 extra seconds per review just for token overhead, plus burning 10 of the 5,000 rate-limited token exchange requests per hour.

---

## Decision

**Cache installation tokens in Redis with a TTL of 55 minutes (5 minutes short of the 60-minute expiry).**

The 5-minute buffer ensures we never use a token that is about to expire or has just expired due to clock skew between our server and GitHub's servers.

### Cache Key Structure

```
github:token:{installation_id}
```

Examples:
```
github:token:12345678    → "ghs_abc123def456..."  (TTL: 3180s remaining)
github:token:87654321    → "ghs_xyz789..."         (TTL: 1200s remaining)
```

### Implementation

```python
# app/core/github_client.py
class GitHubClient:
    CACHE_KEY_PREFIX = "github:token:"
    TOKEN_TTL_SECONDS = 55 * 60  # 55 minutes (token valid for 60, buffer 5)
    
    def __init__(self, installation_id: int, redis: Redis):
        self.installation_id = installation_id
        self.redis = redis
        self._token: str | None = None
    
    async def get_access_token(self) -> str:
        """Get a valid installation token, using cache when available."""
        cache_key = f"{self.CACHE_KEY_PREFIX}{self.installation_id}"
        
        # Try cache first
        cached = await self.redis.get(cache_key)
        if cached:
            return cached.decode("utf-8")
        
        # Cache miss — generate fresh token
        token = await self._fetch_fresh_token()
        
        # Cache with 55-minute TTL
        await self.redis.setex(cache_key, self.TOKEN_TTL_SECONDS, token)
        
        return token
    
    async def _fetch_fresh_token(self) -> str:
        """Exchange App JWT for installation access token."""
        app_jwt = self._generate_app_jwt()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{self.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            
            if response.status_code == 401:
                raise GitHubAuthError("App JWT is invalid — check GITHUB_APP_PRIVATE_KEY_PATH")
            if response.status_code == 404:
                raise GitHubInstallationNotFoundError(
                    f"Installation {self.installation_id} not found — may have been uninstalled"
                )
            response.raise_for_status()
            
            data = response.json()
            return data["token"]
    
    async def _invalidate_token(self) -> None:
        """Force token refresh on next request (e.g., after 403 from GitHub API)."""
        cache_key = f"{self.CACHE_KEY_PREFIX}{self.installation_id}"
        await self.redis.delete(cache_key)
    
    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make an authenticated GitHub API request with automatic token refresh."""
        token = await self.get_access_token()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method, url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    **kwargs.pop("headers", {}),
                },
                **kwargs
            )
            
            if response.status_code == 403:
                # Token may be revoked — invalidate cache and retry once
                remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
                if remaining == "0":
                    raise GitHubRateLimitError(
                        f"Rate limit exceeded. Resets at: {response.headers.get('X-RateLimit-Reset')}"
                    )
                # Otherwise, token was likely revoked — invalidate and retry
                await self._invalidate_token()
                token = await self.get_access_token()
                response = await client.request(method, url, **kwargs)
            
            return response
```

### Rate Limit Monitoring

```python
async def check_rate_limit(self, response: httpx.Response) -> None:
    """Log rate limit status from every GitHub API response."""
    remaining = int(response.headers.get("X-RateLimit-Remaining", -1))
    limit = int(response.headers.get("X-RateLimit-Limit", -1))
    reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))
    
    if remaining < 100:
        logger.warning(
            "GitHub rate limit low",
            installation_id=self.installation_id,
            remaining=remaining,
            limit=limit,
            resets_in_seconds=reset_ts - int(time.time()),
        )
    
    # Store in Redis for admin monitoring
    await self.redis.setex(
        f"github:rate_limit:{self.installation_id}",
        300,  # 5-minute TTL
        json.dumps({"remaining": remaining, "limit": limit, "reset": reset_ts})
    )
```

---

## Consequences

### Positive
- ~10 GitHub API calls per review → 1 token exchange per 55 minutes per installation (instead of 10 per review)
- Token cache survives across Celery worker restarts (Redis persistence)
- If a token is revoked or expires early (e.g., installation permissions changed), the 403 retry logic handles it gracefully — invalidates cache and fetches a fresh token
- Rate limit monitoring built into every API call — proactive alerting before limits are hit

### Negative
- If Redis is down, every API call must exchange a token. The system continues functioning (graceful degradation) but with higher latency and rate limit consumption. **Mitigation:** Redis health check in Docker Compose; Redis persistence ensures fast restart recovery

### Security Note

Installation tokens in Redis are encrypted in transit (TLS to Redis in production) but stored as plaintext values. If the Redis instance is compromised, tokens are exposed. **Mitigation:** Redis must not be exposed on public network interfaces; Docker network isolation is enforced in Compose; tokens expire within 55 minutes even if stolen.
