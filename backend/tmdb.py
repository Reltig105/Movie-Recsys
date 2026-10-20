"""Общие помощники для работы с TMDB API.

Модуль инкапсулирует обращение к The Movie Database и разбор ответа в плоскую
структуру :class:`MovieRecord`, а также чтение официального ``links.csv``.

Сетевой слой полностью асинхронный (:mod:`aiohttp`) — его использует массовое
обогащение каталога :mod:`backend.enrich_movies`. Синхронного клиента в проекте
нет: работа и с сетью, и с БД идёт строго через ``async``.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import re
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger("tmdb")

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w780"
DEFAULT_LANGUAGE = "ru-RU"
REQUEST_TIMEOUT = 20.0
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5
CAST_SIZE = 3
SYNOPSIS_MAX_SENTENCES = 4

# Ограничения колонок ``movies`` (см. :mod:`backend.models`). TMDB изредка отдаёт
# значения длиннее колонки — их надо обрезать, иначе весь пакет ``executemany``
# падает с ``StringDataRightTruncationError``.
TITLE_MAX = 255
GENRES_MAX = 255
DIRECTOR_MAX = 255
CAST_MAX = 512
POSTER_MAX = 512

# Sentence boundary: whitespace right after terminal punctuation.
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class MovieRecord:
    """Flat, DB-ready representation of one TMDB record."""

    id: int
    title: str
    year: int | None
    genres: str
    poster_url: str
    synopsis: str
    director: str
    cast_members: str


def truncate_synopsis(text: str, max_sentences: int = SYNOPSIS_MAX_SENTENCES) -> str:
    """Collapse whitespace and keep only the opening sentences (no spoilers)."""
    normalized = re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()
    if not normalized:
        return ""
    sentences = [part.strip() for part in _SENTENCE_RE.split(normalized) if part.strip()]
    return " ".join(sentences[:max_sentences])


def _clip(value: str, limit: int, field: str, movielens_id: int) -> str:
    """Обрезать строку до длины колонки, залогировав факт обрезки."""
    if len(value) > limit:
        logger.warning(
            "ML %s: поле %s длиной %s обрезано до %s символов",
            movielens_id, field, len(value), limit,
        )
        return value[:limit]
    return value


def extract_director(credits: dict) -> str:
    """Join the names of crew members whose job is ``Director``."""
    crew = credits.get("crew") or []
    names = [member.get("name", "") for member in crew if member.get("job") == "Director"]
    return ", ".join(name for name in names if name)


def extract_cast(credits: dict, size: int = CAST_SIZE) -> str:
    """Join the top ``size`` billed actors, comma-separated."""
    cast = credits.get("cast") or []
    names = [member.get("name", "") for member in cast[:size]]
    return ", ".join(name for name in names if name)


def _payload_to_record(movielens_id: int, payload: dict) -> MovieRecord | None:
    """Разбор ответа TMDB в :class:`MovieRecord` (пропуск, если нет постера)."""
    poster_path = payload.get("poster_path")
    if not poster_path:
        logger.debug("«%s» (ML %s): нет постера — пропуск",
                     payload.get("title") or payload.get("original_title"), movielens_id)
        return None

    release_date = payload.get("release_date") or ""
    year = int(release_date[:4]) if release_date[:4].isdigit() else None
    genres = ", ".join(
        genre["name"] for genre in (payload.get("genres") or []) if genre.get("name")
    )
    credits = payload.get("credits") or {}
    title = payload.get("title") or payload.get("original_title") or f"Movie {movielens_id}"

    return MovieRecord(
        id=movielens_id,
        title=_clip(title, TITLE_MAX, "title", movielens_id),
        year=year,
        genres=_clip(genres, GENRES_MAX, "genres", movielens_id),
        poster_url=_clip(f"{TMDB_IMAGE_BASE}{poster_path}", POSTER_MAX, "poster_url", movielens_id),
        synopsis=truncate_synopsis(payload.get("overview") or ""),
        director=_clip(extract_director(credits), DIRECTOR_MAX, "director", movielens_id),
        cast_members=_clip(extract_cast(credits), CAST_MAX, "cast_members", movielens_id),
    )


def parse_movie(movielens_id: int, payload: dict) -> MovieRecord | None:
    """Разбор ответа TMDB → :class:`MovieRecord` (пропуск, если нет постера)."""
    return _payload_to_record(movielens_id, payload)


def _movie_params(api_key: str, language: str) -> dict:
    return {"api_key": api_key, "append_to_response": "credits", "language": language}


# --------------------------------------------------------------------------- #
# Асинхронный клиент (основной путь для массовых скриптов)
# --------------------------------------------------------------------------- #
async def fetch_movie_async(
    session: aiohttp.ClientSession, tmdb_id: int, api_key: str, language: str
) -> dict | None:
    """GET одного фильма TMDB (с credits) через ``aiohttp``; транзиентные сбои ретраятся."""
    url = f"{TMDB_API_BASE}/movie/{tmdb_id}"
    params = _movie_params(api_key, language)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, params=params, timeout=timeout) as response:
                if response.status == 200:
                    return await response.json()
                if response.status == 404:
                    logger.debug("TMDB id %s не найден (404) — пропуск", tmdb_id)
                    return None
                if response.status in (429, 500, 502, 503, 504):
                    logger.warning("TMDB id %s вернул %s, попытка %s/%s",
                                   tmdb_id, response.status, attempt, MAX_RETRIES)
                else:
                    logger.error("TMDB id %s: неожиданный статус %s — пропуск",
                                 tmdb_id, response.status)
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            logger.warning("TMDB id %s: сетевая ошибка (%s), попытка %s/%s",
                           tmdb_id, error, attempt, MAX_RETRIES)

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF ** attempt)

    logger.error("TMDB id %s: не удалось получить данные после %s попыток",
                 tmdb_id, MAX_RETRIES)
    return None


def load_links(path: str) -> dict[int, int]:
    """Прочитать ``links.csv`` и вернуть отображение ``movieId -> tmdbId``.

    Строки без ``tmdbId`` пропускаются. Источник маппинга — официальный файл
    MovieLens, поэтому ``Movie.id`` действительно остаётся movieId.
    """
    mapping: dict[int, int] = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            tmdb_id = (row.get("tmdbId") or "").strip()
            if not tmdb_id:
                continue
            mapping[int(row["movieId"])] = int(tmdb_id)
    logger.info("links.csv: %s связей MovieLens→TMDB", len(mapping))
    return mapping
