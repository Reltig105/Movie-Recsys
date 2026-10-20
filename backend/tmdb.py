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
import unicodedata
from dataclasses import dataclass, replace

import aiohttp

logger = logging.getLogger("tmdb")

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w780"
DEFAULT_LANGUAGE = "ru-RU"
# Резервная локаль для описаний: у многих фильмов нет русского overview.
FALLBACK_LANGUAGE = "en-US"
# Локаль поиска: названия в MovieLens английские. В других локалях TMDB кладёт в
# ``title`` локализованное имя, и английское совпадение теряется (пример: «So Be
# It» в ru-RU приходит как «เอวังฯ»), поэтому ищем всегда в en-US, а детали
# запрашиваем уже в ``DEFAULT_LANGUAGE``.
SEARCH_LANGUAGE = "en-US"
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
    """Flat, DB-ready representation of one TMDB record.

    ``poster_url`` может быть ``None``: у части записей TMDB постера нет вовсе,
    но остальные поля (описание, режиссёр, актёры) всё равно полезны.
    """

    id: int
    title: str
    year: int | None
    genres: str
    poster_url: str | None
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


def _clean_title(title: str) -> str:
    """Убрать хвостовой год MovieLens: ``«Матрица (1999)»`` → ``«Матрица»``."""
    return re.sub(r"\s*\(\d{4}\)\s*$", "", (title or "").strip()).strip()


def _normalize_title(title: str) -> str:
    """Нормализовать название для строгого сравнения (регистр, знаки, артикли)."""
    normalized = unicodedata.normalize("NFKD", _clean_title(title).lower())
    normalized = re.sub(r"[^0-9a-zа-яё]+", " ", normalized).strip()
    normalized = re.sub(r"^(the|a|an|der|die|das|le|la|les|el|il|lo)\s+", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _payload_to_record(movielens_id: int, payload: dict) -> MovieRecord:
    """Разбор ответа TMDB в :class:`MovieRecord`.

    Постер необязателен: у части записей TMDB его нет, и раньше такие фильмы
    пропускались целиком вместе с описанием, режиссёром и актёрами. Теперь
    пишется всё, что есть, а ``poster_url`` остаётся ``None``.
    """
    poster_path = payload.get("poster_path")

    release_date = payload.get("release_date") or ""
    year = int(release_date[:4]) if release_date[:4].isdigit() else None
    genres = ", ".join(
        genre["name"] for genre in (payload.get("genres") or []) if genre.get("name")
    )
    credits = payload.get("credits") or {}
    title = payload.get("title") or payload.get("original_title") or f"Movie {movielens_id}"
    poster_url = (
        _clip(f"{TMDB_IMAGE_BASE}{poster_path}", POSTER_MAX, "poster_url", movielens_id)
        if poster_path
        else None
    )

    return MovieRecord(
        id=movielens_id,
        title=_clip(title, TITLE_MAX, "title", movielens_id),
        year=year,
        genres=_clip(genres, GENRES_MAX, "genres", movielens_id),
        poster_url=poster_url,
        synopsis=truncate_synopsis(payload.get("overview") or ""),
        director=_clip(extract_director(credits), DIRECTOR_MAX, "director", movielens_id),
        cast_members=_clip(extract_cast(credits), CAST_MAX, "cast_members", movielens_id),
    )


def parse_movie(movielens_id: int, payload: dict) -> MovieRecord:
    """Разбор ответа TMDB → :class:`MovieRecord` (постер может отсутствовать)."""
    return _payload_to_record(movielens_id, payload)


def merge_records(primary: MovieRecord, extra: MovieRecord) -> MovieRecord:
    """Дополнить запись недостающими полями из альтернативной записи TMDB.

    Название всегда берётся из ``primary`` — перевод/название не подменяем.
    """
    return replace(
        primary,
        year=primary.year or extra.year,
        genres=primary.genres or extra.genres,
        poster_url=primary.poster_url or extra.poster_url,
        synopsis=primary.synopsis or extra.synopsis,
        director=primary.director or extra.director,
        cast_members=primary.cast_members or extra.cast_members,
    )


def _movie_params(api_key: str, language: str, *, with_credits: bool = True) -> dict:
    params = {"api_key": api_key, "language": language}
    if with_credits:
        params["append_to_response"] = "credits"
    return params


# --------------------------------------------------------------------------- #
# Асинхронный клиент (основной путь для массовых скриптов)
# --------------------------------------------------------------------------- #
async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict,
    *,
    tag: str,
) -> tuple[dict | None, bool]:
    """GET с ретраями на 429/5xx.

    Возвращает ``(payload, definitive)``:

    * ``definitive=True`` — TMDB дал окончательный ответ (200/404/4xx): повторять
      запрос бессмысленно, фильм можно помечать проверенным;
    * ``definitive=False`` — сеть/таймаут/исчерпаны попытки: запись не помечаем,
      чтобы добор не «залип» навсегда из-за разового сбоя.
    """
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, params=params, timeout=timeout) as response:
                if response.status == 200:
                    return await response.json(), True
                if response.status == 404:
                    logger.debug("%s: не найдено (404) — пропуск", tag)
                    return None, True
                if response.status in (429, 500, 502, 503, 504):
                    logger.warning("%s вернул %s, попытка %s/%s",
                                   tag, response.status, attempt, MAX_RETRIES)
                else:
                    logger.error("%s: неожиданный статус %s — пропуск",
                                 tag, response.status)
                    return None, True
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            logger.warning("%s: сетевая ошибка (%s), попытка %s/%s",
                           tag, error, attempt, MAX_RETRIES)

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF ** attempt)

    logger.error("%s: не удалось получить данные после %s попыток", tag, MAX_RETRIES)
    return None, False


