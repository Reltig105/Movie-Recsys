"""Streamlit-интерфейс рекомендательной системы фильмов.

Запуск:
    streamlit run app.py

Сейчас данные берутся из заглушки ``mock_recommend``. Точка интеграции с
реальной SVD-моделью помечена в коде ниже (см. блок TODO и пометки
"ЗАМЕНИТЬ ЗДЕСЬ").
"""

from __future__ import annotations

import streamlit as st

# =============================================================================
# TODO: ТОЧКА ИНТЕГРАЦИИ С РЕАЛЬНОЙ МОДЕЛЬЮ.
#
# Сейчас используется заглушка ``mock_recommend``. Чтобы подключить настоящую
# SVD-модель из ml/src/models/svd_model.py:
#
#   1) Раскомментируйте блок ниже.
#   2) В main() замените вызов mock_recommend(...) на get_recommendations(...)
#      (там стоит пометка "ЗАМЕНИТЬ ЗДЕСЬ").
#
# -----------------------------------------------------------------------------
# from ml.src.models.svd_model import SVDRecommender, load_train_ratings
#
#
# @st.cache_resource
# def load_recommender() -> SVDRecommender:
#     """Обучить модель один раз и закэшировать между перезапусками интерфейса."""
#     model = SVDRecommender(n_components=20, random_state=42)
#     return model.fit(load_train_ratings())
#
#
# def get_recommendations(user_id: int, top_k: int) -> list[int]:
#     """Рекомендации для пользователя; возвращает список item_id (int)."""
#     model = load_recommender()
#     return model.recommend(user_id=user_id, top_k=top_k)
# -----------------------------------------------------------------------------
#
# ВАЖНО: реальная модель вернёт список ``item_id`` (int), а не названия.
# Чтобы показывать названия, сопоставьте id с тайтлами, например через
# ``load_movies()`` из ml.src.explore_data, и передайте в render_recommendations()
# уже список строк.
# =============================================================================


def mock_recommend(user_id: int, top_k: int) -> list[str]:
    """Заглушка вместо реальной модели.

    Возвращает ``top_k`` фейковых названий фильмов ("Movie A", "Movie B", ...).

    Args:
        user_id: Идентификатор пользователя (в заглушке не используется).
        top_k: Количество рекомендаций.

    Returns:
        Список названий фильмов длиной не более ``top_k``.
    """
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [f"Movie {letters[i]}" for i in range(min(top_k, len(letters)))]


def render_recommendations(items: list[str]) -> None:
    """Вывести рекомендации в виде карточек с нумерацией."""
    if not items:
        st.warning("Рекомендации не найдены.")
        return

    st.subheader("Рекомендации")
    for rank, title in enumerate(items, start=1):
        with st.container(border=True):
            st.markdown(f"**{rank}. {title}**")


def main() -> None:
    st.set_page_config(
        page_title="Movie Recommender System",
        page_icon="🎬",
        layout="centered",
    )
    st.title("Movie Recommender System")

    # --- Ввод данных ---
    user_id = st.number_input("User ID", min_value=1, value=1, step=1)
    top_k = st.slider(
        "Количество рекомендаций (top_k)",
        min_value=5,
        max_value=20,
        value=10,
        step=1,
    )

    # --- Логика по нажатию кнопки ---
    if st.button("Получить рекомендации", type="primary"):
        try:
            # ЗАМЕНИТЬ ЗДЕСЬ: mock_recommend -> get_recommendations (реальная модель).
            recommendations = mock_recommend(user_id=int(user_id), top_k=int(top_k))
        except Exception as error:  # noqa: BLE001 - показываем любую ошибку в UI
            st.error(f"Не удалось получить рекомендации: {error}")
        else:
            # Сохраняем результат, чтобы он не исчезал при перерисовке страницы.
            st.session_state["recommendations"] = recommendations

    # --- Вывод результатов ---
    recommendations = st.session_state.get("recommendations")
    if recommendations:
        render_recommendations(recommendations)
    elif recommendations == []:
        st.warning("Рекомендации не найдены.")


if __name__ == "__main__":
    main()
