"""Пересчёт предрасчитанной статистики фильмов (``movie_stats``).

Тяжёлая агрегация ``GROUP BY`` по 32 млн строк ``ratings`` выполняется **только
здесь**, а не в API. Результат хранится в materialized view::

    movie_id | avg_rating | ratings_count

Первичное наполнение — это ровно один запрос
``CREATE MATERIALIZED VIEW ... AS SELECT ... GROUP BY`` (эквивалент
``INSERT INTO movie_stats SELECT ... GROUP BY``). Уникальный индекс по
``movie_id`` обязателен для ``REFRESH MATERIALIZED VIEW CONCURRENTLY``, то есть
обновления без блокировки читателей.

Использование::

    python -m backend.refresh_stats              # создать (если нет) и обновить
    python -m backend.refresh_stats --recreate   # DROP + CREATE (полный пересчёт)
    python -m backend.refresh_stats --no-concurrently

После ``python -m backend.load_movielens --reset`` view удаляется вместе с
таблицами — запустите этот скрипт, чтобы наполнить её заново.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

import asyncpg

from backend.database import DATABASE_URL
from backend.load_movielens import dsn_from_url

logger = logging.getLogger("refresh_stats")

#: Один тяжёлый проход по ratings: агрегат по каждому фильму.
CREATE_SQL = """
CREATE MATERIALIZED VIEW movie_stats AS
SELECT movie_id,
       avg(score)::double precision AS avg_rating,
       count(*)::integer           AS ratings_count
  FROM ratings
 GROUP BY movie_id
"""

#: Уникальный индекс — требование REFRESH MATERIALIZED VIEW CONCURRENTLY.
INDEX_SQL = """
CREATE UNIQUE INDEX movie_stats_movie_id_key ON movie_stats (movie_id)
"""

_VIEW_EXISTS_SQL = "SELECT to_regclass('movie_stats') IS NOT NULL"
_INDEX_EXISTS_SQL = "SELECT to_regclass('movie_stats_movie_id_key') IS NOT NULL"


async def refresh_movie_stats(
    *, concurrently: bool = True, recreate: bool = False
) -> dict[str, int | str | float]:
    """Создать/обновить ``movie_stats`` и вернуть сводку для лога."""
    conn = await asyncpg.connect(dsn_from_url(DATABASE_URL))
    try:
        started = time.perf_counter()
        exists = bool(await conn.fetchval(_VIEW_EXISTS_SQL))

        if recreate and exists:
            logger.info("movie_stats: DROP (полный пересчёт)")
            await conn.execute("DROP MATERIALIZED VIEW movie_stats")
            exists = False

        if not exists:
            logger.info("movie_stats: CREATE + первичная агрегация ratings…")
            await conn.execute(CREATE_SQL)
            await conn.execute(INDEX_SQL)
            action = "created"
        else:
            if not await conn.fetchval(_INDEX_EXISTS_SQL):
                logger.info("movie_stats: создаю уникальный индекс")
                await conn.execute(INDEX_SQL)
            statement = (
                "REFRESH MATERIALIZED VIEW CONCURRENTLY movie_stats"
                if concurrently
                else "REFRESH MATERIALIZED VIEW movie_stats"
            )
            logger.info("movie_stats: %s", statement)
            await conn.execute(statement)
            action = "refreshed_concurrently" if concurrently else "refreshed"

        await conn.execute("ANALYZE movie_stats")
        elapsed = time.perf_counter() - started

        row = await conn.fetchrow(
            "SELECT count(*) AS rows, "
            "       COALESCE(min(ratings_count), 0) AS min_cnt, "
            "       COALESCE(max(ratings_count), 0) AS max_cnt "
            "  FROM movie_stats"
        )
        summary: dict[str, int | str | float] = {
            "action": action,
            "rows": int(row["rows"]),
            "min_ratings": int(row["min_cnt"]),
            "max_ratings": int(row["max_cnt"]),
            "seconds": round(elapsed, 1),
        }
        logger.info(
            "Готово: %s, строк %s, оценок на фильм %s–%s, %.1f с",
            action,
            summary["rows"],
            summary["min_ratings"],
            summary["max_ratings"],
            elapsed,
        )
        return summary
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Пересчёт movie_stats (materialized view) по оценкам MovieLens."
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="DROP + CREATE: полный пересчёт (блокирует чтение на время агрегации)",
    )
    parser.add_argument(
        "--no-concurrently",
        action="store_true",
        help="блокирующий REFRESH вместо CONCURRENTLY",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    logger.info("База данных: %s", DATABASE_URL)
    try:
        asyncio.run(
            refresh_movie_stats(
                concurrently=not args.no_concurrently,
                recreate=args.recreate,
            )
        )
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
