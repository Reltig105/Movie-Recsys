"""Streamlit-интерфейс рекомендательной системы фильмов.

Запуск:
    streamlit run app.py

Модуль умеет работать в двух режимах:
    1) с реальной моделью — если удалось импортировать ``recommend``;
    2) с mock-заглушкой — если модуля модели ещё нет (см. ``mock_recommend``).
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

# =============================================================================
# 👇 ДЛЯ ML-ИНЖЕНЕРА: НАЗВАНИЯ ФИЛЬМОВ (item_id -> title).
#
# MOVIE_TITLES загружается автоматически из MovieLens (ml/src/explore_data.py).
# Если данные недоступны — словарь пустой, и интерфейс покажет "Movie ID <id>".
#
# Хочешь другой источник (например, обогащённые TMDB-названия/постеры) — замени
# тело _load_movie_titles, например:
#     import pandas as pd
#     return pd.read_csv("ml/data/interim/tmdb_features.csv") \
#              .set_index("item_id")["title"].to_dict()
# =============================================================================
def _load_movie_titles() -> dict[int, str]:
    """Загрузить словарь ``{item_id: title}``. При ошибке вернуть пустой словарь."""
    try:
        from ml.src.explore_data import load_movies

        movies = load_movies().set_index("item_id")["title"]
        return {int(item_id): str(title) for item_id, title in movies.items()}
    except Exception:  # noqa: BLE001 - данных может не быть, это не критично для UI
        return {}


MOVIE_TITLES: dict[int, str] = _load_movie_titles()

# Тип, который может вернуть модель: либо id (int), либо уже готовые названия (str).
Recommendation = int | str


def _load_recommend_function() -> Callable[[int, int], list[Recommendation]] | None:
    """Попытаться импортировать функцию рекомендаций ``recommend(user_id, top_k)``.

    Порядок попыток:
        1. ``src.svd_model`` — путь из ТЗ;
        2. ``ml.src.models.svd_model`` — фактическое расположение модуля в репо
           (там это класс ``SVDRecommender``; обучаем его и берём метод ``recommend``).

    Returns:
        Функцию ``recommend`` или ``None``, если модель ещё не готова.
    """
    # --- Попытка 1: путь из ТЗ ---
    try:
        from src.svd_model import recommend as svd_recommend

        return svd_recommend
    except ImportError:
        pass

    # --- Попытка 2: реальный путь в этом репозитории ---
    try:
        from ml.src.models.svd_model import SVDRecommender, load_train_ratings

        # Модель обучается один раз и кэшируется между перезапусками интерфейса.
        @st.cache_resource(show_spinner="Обучаю SVD-модель...")
        def _build() -> SVDRecommender:
            return SVDRecommender(n_components=20, random_state=42).fit(load_train_ratings())

        model = _build()
        return model.recommend
    except ImportError:
        return None
    except Exception:  # noqa: BLE001 - модель есть, но не удалось обучить/загрузить
        return None


def mock_recommend(user_id: int, top_k: int) -> list[Recommendation]:
    """Заглушка на случай, если реальная модель пока недоступна.

    Возвращает ``top_k`` фейковых названий фильмов ("Movie A", "Movie B", ...).
    """
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [f"Movie {letters[i]}" for i in range(min(top_k, len(letters)))]


def resolve_for_display(item: Recommendation) -> str:
    """Преобразовать элемент рекомендации в строку для отображения.

    Args:
        item: Либо ``item_id`` (int / строка-число), либо готовое название.

    Returns:
        Название фильма, если оно есть в ``MOVIE_TITLES``, иначе "Movie ID <id>".
    """
    # Если вернулся ID (int или строка из цифр) — пробуем найти название.
    if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
        item_id = int(item)
        # 👇 Здесь словарь MOVIE_TITLES подставляет реальное название вместо ID.
        return MOVIE_TITLES.get(item_id, f"Movie ID {item_id}")

    # Уже название.
    return str(item)


def render_recommendations(items: list[Recommendation]) -> None:
    """Отрисовать рекомендации в виде нумерованного списка."""
    if not items:
        st.warning("Рекомендации не найдены.")
        return

    st.subheader("Рекомендации")
    for rank, item in enumerate(items, start=1):
        st.write(f"**{rank}.** {resolve_for_display(item)}")


def main() -> None:
    st.set_page_config(page_title="Movie Recommendation System", page_icon="🎬", layout="centered")
    st.title("Movie Recommendation System")

    # --- Сайдбар с параметрами ---
    with st.sidebar:
        st.header("Параметры")
        user_id = st.number_input("User ID", min_value=1, value=1, step=1)
        top_k = st.slider("Количество рекомендаций (top_k)", min_value=5, max_value=20, value=10, step=1)

    # --- Кнопка в основной области ---
    if st.button("Get Recommendations", type="primary"):
        recommend_fn = _load_recommend_function()
        if recommend_fn is None:
            st.info("Модуль модели не найден — показываю демо-результаты (mock).")
            recommend_fn = mock_recommend

        try:
            items = recommend_fn(int(user_id), int(top_k))
        except Exception as error:  # noqa: BLE001 - показываем любую ошибку прямо в UI
            st.error(f"Не удалось получить рекомендации: {error}")
        else:
            # Сохраняем, чтобы результат не исчезал при перерисовке страницы.
            st.session_state["recommendations"] = list(items)

    # --- Вывод результатов ---
    results = st.session_state.get("recommendations")
    if results is not None:
        render_recommendations(results)


if __name__ == "__main__":
    main()
