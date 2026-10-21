"""Сборка разреженных матриц для обучения LightFM.

Что делает скрипт::

    ratings ──► interactions.npz   (users × movies, CSR, значения = score)
    movies  ──► item_features.npz  (movies × features, CSR, multi-hot)
    id      ──► user_mapping.pkl / movie_mapping.pkl

Матрица признаков гибридная: контент (``genre:``, ``director:``, ``cast:``) плюс
identity-признак ``movie:<id>``. Identity даёт модели «память» о конкретном
фильме (качество exploit-выдачи), а контентные признаки позволяют оценить
новинку без единой оценки — их векторы просто складываются (см. LightFM).

Идентификаторы MovieLens не непрерывны (``movie_id`` доходит до 292 757 при
87 585 фильмах), поэтому обоим измерениям присваиваются внутренние индексы
``0…N-1``. Маппинг хранится как биекция: ``raw_to_index`` (словарь) и
``index_to_raw`` (массив — обратное преобразование за O(1)).

Память: 32 млн взаимодействий — это три массива ``int32/int32/float32``
(≈ 384 МБ) плюс столько же на момент сборки CSR. Строки читаются из базы
порциями по ``--chunk-size`` и не накапливаются как объекты Python.

База опрашивается асинхронно (asyncpg): синхронного драйвера в проекте нет,
а pandas используется для разбора порций и метаданных фильмов.

Запуск::

    python -m backend.ml_pipeline.build_features
    python -m backend.ml_pipeline.build_features --limit 2000000 --chunk-size 500000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pickle
import time
from pathlib import Path

import asyncpg
import numpy as np
import pandas as pd
from scipy import sparse

from backend.database import DATABASE_URL
from backend.load_movielens import dsn_from_url

logger = logging.getLogger("rexter.ml_pipeline")

PROCESSED_DIR = Path("data/processed")
DEFAULT_CHUNK = 1_000_000
UNKNOWN = "<unknown>"

#: Категориальные колонки фильмов и префиксы их признаков.
FEATURE_COLUMNS = {
    "genres": "genre",
    "director": "director",
    "cast_members": "cast",
}


def _fmt(value: int) -> str:
    """Число с пробелами между разрядами (``32 000 204``)."""
    return f"{value:,}".replace(",", " ")


def _to_internal(sorted_ids: np.ndarray, values: np.ndarray, label: str) -> np.ndarray:
    """Отобразить внешние id в непрерывные индексы ``0…N-1``.

    ``sorted_ids`` отсортирован, поэтому ``searchsorted`` даёт O(log N) на
    элемент без словарей и питоновских объектов.
    """
    if sorted_ids.size == 0:
        raise ValueError(f"{label}: таблица пуста")
    positions = np.searchsorted(sorted_ids, values)
    np.clip(positions, 0, sorted_ids.size - 1, out=positions)
    if not np.array_equal(sorted_ids[positions], values):
        raise ValueError(f"{label}: в ratings встретился id, которого нет в справочнике")
    return positions.astype(np.int32)


def _save_mapping(path: Path, raw_ids: np.ndarray) -> None:
    """Сохранить биекцию внешний id ↔ внутренний индекс."""
    payload = {
        "raw_to_index": {int(raw): index for index, raw in enumerate(raw_ids)},
        "index_to_raw": raw_ids.astype(np.int32),
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info(
        "%s: %s → 0…%s", path.name, _fmt(raw_ids.size), _fmt(raw_ids.size - 1)
    )


async def _load_ids(conn: asyncpg.Connection, query: str, label: str) -> np.ndarray:
    """Отсортированный массив id (порядок задаёт внутреннюю нумерацию)."""
    rows = await conn.fetch(query)
    ids = np.fromiter((row[0] for row in rows), dtype=np.int64, count=len(rows))
    logger.info("%s: %s", label, _fmt(ids.size))
    return ids


async def _load_interactions(
    conn: asyncpg.Connection,
    user_ids: np.ndarray,
    movie_ids: np.ndarray,
    chunk_size: int,
    limit: int | None,
) -> sparse.csr_matrix:
    """Прочитать все оценки и собрать CSR ``users × movies``."""
    total = await conn.fetchval("SELECT count(*) FROM ratings")
    if limit is not None:
        total = min(total, limit)
    logger.info("ratings: строк к чтению — %s", _fmt(total))

    user_index = np.empty(total, dtype=np.int32)
    movie_index = np.empty(total, dtype=np.int32)
    scores = np.empty(total, dtype=np.float32)

    query = "SELECT user_id, movie_id, score FROM ratings"
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    started = time.perf_counter()
    offset = 0
    async with conn.transaction():
        cursor = await conn.cursor(query)
        while offset < total:
            chunk = await cursor.fetch(min(chunk_size, total - offset))
            if not chunk:
                break
            frame = pd.DataFrame.from_records(
                [tuple(record) for record in chunk],
                columns=["user_id", "movie_id", "score"],
            )
            size = len(frame)
            user_index[offset : offset + size] = _to_internal(
                user_ids, frame["user_id"].to_numpy(), "users"
            )
            movie_index[offset : offset + size] = _to_internal(
                movie_ids, frame["movie_id"].to_numpy(), "movies"
            )
            scores[offset : offset + size] = frame["score"].to_numpy(dtype=np.float32)
            offset += size
            elapsed = time.perf_counter() - started
            logger.info(
                "ratings: %s / %s (%.0f строк/с)",
                _fmt(offset),
                _fmt(total),
                offset / max(elapsed, 1e-9),
            )

    if offset != total:
        raise RuntimeError(f"прочитано {offset} строк вместо {total}")

    matrix = sparse.csr_matrix(
        (scores, (user_index, movie_index)),
        shape=(user_ids.size, movie_ids.size),
    )
    logger.info(
        "interactions: %s × %s, ненулевых — %s",
        _fmt(matrix.shape[0]),
        _fmt(matrix.shape[1]),
        _fmt(matrix.nnz),
    )
    return matrix


def _split(value: object) -> list[str]:
    """``"Drama, Comedy"`` → ``["Drama", "Comedy"]`` (NULL → ``[]``)."""
    if not isinstance(value, str) or not value.strip():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _build_item_features(
    frame: pd.DataFrame, with_item_ids: bool = True
) -> tuple[sparse.csr_matrix, list[str]]:
    """Multi-hot признаков фильмов: id, жанры, режиссёр, актёры.

    Признаки разделены префиксами (``movie:``, ``genre:``, ``director:``,
    ``cast:``), чтобы значения из разных колонок не склеивались. Фильм без
    значения получает ``<unknown>`` соответствующего типа — иначе строка
    осталась бы нулевой и LightFM не смог бы его оценить. Identity-признак
    ``movie:<id>`` есть у каждого фильма всегда.
    """
    feature_index: dict[str, int] = {}
    rows: list[int] = []
    columns: list[int] = []

    for row, record in enumerate(frame.itertuples(index=False)):
        features: dict[str, None] = {}
        if with_item_ids:
            features[f"movie:{record.id}"] = None
        for column, prefix in FEATURE_COLUMNS.items():
            values = _split(getattr(record, column))
            if values:
                for value in values:
                    features[f"{prefix}:{value}"] = None
            else:
                features[f"{prefix}:{UNKNOWN}"] = None

        for feature in features:
            index = feature_index.get(feature)
            if index is None:
                index = len(feature_index)
                feature_index[feature] = index
            rows.append(row)
            columns.append(index)

    matrix = sparse.csr_matrix(
        (
            np.ones(len(rows), dtype=np.float32),
            (
                np.asarray(rows, dtype=np.int32),
                np.asarray(columns, dtype=np.int32),
            ),
        ),
        shape=(len(frame), len(feature_index)),
    )
    names: list[str] = [""] * len(feature_index)
    for name, index in feature_index.items():
        names[index] = name
    logger.info(
        "item_features: %s × %s, ненулевых — %s",
        _fmt(matrix.shape[0]),
        _fmt(matrix.shape[1]),
        _fmt(matrix.nnz),
    )
    return matrix, names


def _size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024


async def build(
    out_dir: Path,
    chunk_size: int,
    limit: int | None,
    with_item_ids: bool = True,
) -> int:
    """Собрать и сохранить все артефакты в ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    conn = await asyncpg.connect(dsn_from_url(DATABASE_URL))
    try:
        user_ids = await _load_ids(conn, "SELECT id FROM users ORDER BY id", "users")
        movie_ids = await _load_ids(conn, "SELECT id FROM movies ORDER BY id", "movies")
        interactions = await _load_interactions(
            conn, user_ids, movie_ids, chunk_size, limit
        )
        movie_rows = await conn.fetch(
            "SELECT id, genres, director, cast_members FROM movies ORDER BY id"
        )
    finally:
        await conn.close()

    movies = pd.DataFrame.from_records(
        [tuple(row) for row in movie_rows],
        columns=["id", "genres", "director", "cast_members"],
    )
    del movie_rows
    if not np.array_equal(movies["id"].to_numpy(), movie_ids):
        raise RuntimeError("порядок фильмов в каталоге не совпал с movie_mapping")

    item_features, feature_names = _build_item_features(movies, with_item_ids)
    del movies

    _save_mapping(out_dir / "user_mapping.pkl", user_ids)
    _save_mapping(out_dir / "movie_mapping.pkl", movie_ids)
    # Те же биекции в .npy: обучающий контейнер живёт на numpy 1.26, а pickle
    # из numpy 2.x он прочитать не может (модуль ``numpy._core`` переименован).
    np.save(out_dir / "user_ids.npy", user_ids.astype(np.int32))
    np.save(out_dir / "movie_ids.npy", movie_ids.astype(np.int32))
    sparse.save_npz(out_dir / "interactions.npz", interactions)
    sparse.save_npz(out_dir / "item_features.npz", item_features)
    with (out_dir / "item_feature_names.pkl").open("wb") as handle:
        pickle.dump(feature_names, handle, protocol=pickle.HIGHEST_PROTOCOL)

    users_total, movies_total = interactions.shape
    density = interactions.nnz / max(users_total * movies_total, 1)
    logger.info("плотность матрицы взаимодействий — %.4f %%", density * 100)
    for name in (
        "user_mapping.pkl",
        "movie_mapping.pkl",
        "user_ids.npy",
        "movie_ids.npy",
        "interactions.npz",
        "item_features.npz",
        "item_feature_names.pkl",
    ):
        logger.info("%s — %.1f МБ", name, _size_mb(out_dir / name))
    logger.info("готово за %.1f с", time.perf_counter() - started)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Собрать разреженные матрицы (interactions + item features) для LightFM."
    )
    parser.add_argument(
        "--out-dir", type=Path, default=PROCESSED_DIR, help="куда писать артефакты"
    )
    parser.add_argument(
        "--chunk-size", type=int, default=DEFAULT_CHUNK, help="строк за один fetch"
    )
    parser.add_argument("--limit", type=int, help="ограничить число оценок (для проверки)")
    parser.add_argument(
        "--no-item-ids",
        action="store_true",
        help="не добавлять identity-признак movie:<id> (только контент)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    logger.info("База данных: %s", DATABASE_URL)
    try:
        return asyncio.run(
            build(args.out_dir, args.chunk_size, args.limit, not args.no_item_ids)
        )
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
