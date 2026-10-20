"""Массовая загрузка датасета MovieLens 32M в PostgreSQL.

Только PostgreSQL + ``asyncpg``. Данные заливаются нативным ``COPY``
(:meth:`asyncpg.Connection.copy_records_to_table`) — это на порядок быстрее
построчных вставок и рассчитано на десятки миллионов строк. Никаких потоков
и никакого SQLite.

Production-порядок загрузки (критично для 32M строк):

1. схема создаётся **без «тяжёлых» объектов на ``ratings``** — индексов,
   PRIMARY KEY, UNIQUE и внешних ключей;
2. ``movies`` / ``users`` / ``ratings`` заливаются через ``COPY`` в «сырые»
   таблицы (ни одна строка не обслуживает индексы и не проверяет FK);
3. только **после** заливки на ``ratings`` строятся индексы и констрейнты по
   уже готовым данным, затем выполняется ``ANALYZE``.

Поддерживать три индекса, UNIQUE и два FK построчно при вставке десятков
миллионов строк дороже, чем построить их один раз в конце — это стандартный
приём массовой загрузки. DDL отложенных объектов снимается с живой схемы
(``pg_get_constraintdef`` / ``pg_indexdef``), поэтому он не дублируется в коде
и не расходится с :mod:`backend.models`.

Порядок загрузки таблиц:

* ``movies``  ← ``data/movies.csv`` (id, title, year, genres);
* ``users``   ← список ``userId`` из ``data/ratings.csv`` (теневые пользователи
  MovieLens без логина — нужны для внешнего ключа ``ratings.user_id``);
* ``ratings`` ← ``data/ratings.csv`` (user_id, movie_id, score, timestamp).

Оценки MovieLens имеют шаг 0.5 и округляются до целого (``0.5 → 1``), так как
``Rating.score`` — целочисленный.

Использование::

    python -m backend.load_movielens                  # фильмы + все оценки
    python -m backend.load_movielens --reset          # пересоздать схему и залить
    python -m backend.load_movielens --movies-only
    python -m backend.load_movielens --ratings-limit 1000000

Полную загрузку выполняйте с ``--reset``: скрипт рассчитан на чистую БД.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import re
import time
from collections.abc import Iterator

import asyncpg

from backend.database import DATABASE_URL, Base, engine, init_db

logger = logging.getLogger("load_movielens")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MOVIES_CSV = os.path.join(DATA_DIR, "movies.csv")
RATINGS_CSV = os.path.join(DATA_DIR, "ratings.csv")

_NO_GENRES = "(no genres listed)"
_YEAR_RE = re.compile(r"^(?P<title>.*?)\s*\((?P<year>\d{4})\)\s*$")
_PROGRESS_EVERY = 1_000_000

#: Таблицы, «тяжёлые» объекты которых строятся ПОСЛЕ COPY.
#: ``ratings`` — единственная таблица на десятки миллионов строк; индексы и
#: констрейнты на ней обслуживать построчно недопустимо дорого. ``movies`` и
#: ``users`` на два порядка меньше, их объекты строятся сразу при создании схемы.
DEFERRED_TABLES: tuple[str, ...] = ("ratings",)

# Констрейнты таблицы (PK / UNIQUE / FK): имя + готовое определение.
_CONSTRAINTS_SQL = """
    SELECT conname AS name, pg_get_constraintdef(oid) AS definition
      FROM pg_constraint
     WHERE conrelid = $1::regclass
"""

# «Обычные» индексы (не поддерживающие констрейнт): имя + готовый CREATE INDEX.
# Индексы PK/UNIQUE исключены — у них имя совпадает с именем констрейнта,
# и они снимаются/возвращаются вместе с ним.
_PLAIN_INDEXES_SQL = """
    SELECT i.indexname AS name, i.indexdef AS definition
      FROM pg_indexes AS i
     WHERE i.tablename = $1
       AND NOT EXISTS (
           SELECT 1
             FROM pg_constraint AS c
            WHERE c.conrelid = $1::regclass
              AND c.conname = i.indexname
       )
