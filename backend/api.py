"""REST API Rexter — FastAPI + PostgreSQL (async SQLAlchemy 2.0).

Единый бэкенд сервиса: аутентификация, оценки, поиск, лента, рекомендации и
профиль. Все обращения к БД асинхронные (драйвер ``asyncpg``); схема создаётся
автоматически на старте приложения.

Эндпоинты:

* ``GET  /health``                   — health-check;
* ``GET  /api/v1/movies``            — первые N фильмов (смоук-тест данных);
* ``POST /register``                 — регистрация (возвращает токен сессии);
* ``POST /login``                    — вход (возвращает токен сессии);
* ``POST /logout``                   — выход (аннулирует токен);
* ``GET  /user/{user_id}``           — профиль пользователя;
* ``POST /rate``                     — сохранить оценку;
* ``GET  /user/{user_id}/history``   — история оценок;
* ``GET  /search?query=``            — поиск фильмов по названию;
* ``GET  /recommend/{user_id}``      — рекомендации (hot/cold start);
* ``GET  /api/v1/calibration-movies``— фильмы для онбординга;
* ``POST /api/v1/rate``              — оценка из v1-фронтенда;
* ``GET  /api/v1/feed``              — лента-карусель (с фильтрами);
* ``GET  /api/v1/filters``           — страны, жанры и годы для фильтров;
* ``GET  /api/v1/profile``           — профиль (статистика/жанры/история).

Запись оценок и личные данные (профиль, история) требуют заголовок
``X-Session-Token`` с токеном, который выдают ``/register`` и ``/login``.
Каталог (лента, рекомендации, поиск) открыт без авторизации.

В продакшене тот же процесс отдаёт собранный фронтенд из ``frontend/dist``
(SPA-fallback), поэтому CORS не нужен: браузер и API живут на одном origin.

Запуск::

    docker compose up -d
    uvicorn backend.api:app --host 127.0.0.1 --port 8000 --proxy-headers
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import random
import re
import secrets
import time
from collections import Counter, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .countries import country_label
from .database import SessionLocal, dispose, get_session, init_db
from .models import Movie, Rating, User, WatchlistItem, movie_stats
from .recommender import RecommenderEngine

logger = logging.getLogger(__name__)

# Диагностика (загрузка эмбеддингов, Redis, холодный старт) должна попадать в
# journalctl, а не теряться на уровне WARNING.
logging.basicConfig(
    level=os.getenv("REXTER_LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s | %(name)s | %(message)s",
)

MAX_LIMIT = 100
_PBKDF2_ITERATIONS = 200_000

#: Каталог с артефактами LightFM (``item_embeddings.npy``, ``faiss.index`` …).
ARTIFACTS_DIR = Path(
    os.getenv(
        "REXTER_ARTIFACTS_DIR",
        str(Path(__file__).resolve().parent.parent / "data" / "artifacts"),
    )
)

#: Кулдаун показов. Пустая строка отключает Redis (fallback в память).
REDIS_URL = os.getenv("REXTER_REDIS_URL", "redis://127.0.0.1:6379/0")
COOLDOWN_TTL = int(os.getenv("REXTER_COOLDOWN_TTL", str(24 * 60 * 60)))

#: Загруженный движок рекомендаций (``None`` — артефактов нет, работаем на топе).
_ENGINE: RecommenderEngine | None = None

#: Фильмы без постера: в каталоге остаются, в выдачу не попадают.
_UNAVAILABLE: frozenset[int] = frozenset()

#: Заголовок с токеном сессии (его выдают ``/register`` и ``/login``).
SESSION_HEADER = "X-Session-Token"

#: Ограничение попыток входа/регистрации: не более N за окно (секунды) с одного IP.
_AUTH_LIMIT = 10
_AUTH_WINDOW = 60.0

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
    """Создать схему, загрузить ML-артефакты и закрыть ресурсы на остановке."""
    global _ENGINE, _UNAVAILABLE

    await init_db()
    async with SessionLocal() as session:
        posterless = await session.scalars(
            select(Movie.id).where(Movie.poster_url.is_(None))
        )
        _UNAVAILABLE = frozenset(posterless.all())

    try:
        _ENGINE = await RecommenderEngine.load(ARTIFACTS_DIR, REDIS_URL, COOLDOWN_TTL)
    except Exception:  # noqa: BLE001 — без модели сервис должен подняться
        logger.exception("Не удалось загрузить ML-артефакты — работаем на популярном")
        _ENGINE = None
    yield

    if _ENGINE is not None:
        await _ENGINE.cooldown.close()
    await dispose()


app = FastAPI(title="Rexter", version="4.0.0", lifespan=lifespan)

# CORS нужен только если фронтенд живёт на другом origin. Штатный режим —
# один origin: статику и API отдаёт этот же процесс, а в dev запросы идут через
# прокси Vite. Список источников задаётся через ``REXTER_ALLOWED_ORIGINS``
# (через запятую); по умолчанию middleware не подключается.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("REXTER_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# --------------------------------------------------------------------------- #
# Схемы данных (контракт API)
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    """Данные для регистрации нового пользователя."""

    username: str = Field(..., min_length=1, max_length=128)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1)
    age: int = Field(25, ge=0, le=120)
    gender: str = "unknown"
    occupation: str = "unknown"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """Привести почту к нижнему регистру и проверить базовый формат."""
        email = value.strip().lower()
        local, _, domain = email.partition("@")
        if not local or not domain or "." not in domain:
            raise ValueError("Некорректный адрес почты")
        return email


class RegisterResponse(BaseModel):
    """Ответ на успешную регистрацию."""

    user_id: int
    username: str
    session_token: str


class LoginRequest(BaseModel):
    """Данные для входа."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Ответ на успешный вход."""

    user_id: int
    username: str
    session_token: str


