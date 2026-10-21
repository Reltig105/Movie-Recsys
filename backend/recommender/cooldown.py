"""Кулдаун показов: какие фильмы пользователь уже видел за последние 24 часа.

Основное хранилище — Redis (sorted set ``rexter:shown:<user_id>``, score =
unix-время показа). Если Redis не поднят или недоступен, модуль прозрачно
переключается на словарь в памяти процесса: для одного uvicorn-воркера этого
достаточно, а падение кэша не должно ломать выдачу рекомендаций.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: Сколько живёт отметка о показе (сутки).
DEFAULT_TTL = 24 * 60 * 60

#: Предохранитель для in-memory режима: не копить отметки бесконечно.
MAX_MEMORY_USERS = 50_000


class ShowHistory:
    """Учёт показов с TTL и двумя бэкендами (Redis → память)."""

    def __init__(self, redis_url: str | None, ttl: int = DEFAULT_TTL) -> None:
        self._redis_url = redis_url
        self._ttl = max(60, int(ttl))
        self._redis = None
        self._memory: dict[int, dict[int, float]] = {}

    @property
    def backend(self) -> str:
        """``redis`` или ``memory`` — для логов и health-диагностики."""
        return "redis" if self._redis is not None else "memory"

    async def connect(self) -> None:
        """Подключиться к Redis; при неудаче остаться на памяти."""
        if not self._redis_url:
            logger.info("Кулдаун показов: Redis не задан, используется память")
            return
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(self._redis_url, decode_responses=True)
            await client.ping()
        except Exception as exc:  # noqa: BLE001 — любой сбой Redis не фатален
            logger.warning(
                "Кулдаун показов: Redis недоступен (%s) — память процесса", exc
            )
            return
        self._redis = client
        logger.info("Кулдаун показов: Redis %s", self._redis_url)

    async def close(self) -> None:
        """Закрыть соединение с Redis (вызывается на shutdown)."""
        await self._drop_redis()

    async def _drop_redis(self) -> None:
        """Отпустить сломанный клиент, чтобы не копить соединения."""
        client = self._redis
        self._redis = None
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 — клиент уже нерабочий
                pass

    def _key(self, user_id: int) -> str:
        return f"rexter:shown:{user_id}"

    def _memory_recent(self, user_id: int) -> set[int]:
        bucket = self._memory.get(user_id)
        if not bucket:
            return set()
        cutoff = time.time() - self._ttl
        for movie_id, shown_at in list(bucket.items()):
            if shown_at < cutoff:
                del bucket[movie_id]
        if not bucket:
            self._memory.pop(user_id, None)
            return set()
        return set(bucket)

    async def recent(self, user_id: int) -> set[int]:
        """Id фильмов, показанных пользователю за последние ``ttl`` секунд."""
        now = time.time()
        if self._redis is not None:
            try:
                key = self._key(user_id)
                await self._redis.zremrangebyscore(key, 0, now - self._ttl)
                members = await self._redis.zrangebyscore(key, now - self._ttl, now)
                return {int(member) for member in members}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Кулдаун: Redis не ответил (%s) — память", exc)
                await self._drop_redis()
        return self._memory_recent(user_id)

    async def record(self, user_id: int, movie_ids: list[int]) -> None:
        """Запомнить показ фильмов (перезапись времени для повторов)."""
        if not movie_ids:
            return
        now = time.time()
        if self._redis is not None:
            try:
                key = self._key(user_id)
                await self._redis.zadd(key, {str(movie_id): now for movie_id in movie_ids})
                await self._redis.expire(key, self._ttl)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Кулдаун: Redis не ответил (%s) — память", exc)
                await self._drop_redis()

        if user_id not in self._memory and len(self._memory) >= MAX_MEMORY_USERS:
            self._memory.clear()
            logger.warning("Кулдаун: словарь в памяти переполнен — очищен")
        bucket = self._memory.setdefault(user_id, {})
        for movie_id in movie_ids:
            bucket[int(movie_id)] = now
