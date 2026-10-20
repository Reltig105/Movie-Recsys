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

Стратегия добора:

1. основная запись — ``/movie/{tmdbId}`` из ``links.csv``;
2. если постер или описание так и не нашлись (а также если ``tmdbId`` в
   ``links.csv`` вообще отсутствует) — строгий поиск ``/search/movie`` по
   названию и году: у части фильмов ссылка ведёт на «пустой» дубликат TMDB,
   тогда как соседняя запись с тем же названием и годом имеет постер;
3. найденная запись **дополняет** основную только пустыми полями — название
   никогда не подменяется;
4. факт окончательного ответа TMDB отмечается в ``movies.tmdb_checked_at``,
   поэтому повторный запуск не дёргает фильмы, для которых на TMDB ничего нет.

Скрипт идемпотентен и возобновляем: по умолчанию обрабатываются фильмы без
постера (``poster_url IS NULL``), ``--missing`` дополнительно добирает фильмы без
описания. Если ``overview`` в основной локали пуст, из TMDB берётся английский
текст — **название при этом не подменяется**: оно остаётся английским из
``movies.csv`` (TMDB-овское пишется только в пустое поле).

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
from datetime import datetime, timezone

import aiohttp
import asyncpg

from backend.database import DATABASE_URL, engine, init_db
from backend.load_movielens import dsn_from_url
from backend.tmdb import (
    DEFAULT_LANGUAGE,
    FALLBACK_LANGUAGE,
    MovieRecord,
    fetch_movie_result,
    load_links,
    merge_records,
    parse_movie,
    search_movie_result,
)

logger = logging.getLogger("enrich_movies")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LINKS_CSV = os.path.join(DATA_DIR, "links.csv")

DEFAULT_CONCURRENCY = 40  # asyncio.Semaphore(40) — лимит одновременных запросов к TMDB
DEFAULT_BATCH = 500       # размер пакета UPDATE
DEFAULT_CHUNK = 1000      # сколько задач держать «в полёте» одновременно
MAX_DB_POOL = 1           # все записи идут через одно соединение asyncpg

# Обновление строго аддитивное: пустое/None-значение никогда не затирает уже
# имеющиеся данные (важно для добора, где TMDB может отдать запись без постера).
# Название — отдельное правило: оно приходит из ``movies.csv`` и TMDB-овским не
# подменяется (пишем, только если в строке названия ещё нет).
_UPDATE_SQL = """
    UPDATE movies
       SET title = COALESCE(NULLIF(title, ''), NULLIF($2, '')),
           year = COALESCE($3, year),
           genres = COALESCE(NULLIF($4, ''), genres),
           poster_url = COALESCE($5, poster_url),
           synopsis = COALESCE(NULLIF($6, ''), synopsis),
           director = COALESCE(NULLIF($7, ''), director),
           cast_members = COALESCE(NULLIF($8, ''), cast_members),
           tmdb_checked_at = COALESCE($9, tmdb_checked_at)
     WHERE id = $1
"""


def _as_params(
    movie_id: int,
    record: MovieRecord | None,
    checked_at: datetime | None,
) -> tuple:
    """Преобразовать :class:`MovieRecord` (или пустой результат) в параметры UPDATE."""
    if record is None:
        return (movie_id, None, None, None, None, None, None, None, checked_at)
    return (
        record.id,
        record.title,
        record.year,
        record.genres,
        record.poster_url,
        record.synopsis,
        record.director,
        record.cast_members,
        checked_at,
    )