"""


# --------------------------------------------------------------------------- #
# Разбор строк MovieLens
# --------------------------------------------------------------------------- #
def parse_title(raw: str) -> tuple[str, int | None]:
    """Разделить строку ``"Toy Story (1995)"`` на заголовок и год."""
    match = _YEAR_RE.match(raw.strip())
    if match:
        return match.group("title").strip(), int(match.group("year"))
    return raw.strip(), None


def parse_genres(raw: str) -> str:
    """``"Action|Comedy"`` → ``"Action, Comedy"`` (пустые жанры → ``""``)."""
    if not raw or raw == _NO_GENRES:
        return ""
    return ", ".join(part for part in (g.strip() for g in raw.split("|")) if part)


def round_score(value: str) -> int:
    """Округлить оценку MovieLens (шаг 0.5) до целого, половина — вверх."""
    return int(float(value) + 0.5)


def dsn_from_url(url: str) -> str:
    """``postgresql+asyncpg://…`` → ``postgresql://…`` (asyncpg не знает SQLAlchemy-схему)."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def open_csv(path: str) -> tuple[object, dict[str, int], Iterator[list[str]]]:
    """Открыть CSV и вернуть ``(дескриптор, карта колонок, итератор строк)``.

    Читаем через быстрый :class:`csv.reader` с индексами колонок вместо
    :class:`csv.DictReader` — на 32 миллионах строк разница ощутима.
    Вызывающая сторона обязана закрыть дескриптор (``try/finally``).
    """
    handle = open(path, encoding="utf-8", newline="")
    reader = csv.reader(handle)
    header = next(reader, None)
    if not header:
        handle.close()
        raise ValueError(f"Пустой или повреждённый CSV: {path}")
    index = {name: position for position, name in enumerate(header)}
    return handle, index, reader


# --------------------------------------------------------------------------- #
# Схема
# --------------------------------------------------------------------------- #
async def recreate_schema() -> None:
    """Удалить и заново создать все таблицы (drop_all + create_all)."""
    from backend import models  # noqa: F401  (регистрирует модели в Base.metadata)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    logger.info("Схема пересоздана (drop_all + create_all)")


# --------------------------------------------------------------------------- #
# Отложенные индексы и констрейнты (строятся после COPY)
# --------------------------------------------------------------------------- #
async def capture_deferred_objects(conn: asyncpg.Connection) -> dict[str, list[str]]:
    """Снять с живой схемы DDL индексов и констрейнтов для отложенного построения."""
    ddl: dict[str, list[str]] = {}
    for table in DEFERRED_TABLES:
        statements = [
            f'ALTER TABLE {table} ADD CONSTRAINT "{row["name"]}" {row["definition"]}'
            for row in await conn.fetch(_CONSTRAINTS_SQL, table)
        ]
        statements += [row["definition"] for row in await conn.fetch(_PLAIN_INDEXES_SQL, table)]
        ddl[table] = statements
        logger.info("%s: отложено объектов — %s", table, len(statements))
    return ddl


async def drop_deferred_objects(conn: asyncpg.Connection) -> None:
    """Снять индексы и констрейнты с горячих таблиц перед массовой вставкой."""
    for table in DEFERRED_TABLES:
        constraints = await conn.fetch(
            "SELECT conname AS name FROM pg_constraint WHERE conrelid = $1::regclass", table
        )
        for row in constraints:
            await conn.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{row["name"]}"')
        indexes = await conn.fetch(_PLAIN_INDEXES_SQL, table)
        for row in indexes:
            await conn.execute(f'DROP INDEX "{row["name"]}"')
        logger.info(
            "%s: снято перед COPY — %s констрейнтов, %s индексов",
            table, len(constraints), len(indexes),
        )


async def create_deferred_objects(
    conn: asyncpg.Connection, ddl: dict[str, list[str]]
) -> None:
    """Построить отложенные объекты по уже загруженным данным."""
    started = time.perf_counter()
    for table, statements in ddl.items():
        for statement in statements:
            await conn.execute(statement)
        logger.info("%s: построено объектов — %s", table, len(statements))
    logger.info("Индексы и констрейнты построены за %.1f с", time.perf_counter() - started)


async def analyze_tables(conn: asyncpg.Connection) -> None:
    """Обновить статистику планировщика по залитым таблицам."""
    for table in ("movies", "users", "ratings"):
        await conn.execute(f"ANALYZE {table}")
    logger.info("ANALYZE выполнен (movies, users, ratings)")


# --------------------------------------------------------------------------- #
# COPY-загрузка
# --------------------------------------------------------------------------- #
async def load_movies(conn: asyncpg.Connection, path: str) -> None:
    """``COPY movies`` из ``movies.csv`` (генератор — потоковая передача)."""
    state = {"count": 0}

    def records():
        handle, index, reader = open_csv(path)
        title_col, year_col, genres_col = index["title"], index["movieId"], index["genres"]
        try:
            for row in reader:
                title, year = parse_title(row[title_col])
                state["count"] += 1
                yield (int(row[year_col]), title, year, parse_genres(row[genres_col]))
        finally:
            handle.close()

    started = time.perf_counter()
    await conn.copy_records_to_table(
        "movies", records=records(), columns=["id", "title", "year", "genres"]
    )
    logger.info(
        "movies: COPY %s строк за %.1f с", state["count"], time.perf_counter() - started
    )