async def fetch_movie_result(
    session: aiohttp.ClientSession,
    tmdb_id: int,
    api_key: str,
    language: str,
    fallback_language: str | None = None,
) -> tuple[dict | None, bool]:
    """То же, что :func:`fetch_movie_async`, но с признаком окончательности ответа."""
    payload, definitive = await _get_json(
        session,
        f"{TMDB_API_BASE}/movie/{tmdb_id}",
        _movie_params(api_key, language),
        tag=f"TMDB id {tmdb_id}",
    )
    if payload is None:
        return None, definitive

    if (
        fallback_language
        and fallback_language != language
        and not (payload.get("overview") or "").strip()
    ):
        fallback, _ = await _get_json(
            session,
            f"{TMDB_API_BASE}/movie/{tmdb_id}",
            _movie_params(api_key, fallback_language, with_credits=False),
            tag=f"TMDB id {tmdb_id} ({fallback_language})",
        )
        overview = (fallback or {}).get("overview") or ""
        if overview.strip():
            payload = {**payload, "overview": overview}

    return payload, definitive


async def fetch_movie_async(
    session: aiohttp.ClientSession,
    tmdb_id: int,
    api_key: str,
    language: str,
    fallback_language: str | None = None,
) -> dict | None:
    """GET одного фильма TMDB (с credits) через ``aiohttp``; сбои ретраятся.

    Если в запрошенной локали нет ``overview`` (частый случай для ru-RU), а
    ``fallback_language`` задан, выполняется облегчённый запрос в резервной
    локали и из него берётся **только** описание. Название и метаданные
    остаются в основной локали.
    """
    payload, _ = await fetch_movie_result(
        session, tmdb_id, api_key, language, fallback_language=fallback_language
    )
    return payload


def _pick_search_match(results: list[dict], title: str, year: int | None) -> dict | None:
    """Выбрать из результатов поиска строгое совпадение по названию и году."""
    target = _normalize_title(title)
    if not target:
        return None

    fallback: dict | None = None
    for item in results[:10]:
        names = {
            _normalize_title(item.get("title") or ""),
            _normalize_title(item.get("original_title") or ""),
        }
        if target not in names:
            continue

        item_year = (item.get("release_date") or "")[:4]
        if year and item_year and item_year != str(year):
            continue

        if item.get("poster_path"):
            return item
        fallback = fallback or item

    return fallback


async def search_movie_result(
    session: aiohttp.ClientSession,
    title: str,
    year: int | None,
    api_key: str,
    language: str,
    *,
    fallback_language: str | None = None,
) -> tuple[dict | None, bool]:
    """Найти дубликат записи TMDB по названию и году и вернуть её ``/movie/{id}``.

    Нужен для фильмов, где ``poster_url`` пуст: у MovieLens ``links.csv`` иногда
    указан «пустой» дубликат TMDB, тогда как соседняя запись с тем же названием и
    годом имеет постер и описание. Совпадение строгое (название + год), чтобы не
    подставить постер чужого фильма.
    """
    query = _clean_title(title)
    if not query:
        return None, True

    params = {"api_key": api_key, "query": query, "language": SEARCH_LANGUAGE}
    if year:
        params["year"] = year

    found, definitive = await _get_json(
        session, f"{TMDB_API_BASE}/search/movie", params, tag=f"поиск «{query}»"
    )
    if found is None:
        return None, definitive

    match = _pick_search_match(found.get("results") or [], title, year)
    if match is None:
        return None, True

    tmdb_id = match.get("id")
    if not tmdb_id:
        return None, True

    payload, payload_definitive = await fetch_movie_result(
        session, tmdb_id, api_key, language, fallback_language=fallback_language
    )
    return payload, definitive and payload_definitive



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
