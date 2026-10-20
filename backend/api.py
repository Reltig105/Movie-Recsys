"""REST API Rexter — FastAPI + PostgreSQL (async SQLAlchemy 2.0).

Единый бэкенд сервиса: аутентификация, оценки, поиск, лента, рекомендации и
профиль. Все обращения к БД асинхронные (драйвер ``asyncpg``); схема создаётся
автоматически на старте приложения.

Эндпоинты:

* ``GET  /health``                   — health-check;
* ``GET  /api/v1/movies``            — первые N фильмов (смоук-тест данных);
* ``POST /register``                 — регистрация;
* ``POST /login``                    — вход;
* ``GET  /user/{user_id}``           — профиль пользователя;
* ``POST /rate``                     — сохранить оценку;
* ``GET  /user/{user_id}/history``   — история оценок;
* ``GET  /search?query=``            — поиск фильмов по названию;
* ``GET  /recommend/{user_id}``      — рекомендации (hot/cold start);
* ``GET  /api/v1/calibration-movies``— фильмы для онбординга;
* ``POST /api/v1/rate``              — оценка из v1-фронтенда;
* ``GET  /api/v1/feed``              — лента-карусель;
* ``GET  /api/v1/profile``           — профиль (статистика/жанры/история).

Запуск::

    docker compose up -d
    uvicorn backend.api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import random
import secrets
import time
from contextlib import asynccontextmanager
from typing import List
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import dispose, get_session, init_db
from .models import Movie, Rating, User, movie_stats

logger = logging.getLogger(__name__)

MAX_LIMIT = 100
_PBKDF2_ITERATIONS = 200_000

# Лента читает только ``movies`` + предрасчитанную ``movie_stats`` (без ratings).
FEED_MIN_RATINGS = 20
FEED_POOL_SIZE = 60

#: Временно: фильмы без постера не попадают в рекомендации (лента, калибровка,
#: ``/recommend``) — вместо карточки с плейсхолдером лучше показать другой фильм.
#: Из каталога они не исчезают: поиск, история оценок и профиль их видят.
#: Чтобы вернуть их обратно, достаточно убрать это условие из трёх запросов ниже.
RECOMMENDABLE = Movie.poster_url.is_not(None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Создать схему на старте и закрыть пул соединений на остановке."""
    await init_db()
    yield
    await dispose()


app = FastAPI(title="Rexter", version="4.0.0", lifespan=lifespan)

# CORS: разрешаем запросы с фронтенда (Vite/React dev-сервер и др.).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Схемы данных (контракт API)
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    """Данные для регистрации нового пользователя."""

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    age: int = Field(25, ge=0, le=120)
    gender: str = "unknown"
    occupation: str = "unknown"


class RegisterResponse(BaseModel):
    """Ответ на успешную регистрацию."""

    user_id: int
    username: str


class LoginRequest(BaseModel):
    """Данные для входа."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Ответ на успешный вход."""

    user_id: int
    username: str


class UserProfile(BaseModel):
    """Профиль пользователя (признаки для ML)."""

    user_id: int
    username: str | None = None
    age: int | None = None
    gender: str | None = None
    occupation: str | None = None


class Recommendation(BaseModel):
    """Одна рекомендация фильма (ML data contract)."""

    movie_id: int
    title: str
    genres: str
    score: float


class RateRequest(BaseModel):
    """Оценка фильма пользователем."""

    user_id: int
    movie_id: int
    rating: float = Field(..., ge=1, le=5)


class HistoryEntry(BaseModel):
    """Одна запись в истории оценок пользователя."""

    movie_id: int
    title: str
    rating: float


class SearchResult(BaseModel):
    """Один фильм в результатах поиска по названию."""

    movie_id: int
    title: str
    genres: str


class MovieOut(BaseModel):
    """Фильм для смоук-эндпоинта ``/api/v1/movies``."""

    model_config = {"from_attributes": True}

    id: int
    title: str
    year: int | None = None
    genres: str | None = None
    poster_url: str | None = None
    synopsis: str | None = None
    director: str | None = None
    cast_members: str | None = None