def collect_user_ids(path: str) -> list[int]:
    """Собрать уникальные ``userId`` из ``ratings.csv`` (первый проход)."""
    ids: set[int] = set()
    handle, index, reader = open_csv(path)
    column = index["userId"]
    try:
        for row in reader:
            ids.add(int(row[column]))
    finally:
        handle.close()
    logger.info("users: найдено %s уникальных userId", len(ids))
    return sorted(ids)


async def load_users(conn: asyncpg.Connection, user_ids: list[int]) -> None:
    """``COPY users`` — только ``id`` (теневые пользователи без логина)."""
    started = time.perf_counter()
    await conn.copy_records_to_table(
        "users", records=((uid,) for uid in user_ids), columns=["id"]
    )
    logger.info(
        "users: COPY %s строк за %.1f с", len(user_ids), time.perf_counter() - started
    )


async def load_ratings(conn: asyncpg.Connection, path: str, limit: int | None) -> None:
    """``COPY ratings`` из ``ratings.csv`` (второй проход)."""
    state = {"count": 0}
    started = time.perf_counter()

    def records():
        handle, index, reader = open_csv(path)
        user_col, movie_col = index["userId"], index["movieId"]
        score_col, time_col = index["rating"], index["timestamp"]
        try:
            for row in reader:
                if limit is not None and state["count"] >= limit:
                    return
                state["count"] += 1
                if state["count"] % _PROGRESS_EVERY == 0:
                    rate = state["count"] / max(time.perf_counter() - started, 1e-9)
                    logger.info("ratings: %s строк (~%.0f строк/с)", state["count"], rate)
                yield (
                    int(row[user_col]),
                    int(row[movie_col]),
                    round_score(row[score_col]),
                    int(row[time_col]),
                )
        finally:
            handle.close()

    await conn.copy_records_to_table(
        "ratings",
        records=records(),
        columns=["user_id", "movie_id", "score", "timestamp"],
    )
    logger.info(
        "ratings: COPY %s строк за %.1f с",
        state["count"],
        time.perf_counter() - started,
    )


async def sync_user_sequence(conn: asyncpg.Connection) -> None:
    """Сдвинуть SEQUENCE ``users.id`` после вставки явных id (иначе коллизии)."""
    await conn.execute(
        "SELECT setval(pg_get_serial_sequence('users', 'id'), "
        "COALESCE((SELECT MAX(id) FROM users), 1))"
    )


# --------------------------------------------------------------------------- #
# Оркестрация
# --------------------------------------------------------------------------- #
async def run(args: argparse.Namespace) -> int:
    """Выполнить загрузку согласно аргументам CLI."""
    if args.reset:
        await recreate_schema()
    else:
        await init_db()
    await engine.dispose()

    conn = await asyncpg.connect(dsn_from_url(DATABASE_URL))
    try:
        if not args.ratings_only:
            await load_movies(conn, args.movies_csv)

        if not args.movies_only:
            # 1. Снять «тяжёлые» объекты ratings и запомнить их DDL.
            deferred = await capture_deferred_objects(conn)
            await drop_deferred_objects(conn)

            # 2. Залить данные в «сырые» таблицы.
            user_ids = collect_user_ids(args.ratings_csv)
            await load_users(conn, user_ids)
            await load_ratings(conn, args.ratings_csv, args.ratings_limit)
            await sync_user_sequence(conn)

            # 3. Построить индексы/констрейнты по готовым данным и обновить статистику.
            await create_deferred_objects(conn, deferred)
            await analyze_tables(conn)
    finally:
        await conn.close()

    logger.info("Загрузка MovieLens завершена")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Загрузка датасета MovieLens (movies.csv + ratings.csv) в PostgreSQL."
    )
    parser.add_argument("--reset", action="store_true", help="пересоздать схему перед загрузкой")
    parser.add_argument("--movies-only", action="store_true", help="только каталог фильмов")
    parser.add_argument("--ratings-only", action="store_true", help="только оценки")
    parser.add_argument("--ratings-limit", type=int, help="ограничить число оценок")
    parser.add_argument("--movies-csv", default=MOVIES_CSV, help="путь к movies.csv")
    parser.add_argument("--ratings-csv", default=RATINGS_CSV, help="путь к ratings.csv")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    logger.info("База данных: %s", DATABASE_URL)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
