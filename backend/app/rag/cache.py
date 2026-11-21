"""Optional cache of verified excerpts. Retrieval and access checks always run first."""
import asyncio
import hashlib
import hmac
import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from .generation import SYSTEM_PROMPT, validate_line


class QuoteCache:
    def __init__(self, config, client=None):
        self.config = config
        self.client = client if client is not None else Redis.from_url(
            config.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)

    def key(self, actor_id, question, evidence):
        parts = ["verified-quotes-v1", actor_id, question, self.config.llm_base_url,
                 self.config.llm_model, self.config.llm_max_tokens, SYSTEM_PROMPT,
                 [(e.source_id, e.source["parent_id"], e.text) for e in evidence]]
        digest = hmac.new(self.config.auth_bridge_secret.encode(), json.dumps(parts).encode(),
                          hashlib.sha256).hexdigest()
        return "aihub:quotes:v1:" + digest

    async def get(self, key, evidence):
        try:
            async with asyncio.timeout(0.2):
                raw = await self.client.get(key)
            if not raw or len(raw) > 30_000:
                return None
            rows = json.loads(raw)
            if not isinstance(rows, list) or not 1 <= len(rows) <= 6:
                return None
            quotes = [validate_line(json.dumps(row), evidence) for row in rows]
            return quotes if all(quotes) else None
        except (RedisError, TimeoutError, ValueError):
            return None

    async def put(self, key, quotes):
        try:
            value = [{"source_id":q["source_id"],"quote":q["text"],"answer":q.get("answer", "")} for q in quotes]
            raw = json.dumps(value, ensure_ascii=False).encode()
            if len(raw) <= 30_000:
                async with asyncio.timeout(0.2):
                    await self.client.set(key, raw, ex=self.config.answer_cache_ttl)
        except (RedisError, TimeoutError):
            pass

    async def close(self):
        await self.client.aclose()
