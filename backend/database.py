"""Асинхронный слой доступа к БД Rexter — PostgreSQL + asyncpg.

Архитектурное правило: **только PostgreSQL**. Ни SQLite, ни иных fallback-
драйверов нет и быть не должно — объём данных (MovieLens 32M: десятки
миллионов оценок) и требования Production-уровня допускают только PostgreSQL.

Поэтому строка подключения обязана начинаться с ``postgresql+asyncpg://``,
иначе модуль падает при импорте (fail fast, без «тихого» переключения на SQLite).

Строка подключения берётся из ``REXTER_DATABASE_URL`` (или ``DATABASE_URL``),
по умолчанию — локальный контейнер из ``docker-compose.yml``::

    postgresql+asyncpg://postgres:postgres@localhost:5432/rexter_db

Пример использования в эндпоинте FastAPI::

    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession
    from backend.database import get_session

    async def endpoint(session: AsyncSession = Depends(get_session)): ...
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/rexter_db"
_REQUIRED_SCHEME = "postgresql+asyncpg://"

DATABASE_URL: str = (
    os.environ.get("REXTER_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or DEFAULT_DATABASE_URL
)

if not DATABASE_URL.startswith(_REQUIRED_SCHEME):
    raise RuntimeError(
        "Rexter работает исключительно на PostgreSQL (драйвер asyncpg). "
        f"Ожидается URL вида '{_REQUIRED_SCHEME}...', получено: {DATABASE_URL!r}. "
        "SQLite и другие драйверы не поддерживаются — поднимите PostgreSQL "
        "через `docker compose up -d`."
    )


class Base(DeclarativeBase):
    """Базовый декларативный класс для всех ORM-моделей."""


# Асинхронный пул соединений asyncpg под FastAPI.
#   * ``pool_pre_ping`` — отсеивает «мёртвые» соединения после пауз
#     (например, когда контейнер с Postgres перезапускался);
#   * ``pool_recycle`` — переоткрывает соединения старше 30 минут;
#   * ``pool_size`` / ``max_overflow`` — потолок одновременных соединений.
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
)

#: Фабрика асинхронных сессий (аналог ``sessionmaker`` для async).
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: асинхронная сессия с закрытием после запроса."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Создать все таблицы, объявленные на :class:`Base` (если их ещё нет)."""
    from . import models  # noqa: F401  (регистрирует модели в Base.metadata)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # ``create_all`` не меняет существующие таблицы. Колонка страны
        # появилась вместе с фильтрами ленты, поэтому старые базы догоняем
        # идемпотентным ALTER, а не требуем отдельный инструмент миграций.
        await connection.execute(
            text("ALTER TABLE movies ADD COLUMN IF NOT EXISTS country varchar(120)")
        )


async def dispose() -> None:
    """Закрыть пул соединений (вызывать на shutdown приложения)."""
    await engine.dispose()