class UserProfile(BaseModel):
    """Профиль пользователя (признаки для ML)."""

    user_id: int
    username: str | None = None
    email: str | None = None
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
    country: str | None = None
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


class CountryOption(BaseModel):
    """Страна производства в списке фильтров."""

    code: str
    name: str
    count: int


class FeedFilters(BaseModel):
    """Доступные значения фильтров для ленты."""

    countries: list[CountryOption]
    genres: list[str]
    year_min: int | None
    year_max: int | None


class ProfileMovie(BaseModel):
    """Фильм из истории оценок пользователя."""

    id: int
    title: str
    year: int | None
    genres: str
    poster_url: str
    rating: float | None


class WatchlistMovie(BaseModel):
    """Фильм из списка «Хочу посмотреть»."""

    id: int
    title: str
    year: int | None
    genres: str
    poster_url: str
    rating: float | None = None


class WatchlistRequest(BaseModel):
    """Добавление фильма в список «Хочу посмотреть»."""

    movie_id: int


class ProfileResponse(BaseModel):
    """Профиль пользователя: статистика, любимые жанры, история оценок."""

    user_id: int
    name: str
    email: str | None = None
    watched_count: int
    hours: int
    favorite_genres: List[str]
    history: List[ProfileMovie]
    watchlist: List[WatchlistMovie] = Field(default_factory=list)


