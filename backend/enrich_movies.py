"""Массовое обогащение каталога фильмов метаданными TMDB — полностью async.

Читает официальный ``data/links.csv`` (``movieId`` → ``tmdbId``) и параллельно
подтягивает постеры, синопсисы, режиссёра и актёров, обновляя строки ``movies``
(их создаёт :mod:`backend.load_movielens`).

Архитектура:

* сеть — асинхронный клиент :mod:`aiohttp` в рамках одного event loop;
* параллелизм ограничен ``asyncio.Semaphore(40)`` (по требованию к нагрузке на
  TMDB; превышение лимита обрабатывается ретраями на HTTP 429);
* запись — пакетный ``asyncpg.Connection.executemany`` по батчам;
* никаких потоков (``threading``) и никакого SQLite.

Скрипт идемпотентен и возобновляем: по умолчанию обрабатываются только фильмы
без постера (``poster_url IS NULL``), а ``--missing`` дополнительно добирает
фильмы без описания. Если ``overview`` в основной локали пуст, из TMDB берётся
английский текст — **название и остальные поля при этом не переводятся**.

Использование::

    python -m backend.enrich_movies                  # только фильмы без постера
    python -m backend.enrich_movies --missing        # постер или описание отсутствуют
    python -m backend.enrich_movies --concurrency 40
    python -m backend.enrich_movies --limit 500
    python -m backend.enrich_movies --all            # переобработать весь каталог
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time

import aiohttp
import asyncpg

from backend.database import DATABASE_URL, engine, init_db
from backend.load_movielens import dsn_from_url
from backend.tmdb import (
    DEFAULT_LANGUAGE,
    FALLBACK_LANGUAGE,
    MovieRecord,
    fetch_movie_async,
    load_links,
    parse_movie,
)

logger = logging.getLogger("enrich_movies")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LINKS_CSV = os.path.join(DATA_DIR, "links.csv")

DEFAULT_CONCURRENCY = 40  # asyncio.Semaphore(40) — лимит одновременных запросов к TMDB
DEFAULT_BATCH = 500       # размер пакета UPDATE
DEFAULT_CHUNK = 1000      # сколько задач держать «в полёте» одновременно
MAX_DB_POOL = 1           # все записи идут через одно соединение asyncpg

_UPDATE_SQL = """
    UPDATE movies
       SET title = $2,
           year = $3,
           genres = $4,
           poster_url = $5,
           synopsis = $6,
           director = $7,
           cast_members = $8
     WHERE id = $1