class CalibrationMovie(BaseModel):
    """Фильм для онбординга (калибровка холодного старта)."""

    id: int
    title: str
    year: int | None
    genres: str
    poster_url: str


class FeedMovie(BaseModel):
    """Фильм в главной карусели рекомендаций."""

    id: int
    title: str
    year: int | None
    genres: str
    poster_url: str
    score: float
    synopsis: str = ""


class ProfileMovie(BaseModel):
    """Фильм из истории оценок пользователя."""

    id: int
    title: str
    year: int | None
    genres: str
    poster_url: str
    rating: float | None


class ProfileResponse(BaseModel):
    """Профиль пользователя: статистика, любимые жанры, история оценок."""

    user_id: int
    name: str
    watched_count: int
    hours: int
    favorite_genres: List[str]
    history: List[ProfileMovie]


class RateV1Request(BaseModel):
    """Оценка из фронтенда v1 (онбординг/карусель)."""

    movie_id: int
    rating: int = Field(..., ge=1, le=5)
    user_id: int = 1


# --------------------------------------------------------------------------- #
# Хеширование паролей (PBKDF2-HMAC-SHA256, только stdlib)
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Вернуть строку-хранилище ``pbkdf2_sha256$iters$salt_b64$hash_b64``."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}"
        f"${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Проверить пароль по строке-хранилищу (constant-time сравнение)."""
    if not stored:
        return False
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations)
    )
    return hmac.compare_digest(digest, expected)


def _poster_url(title: str, poster: str | None) -> str:
    """Постер фильма или плейсхолдер в тёплой палитре проекта."""
    if poster:
        return poster
    return f"https://placehold.co/300x450/F3EFE9/6B4F3A/png?text={quote_plus(title)}"