class PasswordChangeRequest(BaseModel):
    """Смена пароля авторизованного пользователя."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)


class PasswordChangeResponse(BaseModel):
    """Ответ после смены пароля: новый токен текущей сессии."""

    status: str
    session_token: str


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


_TMDB_IMAGE_SIZE_RE = re.compile(r"/t/p/(?:original|w\d+)/")


def _poster_url(title: str, poster: str | None) -> str:
    """Постер фильма или плейсхолдер в тёплой палитре проекта.

    Карточки в интерфейсе занимают около 260 px, поэтому отдаём постер
    шириной 342 px вместо исходных 780 px: файл примерно в 4 раза легче,
    а на экране разница не заметна.
    """
    if poster:
        if "image.tmdb.org/t/p/" in poster:
            return _TMDB_IMAGE_SIZE_RE.sub("/t/p/w342/", poster, count=1)
        return poster
    return f"https://placehold.co/300x450/F3EFE9/6B4F3A/png?text={quote_plus(title)}"


# --------------------------------------------------------------------------- #
# Сессии и антибрутфорс
# --------------------------------------------------------------------------- #
_auth_attempts: dict[str, deque[float]] = {}


def _limit_auth_attempts(request: Request) -> None:
    """Не более ``_AUTH_LIMIT`` попыток входа/регистрации за окно с одного IP.

    Счётчик живёт в памяти процесса: он защищает от перебора пароля, а не
    заменяет собой внешний rate-limiter на уровне reverse-proxy. За Caddy нужен
    запуск uvicorn с ``--proxy-headers``, иначе все клиенты будут с IP прокси.
    """
    now = time.monotonic()
    key = request.client.host if request.client else "unknown"
    attempts = _auth_attempts.setdefault(key, deque())
    while attempts and now - attempts[0] > _AUTH_WINDOW:
        attempts.popleft()
    if len(attempts) >= _AUTH_LIMIT:
        raise HTTPException(
            status_code=429, detail="Слишком много попыток, попробуйте через минуту"
        )
    attempts.append(now)
    if len(_auth_attempts) > 10_000:
        stale = [
            item
            for item, times in _auth_attempts.items()
            if not times or now - times[-1] > _AUTH_WINDOW
        ]
        for item in stale[:1000]:
            _auth_attempts.pop(item, None)


async def require_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Пользователь по токену из ``X-Session-Token`` (401, если токен неверен)."""
    token = request.headers.get(SESSION_HEADER, "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    user = await session.scalar(select(User).where(User.session_token == token))
    if user is None:
        raise HTTPException(status_code=401, detail="Сессия истекла, войдите заново")
    return user


def ensure_owner(user: User, user_id: int) -> None:
    """Запретить доступ к данным другого пользователя."""
    if user.id != user_id:
        raise HTTPException(
            status_code=403, detail="Нет доступа к данным другого пользователя"
        )


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


async def _movies_by_ids(
    session: AsyncSession, movie_ids: list[int]
) -> dict[int, Movie]:
    """Фильмы по id (только рекоменуемые) — одним запросом, с сохранением порядка."""
    if not movie_ids:
        return {}
    rows = await session.scalars(
        select(Movie).where(Movie.id.in_(movie_ids), RECOMMENDABLE)
    )
    return {movie.id: movie for movie in rows}


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
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RegisterResponse:
    """Зарегистрировать пользователя и выдать токен сессии."""
    _limit_auth_attempts(request)
    exists = await session.scalar(select(User.id).where(User.username == payload.username))
    if exists is not None:
        raise HTTPException(
            status_code=400, detail="Пользователь с таким логином уже существует"
        )

    email_exists = await session.scalar(
        select(User.id).where(User.email == payload.email)
    )
    if email_exists is not None:
        raise HTTPException(
            status_code=400, detail="Пользователь с такой почтой уже существует"
        )

    token = secrets.token_hex(16)
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        age=payload.age,
        gender=payload.gender,
        occupation=payload.occupation,
        session_token=token,
    )
    session.add(user)
    await session.commit()
    return RegisterResponse(
        user_id=user.id, username=payload.username, session_token=token
    )


@app.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """Проверить пароль и выдать новый токен сессии."""
    _limit_auth_attempts(request)
    user = await session.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    token = secrets.token_hex(16)
    user.session_token = token
    await session.commit()
    return LoginResponse(
        user_id=user.id,
        username=user.username or payload.username,
        session_token=token,
    )