async def _fetch_one(
    http: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    movie_id: int,
    tmdb_id: int | None,
    title: str,
    year: int | None,
    api_key: str,
    language: str,
    fallback_language: str | None,
) -> tuple[MovieRecord | None, bool]:
    """Скачать и разобрать один фильм под ограничением семафора.

    Возвращает ``(record, definitive)``: ``definitive`` — TMDB дал окончательный
    ответ, повторять не нужно.
    """
    record: MovieRecord | None = None
    legs: list[bool] = []  # окончательность каждого выполненного запроса

    async with semaphore:
        if tmdb_id is not None:
            payload, definitive = await fetch_movie_result(
                http, tmdb_id, api_key, language, fallback_language=fallback_language
            )
            legs.append(definitive)
            if payload is not None:
                record = parse_movie(movie_id, payload)

        # Добор по названию: и когда ссылки нет, и когда у основной записи нет
        # постера или описания.
        if record is None or not record.poster_url or not record.synopsis:
            alt_payload, alt_definitive = await search_movie_result(
                http, title, year, api_key, language,
                fallback_language=fallback_language,
            )
            legs.append(alt_definitive)
            if alt_payload is not None:
                alt_record = parse_movie(movie_id, alt_payload)
                record = merge_records(record, alt_record) if record else alt_record

    # Помечаем проверенным, только если все обращения к TMDB дали окончательный
    # ответ: иначе разовый сетевой сбой «залипнет» и фильм больше не доберётся.
    return record, bool(legs) and all(legs)


async def _select_targets(
    conn: asyncpg.Connection,
    mapping: dict[int, int],
    all_movies: bool,
    missing_synopsis: bool,
    limit: int | None,
) -> list[tuple[int, int | None, str, int | None]]:
    """Определить список ``(movieId, tmdbId, title, year)`` для обработки.

    Фильмы без ``tmdbId`` тоже попадают в выборку — их ищут по названию и году.
    Уже проверенные (``tmdb_checked_at``) повторно не берутся.
    """
    if all_movies:
        rows = await conn.fetch("SELECT id, title, year FROM movies")
    elif missing_synopsis:
        rows = await conn.fetch(
            "SELECT id, title, year FROM movies "
            "WHERE (poster_url IS NULL OR synopsis IS NULL OR synopsis = '') "
            "  AND tmdb_checked_at IS NULL"
        )
    else:
        rows = await conn.fetch(
            "SELECT id, title, year FROM movies "
            "WHERE poster_url IS NULL AND tmdb_checked_at IS NULL"
        )

    targets = [
        (row["id"], mapping.get(row["id"]), row["title"], row["year"])
        for row in rows
    ]
    targets.sort(key=lambda item: item[0])
    if limit is not None:
        targets = targets[: max(0, limit)]
    return targets


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
        targets = await _select_targets(conn, mapping, all_movies, missing_synopsis, limit)
        if not targets:
            logger.info("Нечего обогащать: подходящих фильмов нет")
            return 0, 0

        logger.info(
            "К обогащению: %s фильмов, concurrency=%s", len(targets), concurrency
        )
        semaphore = asyncio.Semaphore(concurrency)
        started = time.perf_counter()
        buffer: list[tuple] = []
        done = 0

        # trust_env=True — уважать системный прокси (реестр Windows / HTTPS_PROXY).
        # Иначе aiohttp игнорирует прокси и падает с «connection refused».
        async with aiohttp.ClientSession(trust_env=True) as http:
            for start in range(0, len(targets), DEFAULT_CHUNK):
                chunk = targets[start : start + DEFAULT_CHUNK]
                results = await asyncio.gather(
                    *(
                        _fetch_one(http, semaphore, movie_id, tmdb_id, title, year,
                                   api_key, language, fallback_language)
                        for movie_id, tmdb_id, title, year in chunk
                    )
                )
                for (movie_id, _tmdb_id, _title, _year), (record, definitive) in zip(chunk, results):
                    done += 1
                    if record is None and not definitive:
                        # Сетевой сбой — не помечаем, попробуем в следующий раз.
                        skipped += 1
                        continue

                    checked_at = datetime.now(timezone.utc) if definitive else None
                    buffer.append(_as_params(movie_id, record, checked_at))
                    if record is None:
                        skipped += 1
                    else:
                        updated += 1

                if len(buffer) >= batch:
                    await conn.executemany(_UPDATE_SQL, buffer)
                    buffer.clear()

                rate = done / max(time.perf_counter() - started, 1e-9)
                eta = (len(targets) - done) / max(rate, 1e-9)
                logger.info(
                    "обогащение: %s/%s (ок %s, пропуск %s) — %.0f фильм/с, осталось ~%.0f мин",
                    done, len(targets), updated, skipped, rate, eta / 60,
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
