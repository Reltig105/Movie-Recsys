"""REST API (FastAPI) рекомендательной системы фильмов.

Запуск:
    uvicorn api:app --host 127.0.0.1 --port 8000
    # или просто
    python api.py

Эндпоинт:
    GET /recommend?user_id=1&top_k=10
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="Movie Recommender API", version="1.0.0")

# Сигнатура функции рекомендаций: (user_id, top_k) -> список item_id.
RecommendFn = Callable[[int, int], list[int]]


@lru_cache(maxsize=1)
def _get_recommend_fn() -> RecommendFn:
    """Загрузить функцию ``recommend(user_id, top_k)`` и закэшировать её.

    Порядок попыток:
        1. ``src.svd_model`` — путь из ТЗ;
        2. ``ml.src.models.svd_model`` — фактическое расположение модуля в репо
           (там это класс ``SVDRecommender``; обучаем его и берём метод ``recommend``).

    Модель обучается один раз (результат кэшируется на процесс), поэтому первый
    запрос может быть медленнее последующих.

    Returns:
        Callable ``recommend(user_id, top_k) -> list[int]``.

    Raises:
        ImportError: если модуль модели недоступен.
    """
    # --- Попытка 1: путь из ТЗ ---
    try:
        from src.svd_model import recommend as svd_recommend

        return svd_recommend
    except ImportError:
        pass

    # --- Попытка 2: реальный путь в этом репозитории ---
    from ml.src.models.svd_model import SVDRecommender, load_train_ratings

    model = SVDRecommender(n_components=20, random_state=42).fit(load_train_ratings())
    return model.recommend


@lru_cache(maxsize=1)
def _get_movie_catalog() -> dict[int, str]:
    """Загрузить каталог ``{item_id: title}`` и закэшировать его.

    Источник — метаданные MovieLens (``ml/src/explore_data.load_movies``).
    При ошибке (данных нет) возвращает пустой словарь, чтобы API не падал.
    """
    try:
        from ml.src.explore_data import load_movies

        movies = load_movies().set_index("item_id")["title"]
        return {int(item_id): str(title) for item_id, title in movies.items()}
    except Exception:  # noqa: BLE001 - каталог не критичен для работы API
        return {}


@app.get("/health")
def health() -> dict[str, str]:
    """Простейший health-check для мониторинга и фронтенда."""
    return {"status": "ok"}


@app.get("/movies")
def list_movies(
    item_ids: str | None = Query(
        None,
        description="Необязательный список item_id через запятую, например '100,475'",
    ),
) -> dict[str, Any]:
    """Вернуть каталог фильмов вида ``{"movies": [{"item_id": .., "title": ..}, ...]}``.

    Если передан ``item_ids`` — возвращаются только запрошенные фильмы (для тех,
    которых нет в каталоге, подставляется ``"Movie ID <id>"``). Иначе — весь каталог.
    """
    catalog = _get_movie_catalog()

    if item_ids:
        requested = []
        for chunk in item_ids.split(","):
            chunk = chunk.strip()
            if chunk.lstrip("-").isdigit():
                requested.append(int(chunk))
        titles = {item_id: catalog.get(item_id, f"Movie ID {item_id}") for item_id in requested}
    else:
        titles = catalog

    movies = [{"item_id": item_id, "title": title} for item_id, title in titles.items()]
    return {"count": len(movies), "movies": movies}


@app.get("/recommend")
def get_recommendations(
    user_id: int = Query(..., ge=1, description="Идентификатор пользователя"),
    top_k: int = Query(10, ge=1, le=100, description="Сколько рекомендаций вернуть"),
) -> dict[str, Any]:
    """Вернуть top-K рекомендаций для пользователя.

    Returns:
        JSON вида ``{"user_id": 1, "top_k": 10, "recommendations": [id1, id2, ...]}``.

    Raises:
        HTTPException: 500, если модель недоступна или упала при инференсе.
    """
    try:
        recommend_fn = _get_recommend_fn()
        recommendations = list(recommend_fn(user_id, top_k))
    except ImportError as error:
        # Модуль модели не найден / не импортируется.
        raise HTTPException(status_code=500, detail=f"Модель недоступна: {error}") from error
    except Exception as error:  # noqa: BLE001 - любую ошибку инференса отдаём как 500
        raise HTTPException(status_code=500, detail=f"Ошибка формирования рекомендаций: {error}") from error

    return {"user_id": user_id, "top_k": top_k, "recommendations": recommendations}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