@app.post("/logout")
async def logout(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Аннулировать токен текущей сессии."""
    user.session_token = None
    await session.commit()
    return {"status": "success"}


@app.get("/user/{user_id}", response_model=UserProfile)
async def get_user(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> UserProfile:
    """Вернуть профиль пользователя (age/gender/occupation)."""
    user = await _fetch_user(session, user_id)
    return UserProfile(
        user_id=user.id,
        username=user.username,
        email=user.email,
        age=user.age,
        gender=user.gender,
        occupation=user.occupation,
    )


# --------------------------------------------------------------------------- #
# Эндпоинты: оценки, история, поиск
# --------------------------------------------------------------------------- #
@app.post("/rate")
async def rate(
    payload: RateRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Сохранить оценку фильма (upsert по ``(user_id, movie_id)``)."""
    ensure_owner(user, payload.user_id)
    await _upsert_rating(
        session, payload.user_id, payload.movie_id, int(round(payload.rating))
    )
    return {"status": "success"}


@app.get("/user/{user_id}/history", response_model=list[HistoryEntry])
async def get_history(
    user_id: int,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[HistoryEntry]:
    """История оценок пользователя (join ``ratings`` и ``movies``)."""
    ensure_owner(user, user_id)
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
    """Персональные рекомендации LightFM.

    Обученный пользователь получает топ из FAISS; новый (зарегистрировался
    после обучения) — вектор, собранный из его оценок. Если профиля нет вовсе,
    откатываемся на популярное (cold start).
    """
    await _fetch_user(session, user_id)
    top_k = max(1, min(int(top_k), MAX_LIMIT))

    if _ENGINE is not None:
        items = await _ENGINE.recommend(
            session, user_id, top_k=top_k, unavailable=_UNAVAILABLE
        )
        if items:
            movies = await _movies_by_ids(session, [item.movie_id for item in items])
            result = [
                Recommendation(
                    movie_id=item.movie_id,
                    title=movies[item.movie_id].title,
                    genres=movies[item.movie_id].genres or "",
                    score=item.score,
                )
                for item in items
                if item.movie_id in movies
            ]
            if result:
                return result

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
    payload: RateV1Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Сохранить оценку из v1-фронтенда (upsert по ``(user_id, movie_id)``)."""
    ensure_owner(user, payload.user_id)
    await _upsert_rating(session, payload.user_id, payload.movie_id, int(payload.rating))
    return {"status": "success"}


@app.post("/api/v1/password", response_model=PasswordChangeResponse)
async def api_change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> PasswordChangeResponse:
    """Сменить пароль и выдать новый токен текущей сессии.

    Старый пароль нельзя показать: в базе хранится только PBKDF2-хеш.
    Смена пароля заодно инвалидирует остальные сессии пользователя.
    """
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=400, detail="Новый пароль должен отличаться от текущего"
        )

    token = secrets.token_hex(16)
    user.password_hash = hash_password(payload.new_password)
    user.session_token = token
    await session.commit()
    return PasswordChangeResponse(status="success", session_token=token)


async def _watchlist_movies(
    session: AsyncSession, user_id: int
) -> list[WatchlistMovie]:
    """Фильмы пользователя из «Хочу посмотреть», новые сверху."""
    rows = await session.execute(
        select(Movie, Rating.score)
        .join(WatchlistItem, WatchlistItem.movie_id == Movie.id)
        .outerjoin(
            Rating,
            and_(
                Rating.movie_id == Movie.id,
                Rating.user_id == user_id,
            ),
        )
        .where(WatchlistItem.user_id == user_id)
        .order_by(WatchlistItem.id.desc())
    )
    return [
        WatchlistMovie(
            id=movie.id,
            title=movie.title,
            year=movie.year,
            genres=movie.genres or "",
            poster_url=_poster_url(movie.title, movie.poster_url),
            rating=float(score) if score is not None else None,
        )
        for movie, score in rows
    ]


@app.get("/api/v1/watchlist", response_model=list[WatchlistMovie])
async def api_watchlist(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[WatchlistMovie]:
    """Список фильмов «Хочу посмотреть» текущего пользователя."""
    return await _watchlist_movies(session, user.id)


@app.post("/api/v1/watchlist")
async def api_watchlist_add(
    payload: WatchlistRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Добавить фильм в «Хочу посмотреть» (идемпотентно)."""
    movie = await session.get(Movie, payload.movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Фильм не найден")

    exists = await session.scalar(
        select(WatchlistItem.id).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.movie_id == payload.movie_id,
        )
    )
    if exists is None:
        session.add(WatchlistItem(user_id=user.id, movie_id=payload.movie_id))
        await session.commit()
    return {"status": "success"}


@app.delete("/api/v1/watchlist/{movie_id}")
async def api_watchlist_remove(
    movie_id: int,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Убрать фильм из «Хочу посмотреть»."""
    item = await session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.movie_id == movie_id,
        )
    )
    if item is not None:
        await session.delete(item)
        await session.commit()
    return {"status": "success"}


def _feed_filter_clauses(
    country: str | None,
    genre: str | None,
    year_from: int | None,
    year_to: int | None,
) -> list:
    """SQL-условия ленты по стране, жанру и году выпуска.

    Страны хранятся кодами через запятую (``US,GB``), жанры — русскими
    названиями через запятую. Разбор через ``string_to_array`` даёт точное
    совпадение: подстрока «драма» не поймает «мелодраму».
    """
    clauses = []
    if country:
        clauses.append(
            text(
                "EXISTS (SELECT 1 FROM unnest(string_to_array(movies.country, ',')) "
                "AS movie_country WHERE btrim(movie_country) = :country)"
            ).bindparams(country=country)
        )
    if genre:
        clauses.append(
            text(
                "EXISTS (SELECT 1 FROM unnest(string_to_array(movies.genres, ',')) "
                "AS feed_genre WHERE btrim(feed_genre) = :genre)"
            ).bindparams(genre=genre)
        )
    if year_from is not None:
        clauses.append(Movie.year >= year_from)
    if year_to is not None:
        clauses.append(Movie.year <= year_to)
    return clauses


async def _filtered_movie_ids(
    session: AsyncSession,
    country: str | None,
    genre: str | None,
    year_from: int | None,
    year_to: int | None,
) -> set[int]:
    """Идентификаторы фильмов, проходящих фильтры ленты."""
    statement = select(Movie.id).where(
        RECOMMENDABLE,
        *_feed_filter_clauses(country, genre, year_from, year_to),
    )
    return set((await session.scalars(statement)).all())


@app.get("/api/v1/filters", response_model=FeedFilters)
async def api_filters(
    session: AsyncSession = Depends(get_session),
) -> FeedFilters:
    """Справочники для панели фильтров: страны, жанры и границы годов."""
    # Разбор строки через unnest выполняет PostgreSQL: передавать 87 тысяч
    # строк в Python и дробить их там заметно дороже (~600 мс против ~90 мс).
    country_counts: Counter[str] = Counter()
    country_rows = (
        await session.execute(
            text(
                "SELECT upper(btrim(code)) AS code, count(*) AS count "
                "FROM movies, unnest(string_to_array(movies.country, ',')) AS code "
                "WHERE movies.poster_url IS NOT NULL "
                "  AND movies.country IS NOT NULL AND movies.country <> '' "
                "GROUP BY 1"
            )
        )
    ).all()
    country_counts.update(
        {row.code: int(row.count) for row in country_rows if row.code}
    )

    # У исторических кодов (CS/XC) одна русская подпись — оставляем один код.
    by_label: dict[str, list] = {}
    for code, count in country_counts.items():
        label = country_label(code)
        # В production_countries TMDB иногда кладёт языковые коды (UR, EO, IK, YI).
        # Это не страны: в фильтрах им делать нечего, реальная страна фильма
        # обычно указана рядом.
        if label == code:
            continue
        entry = by_label.setdefault(label, [code, 0, 0])
        entry[1] += count
        if count > entry[2]:
            entry[0] = code
            entry[2] = count
    countries = [
        CountryOption(code=code, name=label, count=total)
        for label, (code, total, _) in sorted(
            by_label.items(), key=lambda item: (-item[1][1], item[0])
        )
    ]

    genre_counts: Counter[str] = Counter()
    genre_rows = (
        await session.execute(
            text(
                "SELECT btrim(genre) AS genre, count(*) AS count "
                "FROM movies, unnest(string_to_array(movies.genres, ',')) AS genre "
                "WHERE movies.poster_url IS NOT NULL AND movies.genres IS NOT NULL "
                "GROUP BY 1"
            )
        )
    ).all()
    genre_counts.update(
        {row.genre: int(row.count) for row in genre_rows if row.genre}
    )
    genres = [
        name
        for name, _ in sorted(
            genre_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    bounds = (
        await session.execute(
            select(func.min(Movie.year), func.max(Movie.year)).where(
                RECOMMENDABLE, Movie.year.is_not(None)
            )
        )
    ).one()

    return FeedFilters(
        countries=countries,
        genres=genres,
        year_min=bounds[0],
        year_max=bounds[1],
    )


@app.get("/api/v1/feed", response_model=list[FeedMovie])
async def api_feed(
    user_id: int = 1,
    top_k: int = 7,
    seed: int | None = None,
    exclude_ids: str | None = None,
    country: str | None = None,
    genre: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[FeedMovie]:
    """``top_k`` фильмов для главной карусели.

    Основной путь — LightFM: вектор пользователя → FAISS → фильтрация →
    exploit/explore. Из ``ratings`` читается только индексная выборка «что
    пользователь уже оценил» (без агрегаций по 32M строк). Если артефактов
    модели нет, лента собирается из ``movie_stats``, как раньше.

    ``country`` (код ISO), ``genre`` и ``year_from``/``year_to`` ограничивают
    пул кандидатов. С фильтрами поиск идёт по всем разрешённым векторам сразу,
    а не по верхушке FAISS: иначе редкая страна могла бы не попасть в топ.
    """
    top_k = max(1, min(int(top_k), MAX_LIMIT))
    country = (country.strip().upper() or None) if country else None
    genre = (genre.strip() or None) if genre else None
    # Не даём экстремальным значениям из URL выйти за int32 PostgreSQL.
    year_from = max(1800, min(2200, year_from)) if year_from is not None else None
    year_to = max(1800, min(2200, year_to)) if year_to is not None else None
    hidden = (
        {int(part) for part in exclude_ids.split(",") if part.strip().isdigit()}
        if exclude_ids
        else set()
    )

    filtered = any(
        value is not None for value in (country, genre, year_from, year_to)
    )
    allowed_ids: set[int] | None = None
    if filtered:
        allowed_ids = await _filtered_movie_ids(
            session, country, genre, year_from, year_to
        )
        if not allowed_ids:
            return []

    if _ENGINE is not None:
        items = await _ENGINE.recommend(
            session,
            user_id,
            top_k=top_k,
            exclude_ids=hidden,
            unavailable=_UNAVAILABLE,
            seed=seed,
            allowed_ids=allowed_ids,
        )
        if items:
            movies = await _movies_by_ids(session, [item.movie_id for item in items])
            feed = [
                FeedMovie(
                    id=item.movie_id,
                    title=movies[item.movie_id].title,
                    year=movies[item.movie_id].year,
                    genres=movies[item.movie_id].genres or "",
                    poster_url=_poster_url(
                        movies[item.movie_id].title,
                        movies[item.movie_id].poster_url,
                    ),
                    score=item.score,
                    synopsis=movies[item.movie_id].synopsis or "",
                )
                for item in items
                if item.movie_id in movies
            ]
            if feed:
                return feed

    pool_size = max(top_k, FEED_POOL_SIZE)

    statement = (
        select(Movie, movie_stats.c.avg_rating, movie_stats.c.ratings_count)
        .join(movie_stats, movie_stats.c.movie_id == Movie.id)
        .where(
            RECOMMENDABLE,
            movie_stats.c.ratings_count >= FEED_MIN_RATINGS,
            *_feed_filter_clauses(country, genre, year_from, year_to),
        )
        .order_by(
            movie_stats.c.avg_rating.desc(),
            movie_stats.c.ratings_count.desc(),
        )
        .limit(pool_size)
    )
    rows = (await session.execute(statement)).all()
    pool = [(movie, float(score), int(count)) for movie, score, count in rows]

    if hidden:
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
    user_id: int | None = None,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ProfileResponse:
    """Профиль пользователя: статистика, любимые жанры и история оценок."""
    target_id = user.id if user_id is None else user_id
    ensure_owner(user, target_id)
    name = user.username if user.username else "Пользователь"

    rows = (
        await session.execute(
            select(Movie, Rating.score)
            .join(Rating, Rating.movie_id == Movie.id)
            .where(Rating.user_id == target_id)
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

    watched_count = await _ratings_count(session, target_id)

    return ProfileResponse(
        user_id=target_id,
        name=name,
        email=user.email,
        watched_count=watched_count,
        hours=round(watched_count * 1.5),
        favorite_genres=favorite_genres,
        history=history,
        watchlist=await _watchlist_movies(session, target_id),
    )


# --------------------------------------------------------------------------- #
# Собранный фронтенд (frontend/dist) — тот же origin, без CORS
# --------------------------------------------------------------------------- #
DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
INDEX_FILE = DIST_DIR / "index.html"
#: Префиксы, для которых не отдаём index.html: неизвестный API-путь должен
#: оставаться честным 404, а не превращаться в HTML-страницу.
_API_PREFIXES = ("api/", "docs", "redoc", "openapi.json")

if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str) -> FileResponse:
    """SPA-fallback: файл из ``dist`` либо ``index.html`` для клиентских маршрутов."""
    if INDEX_FILE.is_file():
        if full_path:
            candidate = (DIST_DIR / full_path).resolve()
            if candidate.is_file() and DIST_DIR in candidate.parents:
                return FileResponse(candidate)
        if not full_path.startswith(_API_PREFIXES):
            return FileResponse(INDEX_FILE)
    raise HTTPException(status_code=404, detail="Not Found")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