# --------------------------------------------------------------------------- #
# Запросы к БД
# --------------------------------------------------------------------------- #
async def _fetch_user(session: AsyncSession, user_id: int) -> User:
    """Получить пользователя или бросить 404."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


async def _popular_ranked(
    session: AsyncSession, min_ratings: int = 1, limit: int = 1000
) -> list[tuple[Movie, float, int]]:
    """Топ фильмов из предрасчитанной ``movie_stats``: ``[(Movie, avg, count), ...]``.

    Агрегация по ``ratings`` здесь **не выполняется**: тяжёлый ``GROUP BY`` по
    32M строк считается заранее в :mod:`backend.refresh_stats`, API читает
    готовые ``(avg_rating, ratings_count)``.
    """
    statement = (
        select(Movie, movie_stats.c.avg_rating, movie_stats.c.ratings_count)
        .join(movie_stats, movie_stats.c.movie_id == Movie.id)
        .where(RECOMMENDABLE, movie_stats.c.ratings_count >= min_ratings)
        .order_by(
            movie_stats.c.avg_rating.desc(),
            movie_stats.c.ratings_count.desc(),
        )
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [(movie, float(score), int(count)) for movie, score, count in rows]


async def _popular_movies(
    session: AsyncSession,
    top_k: int,
    min_ratings: int = 1,
    exclude_user_id: int | None = None,
    fallback_to_catalog: bool = True,
) -> list[tuple[Movie, float, int]]:
    """Топ популярных фильмов, опционально исключая уже оценённые пользователем.

    ``fallback_to_catalog`` дополняет выдачу фильмами без достаточного числа
    оценок (по убыванию года), чтобы холодный каталог никогда не отдавал пустой
    список — иначе лента/калибровка были бы пустыми до накопления оценок.
    """
    rated: set[int] = set()
    if exclude_user_id is not None:
        rated = set(
            (
                await session.scalars(
                    select(Rating.movie_id).where(Rating.user_id == exclude_user_id)
                )
            ).all()
        )

    ranked = await _popular_ranked(session, min_ratings=min_ratings)

    result: list[tuple[Movie, float, int]] = []
    for movie, score, count in ranked:
        if movie.id in rated:
            continue
        result.append((movie, score, count))
        if len(result) >= top_k:
            break

    if fallback_to_catalog and len(result) < top_k:
        seen = rated | {movie.id for movie, _, _ in result}
        catalog = await session.scalars(
            select(Movie)
            .where(RECOMMENDABLE)
            .order_by(Movie.year.is_(None), Movie.year.desc(), Movie.id)
        )
        for movie in catalog:
            if movie.id in seen:
                continue
            result.append((movie, 0.0, 0))
            if len(result) >= top_k:
                break

    return result[:top_k]


async def _upsert_rating(
    session: AsyncSession, user_id: int, movie_id: int, score: int
) -> None:
    """Вставить или обновить оценку (портативно, без ON CONFLICT)."""
    rating = await session.scalar(
        select(Rating).where(Rating.user_id == user_id, Rating.movie_id == movie_id)
    )
    if rating is None:
        session.add(
            Rating(
                user_id=user_id,
                movie_id=movie_id,
                score=score,
                timestamp=int(time.time()),
            )
        )
    else:
        rating.score = score
        rating.timestamp = int(time.time())
    await session.commit()


async def _ratings_count(session: AsyncSession, user_id: int) -> int:
    """Сколько оценок у пользователя."""
    count = await session.scalar(
        select(func.count()).select_from(Rating).where(Rating.user_id == user_id)
    )
    return int(count or 0)


# --------------------------------------------------------------------------- #
# Мок-каталог (заменяется реальной ML-моделью на следующем этапе)
# --------------------------------------------------------------------------- #
_MOCK_CATALOG: list[Recommendation] = [
    Recommendation(movie_id=2571, title="The Matrix", genres="Action", score=4.8),
    Recommendation(movie_id=58559, title="The Dark Knight", genres="Crime", score=4.7),
    Recommendation(movie_id=79132, title="Inception", genres="Sci-Fi", score=4.6),
    Recommendation(movie_id=2959, title="Fight Club", genres="Drama", score=4.5),
    Recommendation(movie_id=109487, title="Interstellar", genres="Sci-Fi", score=4.4),
    Recommendation(movie_id=4226, title="Memento", genres="Mystery", score=4.2),
]


# --------------------------------------------------------------------------- #
# Эндпоинты: служебные и данные
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict[str, str]:
    """Health-check для мониторинга и фронтенда."""
    return {"status": "ok"}


@app.get("/api/v1/movies", response_model=list[MovieOut])
async def list_movies(
    limit: int = 5, session: AsyncSession = Depends(get_session)
) -> list[Movie]:
    """Вернуть первые ``limit`` фильмов по возрастанию MovieLens id (по умолчанию 5)."""
    bounded_limit = max(1, min(int(limit), MAX_LIMIT))
    result = await session.scalars(select(Movie).order_by(Movie.id).limit(bounded_limit))
    return list(result)


# --------------------------------------------------------------------------- #
# Эндпоинты: аутентификация
# --------------------------------------------------------------------------- #
@app.post("/register", response_model=RegisterResponse)
async def register(
    payload: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> RegisterResponse:
    """Зарегистрировать пользователя."""
    exists = await session.scalar(select(User.id).where(User.username == payload.username))
    if exists is not None:
        raise HTTPException(
            status_code=400, detail="Пользователь с таким логином уже существует"
        )

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        age=payload.age,
        gender=payload.gender,
        occupation=payload.occupation,
        session_token=secrets.token_hex(16),
    )
    session.add(user)
    await session.commit()
    return RegisterResponse(user_id=user.id, username=payload.username)


@app.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_session)
) -> LoginResponse:
    """Проверить пароль и вернуть ``user_id``/``username``."""
    user = await session.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    user.session_token = secrets.token_hex(16)
    await session.commit()
    return LoginResponse(user_id=user.id, username=user.username or payload.username)


@app.get("/user/{user_id}", response_model=UserProfile)
async def get_user(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> UserProfile:
    """Вернуть профиль пользователя (age/gender/occupation)."""
    user = await _fetch_user(session, user_id)
    return UserProfile(
        user_id=user.id,
        username=user.username,
        age=user.age,
        gender=user.gender,
        occupation=user.occupation,
    )


# --------------------------------------------------------------------------- #
# Эндпоинты: оценки, история, поиск
# --------------------------------------------------------------------------- #
@app.post("/rate")
async def rate(
    payload: RateRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    """Сохранить оценку фильма (upsert по ``(user_id, movie_id)``)."""
    await _upsert_rating(
        session, payload.user_id, payload.movie_id, int(round(payload.rating))
    )
    return {"status": "success"}


@app.get("/user/{user_id}/history", response_model=list[HistoryEntry])
async def get_history(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> list[HistoryEntry]:
    """История оценок пользователя (join ``ratings`` и ``movies``)."""
    rows = (
        await session.execute(
            select(Rating.movie_id, Movie.title, Rating.score)
            .join(Movie, Movie.id == Rating.movie_id)
            .where(Rating.user_id == user_id)
            .order_by(func.coalesce(Rating.timestamp, 0).desc())
        )
    ).all()
    return [
        HistoryEntry(movie_id=int(movie_id), title=title, rating=float(score))
        for movie_id, title, score in rows
    ]


@app.get("/search", response_model=list[SearchResult])
async def search(
    query: str, limit: int = 20, session: AsyncSession = Depends(get_session)
) -> list[SearchResult]:
    """Найти фильмы по подстроке в названии (регистронезависимо)."""
    pattern = f"%{query}%"
    bounded_limit = max(1, min(int(limit), MAX_LIMIT))
    movies = await session.scalars(
        select(Movie)
        .where(Movie.title.ilike(pattern))
        .order_by(Movie.title)
        .limit(bounded_limit)
    )
    return [
        SearchResult(movie_id=movie.id, title=movie.title, genres=movie.genres or "")
        for movie in movies
    ]


# --------------------------------------------------------------------------- #
# Эндпоинты: рекомендации
# --------------------------------------------------------------------------- #
@app.get("/recommend/{user_id}", response_model=list[Recommendation])
async def recommend(
    user_id: int, top_k: int = 12, session: AsyncSession = Depends(get_session)
) -> list[Recommendation]:
    """Рекомендации с автоопределением hot/cold start.

    Есть оценки — hot start (пока мок ML); иначе cold start (SQL-топ по среднему).
    """
    await _fetch_user(session, user_id)
    top_k = max(1, int(top_k))

    if await _ratings_count(session, user_id) > 0:
        logger.info("Hot Start (SVD) для user_id=%s", user_id)
        return _MOCK_CATALOG[: max(1, min(top_k, len(_MOCK_CATALOG)))]

    logger.info("Cold Start (SQL) для user_id=%s", user_id)
    rows = await _popular_movies(session, top_k=top_k, min_ratings=10)
    return [
        Recommendation(
            movie_id=movie.id, title=movie.title, genres=movie.genres or "", score=score
        )
        for movie, score, _ in rows
    ]


# --------------------------------------------------------------------------- #
# API v1: онбординг и лента-карусель
# --------------------------------------------------------------------------- #
@app.get("/api/v1/calibration-movies", response_model=list[CalibrationMovie])
async def api_calibration_movies(
    limit: int = 10, session: AsyncSession = Depends(get_session)
) -> list[CalibrationMovie]:
    """Популярные фильмы для калибровки нового пользователя."""
    limit = max(1, int(limit))
    rows = await _popular_movies(session, top_k=limit, min_ratings=20)
    return [
        CalibrationMovie(
            id=movie.id,
            title=movie.title,
            year=movie.year,
            genres=movie.genres or "",
            poster_url=_poster_url(movie.title, movie.poster_url),
        )
        for movie, _, _ in rows
    ]


@app.post("/api/v1/rate")
async def api_rate(
    payload: RateV1Request, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    """Сохранить оценку из v1-фронтенда (upsert по ``(user_id, movie_id)``)."""
    await _upsert_rating(session, payload.user_id, payload.movie_id, int(payload.rating))
    return {"status": "success"}


@app.get("/api/v1/feed", response_model=list[FeedMovie])
async def api_feed(
    user_id: int = 1,
    top_k: int = 7,
    seed: int | None = None,
    exclude_ids: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[FeedMovie]:
    """``top_k`` фильмов для главной карусели (стабильный топ либо перемешанный).

    Читает только ``movies`` и предрасчитанную ``movie_stats`` — таблица
    ``ratings`` (32M строк) не затрагивается вообще. ``user_id`` сохранён в
    контракте для совместимости; уже показанные фильмы клиент скрывает через
    ``exclude_ids``.
    """
    top_k = max(1, int(top_k))
    pool_size = max(top_k, FEED_POOL_SIZE)

    statement = (
        select(Movie, movie_stats.c.avg_rating, movie_stats.c.ratings_count)
        .join(movie_stats, movie_stats.c.movie_id == Movie.id)
        .where(RECOMMENDABLE, movie_stats.c.ratings_count >= FEED_MIN_RATINGS)
        .order_by(
            movie_stats.c.avg_rating.desc(),
            movie_stats.c.ratings_count.desc(),
        )
        .limit(pool_size)
    )
    rows = (await session.execute(statement)).all()
    pool = [(movie, float(score), int(count)) for movie, score, count in rows]

    if exclude_ids:
        hidden = {int(part) for part in exclude_ids.split(",") if part.strip().isdigit()}
        visible = [row for row in pool if row[0].id not in hidden]
        # Если скрыто слишком много — отдаём пул без фильтра, чтобы не вернуть пусто.
        if len(visible) >= top_k:
            pool = visible

    if seed is not None and len(pool) > top_k:
        selected = random.Random(seed).sample(pool, top_k)
        selected.sort(key=lambda row: (row[1], row[2]), reverse=True)
    else:
        selected = pool[:top_k]

    return [
        FeedMovie(
            id=movie.id,
            title=movie.title,
            year=movie.year,
            genres=movie.genres or "",
            poster_url=_poster_url(movie.title, movie.poster_url),
            score=score,
            synopsis=movie.synopsis or "",
        )
        for movie, score, _ in selected
    ]


@app.get("/api/v1/profile", response_model=ProfileResponse)
async def api_profile(
    user_id: int = 1, session: AsyncSession = Depends(get_session)
) -> ProfileResponse:
    """Профиль пользователя: статистика, любимые жанры и история оценок."""
    user = await session.get(User, user_id)
    name = user.username if user and user.username else "Пользователь"

    rows = (
        await session.execute(
            select(Movie, Rating.score)
            .join(Rating, Rating.movie_id == Movie.id)
            .where(Rating.user_id == user_id)
            .order_by(func.coalesce(Rating.timestamp, 0).desc())
            .limit(20)
        )
    ).all()

    history = [
        ProfileMovie(
            id=movie.id,
            title=movie.title,
            year=movie.year,
            genres=movie.genres or "",
            poster_url=_poster_url(movie.title, movie.poster_url),
            rating=float(score) if score is not None else None,
        )
        for movie, score in rows
    ]

    genre_counts: dict[str, int] = {}
    for movie in history:
        for genre in (movie.genres or "").split(","):
            genre = genre.strip()
            if genre:
                genre_counts[genre] = genre_counts.get(genre, 0) + 1

    favorite_genres = [
        genre for genre, _ in sorted(genre_counts.items(), key=lambda item: -item[1])[:5]
    ]
    if not favorite_genres:
        favorite_genres = ["Фантастика", "Детективы", "Драма"]

    watched_count = await _ratings_count(session, user_id)

    return ProfileResponse(
        user_id=user_id,
        name=name,
        watched_count=watched_count,
        hours=round(watched_count * 1.5),
        favorite_genres=favorite_genres,
        history=history,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