"""


def _as_params(record: MovieRecord) -> tuple:
    """Преобразовать :class:`MovieRecord` в кортеж параметров UPDATE."""
    return (
        record.id,
        record.title,
        record.year,
        record.genres,
        record.poster_url,
        record.synopsis,
        record.director,
        record.cast_members,
    )


async def _fetch_one(
    http: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    movie_id: int,
    tmdb_id: int,
    api_key: str,
    language: str,
    fallback_language: str | None,
) -> MovieRecord | None:
    """Скачать и разобрать один фильм под ограничением семафора."""
    async with semaphore:
        payload = await fetch_movie_async(
            http, tmdb_id, api_key, language, fallback_language=fallback_language
        )
    if payload is None:
        return None
    return parse_movie(movie_id, payload)


async def _select_targets(
    conn: asyncpg.Connection,
    mapping: dict[int, int],
    all_movies: bool,
    missing_synopsis: bool,
    limit: int | None,
) -> list[tuple[int, int]]:
    """Определить список ``(movieId, tmdbId)`` для обработки."""
    if all_movies:
        rows = await conn.fetch("SELECT id FROM movies")
    elif missing_synopsis:
        rows = await conn.fetch(
            "SELECT id FROM movies "
            "WHERE poster_url IS NULL OR synopsis IS NULL OR synopsis = ''"
        )
    else:
        rows = await conn.fetch("SELECT id FROM movies WHERE poster_url IS NULL")
    target_ids = sorted({row["id"] for row in rows} & set(mapping))
    pairs = [(movie_id, mapping[movie_id]) for movie_id in target_ids]
    if limit is not None:
        pairs = pairs[: max(0, limit)]
    return pairs


async def enrich(
    api_key: str,
    *,
    links_path: str = LINKS_CSV,
    all_movies: bool = False,
    missing_synopsis: bool = False,
    limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    batch: int = DEFAULT_BATCH,
    language: str = DEFAULT_LANGUAGE,
    fallback_language: str | None = FALLBACK_LANGUAGE,
) -> tuple[int, int]:
    """Обогатить каталог. Возвращает ``(обновлено, пропущено)``."""
    await init_db()
    await engine.dispose()

    mapping = load_links(links_path)
    conn = await asyncpg.connect(dsn_from_url(DATABASE_URL))
    updated = skipped = 0
    try:
        pairs = await _select_targets(conn, mapping, all_movies, missing_synopsis, limit)
        if not pairs:
            logger.info("Нечего обогащать: подходящих фильмов нет")
            return 0, 0

        logger.info(
            "К обогащению: %s фильмов, concurrency=%s", len(pairs), concurrency
        )
        semaphore = asyncio.Semaphore(concurrency)
        started = time.perf_counter()
        buffer: list[tuple] = []
        done = 0

        # trust_env=True — уважать системный прокси (реестр Windows / HTTPS_PROXY).
        # Иначе aiohttp игнорирует прокси и падает с «connection refused».
        async with aiohttp.ClientSession(trust_env=True) as http:
            for start in range(0, len(pairs), DEFAULT_CHUNK):
                chunk = pairs[start : start + DEFAULT_CHUNK]
                results = await asyncio.gather(
                    *(
                        _fetch_one(http, semaphore, movie_id, tmdb_id, api_key, language,
                                   fallback_language)
                        for movie_id, tmdb_id in chunk
                    )
                )
                for record in results:
                    done += 1
                    if record is None:
                        skipped += 1
                    else:
                        buffer.append(_as_params(record))
                        updated += 1

                if len(buffer) >= batch:
                    await conn.executemany(_UPDATE_SQL, buffer)
                    buffer.clear()

                rate = done / max(time.perf_counter() - started, 1e-9)
                eta = (len(pairs) - done) / max(rate, 1e-9)
                logger.info(
                    "обогащение: %s/%s (ок %s, пропуск %s) — %.0f фильм/с, осталось ~%.0f мин",
                    done, len(pairs), updated, skipped, rate, eta / 60,
                )

        if buffer:
            await conn.executemany(_UPDATE_SQL, buffer)

        logger.info(
            "Готово: обновлено %s, пропущено %s за %.1f мин",
            updated, skipped, (time.perf_counter() - started) / 60,
        )
    finally:
        await conn.close()

    return updated, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Массовое обогащение каталога Rexter метаданными TMDB (async)."
    )
    parser.add_argument("--all", action="store_true", help="обрабатывать все фильмы (не только без постера)")
    parser.add_argument(
        "--missing", action="store_true",
        help="добирать фильмы без постера ИЛИ без описания (с англ. фолбэком)",
    )
    parser.add_argument(
        "--no-fallback", action="store_true",
        help="не запрашивать англ. описание, если пусто в основной локали",
    )
    parser.add_argument("--limit", type=int, help="ограничить число фильмов")
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help="лимит одновременных запросов к TMDB (по умолчанию 40)",
    )
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="размер пакета записи")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="язык TMDB (по умолчанию ru-RU)")
    parser.add_argument("--links", default=LINKS_CSV, help="путь к links.csv")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    if not os.environ.get("TMDB_API_KEY"):
        logger.error("Переменная окружения TMDB_API_KEY не задана.")
        return 2

    logger.info("База данных: %s", DATABASE_URL)
    try:
        asyncio.run(
            enrich(
                os.environ["TMDB_API_KEY"],
                links_path=args.links,
                all_movies=args.all,
                missing_synopsis=args.missing,
                limit=args.limit,
                concurrency=args.concurrency,
                batch=args.batch,
                language=args.language,
                fallback_language=None if args.no_fallback else FALLBACK_LANGUAGE,
            )
        )
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
