"""ORM-модели Rexter для PostgreSQL (SQLAlchemy 2.0, async).

Сущности:
* :class:`Movie`  — фильм + метаданные для карточки (id = MovieLens ``movieId``);
* :class:`User`   — пользователь: учётные данные + сессионный токен;
* :class:`Rating` — оценка фильма пользователем (сигнал для ML).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


#: Предрасчитанная статистика оценок — materialized view (см.
#: :mod:`backend.refresh_stats`). Отдельный ``MetaData``: ``create_all`` не
#: должен пытаться создать её как обычную таблицу.
movie_stats = Table(
    "movie_stats",
    MetaData(),
    Column("movie_id", Integer, primary_key=True),
    Column("avg_rating", Float, nullable=False),
    Column("ratings_count", Integer, nullable=False),
)


class Movie(Base):
    """Фильм. ``id`` совпадает с MovieLens ``movieId`` (внешний ключ, не SERIAL)."""

    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[str | None] = mapped_column(String(255))
    # Страны производства — коды ISO 3166-1 через запятую (``US,GB``). Коды
    # устойчивее названий, а русские подписи собираются в backend/countries.py.
    country: Mapped[str | None] = mapped_column(String(120))
    poster_url: Mapped[str | None] = mapped_column(String(512))
    synopsis: Mapped[str | None] = mapped_column(Text)
    director: Mapped[str | None] = mapped_column(String(255))
    # ``cast_members`` — вместо зарезервированного/встроенного имени ``cast``.
    cast_members: Mapped[str | None] = mapped_column(String(512))
    # Когда TMDB дал окончательный ответ (200/404). Отличает «постера нет на
    # TMDB» от «ещё не проверяли», чтобы добор не повторял одни и те же фильмы.
    tmdb_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ratings: Mapped[list["Rating"]] = relationship(
        back_populates="movie", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Movie id={self.id} title={self.title!r} year={self.year}>"


class User(Base):
    """Пользователь: учётные данные для входа + сессионный токен."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    age: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(16))
    occupation: Mapped[str | None] = mapped_column(String(64))
    session_token: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)

    ratings: Mapped[list["Rating"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User id={self.id} username={self.username!r}>"


class Rating(Base):
    """Оценка фильма пользователем.

    Уникальная пара ``(user_id, movie_id)`` позволяет делать безопасный upsert:
    повторная оценка обновляет существующую запись, а не создаёт дубликат.
    """

    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "movie_id", name="uq_ratings_user_movie"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Шкала MovieLens — 0.5…5.0 с шагом 0.5. Целочисленный тип терял бы 31%
    # оценок (все половинные), поэтому храним как double precision: значения
    # вида k/2 представляются точно, а asyncpg отдаёт их обычным float.
    score: Mapped[float] = mapped_column(Float, nullable=False)
    # Время простановки оценки (unix) — нужно для истории и time-based валидации ML.
    timestamp: Mapped[int | None] = mapped_column(Integer, index=True)

    user: Mapped["User"] = relationship(back_populates="ratings")
    movie: Mapped["Movie"] = relationship(back_populates="ratings")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Rating id={self.id} user_id={self.user_id} "
            f"movie_id={self.movie_id} score={self.score}>"
        )
