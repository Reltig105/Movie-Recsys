"""ORM-модели Rexter для PostgreSQL (SQLAlchemy 2.0, async).

Сущности:
* :class:`Movie`  — фильм + метаданные для карточки (id = MovieLens ``movieId``);
* :class:`User`   — пользователь: учётные данные + сессионный токен;
* :class:`Rating` — оценка фильма пользователем (сигнал для ML).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Movie(Base):
    """Фильм. ``id`` совпадает с MovieLens ``movieId`` (внешний ключ, не SERIAL)."""

    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[str | None] = mapped_column(String(255))
    poster_url: Mapped[str | None] = mapped_column(String(512))
    synopsis: Mapped[str | None] = mapped_column(Text)
    director: Mapped[str | None] = mapped_column(String(255))
    # ``cast_members`` — вместо зарезервированного/встроенного имени ``cast``.
    cast_members: Mapped[str | None] = mapped_column(String(512))

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
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    # Время простановки оценки (unix) — нужно для истории и time-based валидации ML.
    timestamp: Mapped[int | None] = mapped_column(Integer, index=True)

    user: Mapped["User"] = relationship(back_populates="ratings")
    movie: Mapped["Movie"] = relationship(back_populates="ratings")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Rating id={self.id} user_id={self.user_id} "
            f"movie_id={self.movie_id} score={self.score}>"
        )
