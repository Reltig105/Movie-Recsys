"""Streamlit-фронтенд рекомендательной системы фильмов.

Экраны:

* страница аутентификации (вкладки ``Login`` / ``Register``);
* основной экран для вошедшего пользователя с боковой навигацией:
  ``Recommendations`` / ``Search Movies`` / ``My Profile``.

Запуск (в двух терминалах):
    1) backend:   uvicorn api:app --host 127.0.0.1 --port 8000
    2) frontend:  streamlit run app.py
"""

from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

BASE_URL = "http://127.0.0.1:8000"
REGISTER_URL = f"{BASE_URL}/register"
LOGIN_URL = f"{BASE_URL}/login"
RATE_URL = f"{BASE_URL}/rate"
HISTORY_URL = f"{BASE_URL}/user"
SEARCH_URL = f"{BASE_URL}/search"
REQUEST_TIMEOUT = 60  # секунд

PAGES = ["🎬 Recommendations", "🔍 Search Movies", "👤 My Profile"]

# Стили для сайдбара: делаем навигационные кнопки плоскими, как вкладки меню.
SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    justify-content: flex-start;
    text-align: left;
    border: none;
    border-radius: 0.25rem;
    border-left: 3px solid transparent;
    background: transparent;
    box-shadow: none;
    padding: 0.55rem 0.85rem;
    font-weight: 500;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(255, 75, 75, 0.10);
    border-left-color: #ff4b4b;
    color: inherit;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    border-left-color: #ff4b4b;
    background: rgba(255, 75, 75, 0.14);
}
</style>
"""

OCCUPATIONS = ["student", "programmer", "engineer", "other"]
GENDERS = ["M", "F"]


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
def _show_error(error: Exception) -> None:
    """Единообразный вывод ошибок запроса."""
    if isinstance(error, requests.exceptions.ConnectionError):
        st.error(
            f"Не удалось подключиться к API ({BASE_URL}). "
            "Проверьте, что backend запущен: `uvicorn api:app --port 8000`."
        )
    elif isinstance(error, requests.exceptions.Timeout):
        st.error("API не ответил вовремя. Попробуйте ещё раз чуть позже.")
    elif isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
        try:
            detail = error.response.json().get("detail", error.response.text)
        except ValueError:
            detail = error.response.text
        st.error(f"API вернул ошибку {error.response.status_code}: {detail}")
    else:
        st.error(f"Ошибка при обращении к API: {error}")


def _rate_movie(user_id: int, movie_id: int, rating: float) -> bool:
    """Отправить оценку фильма в API. Возвращает ``True`` при успехе."""
    try:
        response = requests.post(
            RATE_URL,
            json={"user_id": user_id, "movie_id": movie_id, "rating": rating},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except (requests.exceptions.RequestException, ValueError) as error:
        _show_error(error)
        return False
    return True


def _poster_url(title: str) -> str:
    """URL плейсхолдер-постера для фильма."""
    return f"https://placehold.co/200x300/2b2b2b/FFFFFF/png?text={quote(title)}"


# --------------------------------------------------------------------------- #
# Переиспользуемая сетка карточек фильмов
# --------------------------------------------------------------------------- #
def _render_movie_card(
    user_id: int,
    movie: Dict[str, Any],
    position: int,
    key_prefix: str,
) -> None:
    """Отрисовать одну карточку фильма: постер, жанры, слайдер и кнопку Rate."""
    movie_id = movie.get("movie_id")
    title = movie.get("title", f"Movie {position}")
    genres = movie.get("genres", "")
    score = movie.get("score")

    st.image(_poster_url(title), use_container_width=True)
    st.markdown(f"**{title}**")
    if genres:
        st.caption(genres)
    if score is not None:
        try:
            st.caption(f"Score: {float(score):.2f}")
        except (TypeError, ValueError):
            st.caption(f"Score: {score}")

    if movie_id is None:
        return

    with st.form(f"{key_prefix}_form_{movie_id}"):
        rating = st.slider(
            "Ваша оценка",
            min_value=1,
            max_value=5,
            value=3,
            step=1,
            key=f"{key_prefix}_slider_{movie_id}",
        )
        submitted = st.form_submit_button("Rate", type="primary", use_container_width=True)

    if submitted:
        if _rate_movie(user_id, int(movie_id), float(rating)):
            st.toast(f"Оценка {rating}/5 для «{title}» сохранена.")
            st.success(f"Оценка {rating}/5 сохранена.")


def _render_movie_grid(
    user_id: int,
    movies: List[Dict[str, Any]],
    key_prefix: str,
    columns_per_row: int = 4,
    empty_message: str = "Список фильмов пуст.",
) -> None:
    """Показать фильмы сеткой карточек (movie card grid).

    Единая точка отрисовки для рекомендаций и поиска — карточка общая для всех
    страниц, что исключает дублирование разметки.
    """
    if not isinstance(movies, list):
        st.error("Expected a list of movies.")
        return

    if not movies:
        st.warning(empty_message)
        return

    for start in range(0, len(movies), columns_per_row):
        row = movies[start : start + columns_per_row]
        columns = st.columns(columns_per_row)
        for offset, movie in enumerate(row):
            with columns[offset]:
                _render_movie_card(user_id, movie, start + offset + 1, key_prefix)


# --------------------------------------------------------------------------- #
# Аутентификация
# --------------------------------------------------------------------------- #
def _logout() -> None:
    """Сбросить сессию и перерисовать страницу."""
    for key in (
        "user_id",
        "username",
        "recommendations",
        "search_results",
        "current_page",
    ):
        st.session_state.pop(key, None)
    st.rerun()


def _login_tab() -> None:
    """Вкладка входа."""
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")

    if not submitted:
        return

    if not username or not password:
        st.error("Введите логин и пароль.")
        return

    try:
        response = requests.post(
            LOGIN_URL,
            json={"username": username, "password": password},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.exceptions.RequestException, ValueError) as error:
        _show_error(error)
        return

    st.session_state["user_id"] = int(data["user_id"])
    st.session_state["username"] = data["username"]
    st.rerun()


def _register_tab() -> None:
    """Вкладка регистрации."""
    with st.form("register_form"):
        username = st.text_input("Username", key="register_username")
        password = st.text_input("Password", type="password", key="register_password")
        age = st.number_input("Age", min_value=0, max_value=120, value=25, step=1)
        gender = st.selectbox("Gender", options=GENDERS)
        occupation = st.selectbox("Occupation", options=OCCUPATIONS)
        submitted = st.form_submit_button("Register", type="primary")

    if not submitted:
        return

    if not username or not password:
        st.error("Введите логин и пароль.")
        return

    payload = {
        "username": username,
        "password": password,
        "age": int(age),
        "gender": gender,
        "occupation": occupation,
    }

    try:
        response = requests.post(REGISTER_URL, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.exceptions.RequestException, ValueError) as error:
        _show_error(error)
        return

    st.success(f"Пользователь {data['username']} зарегистрирован (id={data['user_id']}). Теперь войдите.")


def _auth_page() -> None:
    """Страница аутентификации с вкладками Login/Register."""
    st.title("Rexter")
    login_tab, register_tab = st.tabs(["Login", "Register"])
    with login_tab:
        _login_tab()
    with register_tab:
        _register_tab()


# --------------------------------------------------------------------------- #
# Запросы к API
# --------------------------------------------------------------------------- #
def _extract_movies(data: Any) -> List[Dict[str, Any]]:
    """Привести ответ API к плоскому списку фильмов.

    Ожидается ``List[dict]`` вида ``[{"movie_id": 1, ...}]``. Но если пришёл
    словарь-обёртка (``{"recommendations": [...]}``, ``{"movies": [...]}``) или
    ошибка (``{"detail": "..."}``), аккуратно извлекаем список либо показываем
    ошибку и возвращаем пустой список.
    """
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        detail = data.get("detail")
        if detail is not None:
            st.error(detail)
            return []
        for key in ("recommendations", "movies", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    st.error("Неожиданный формат ответа API: ожидался список фильмов.")
    return []


def _fetch_profile(user_id: int) -> Dict[str, Any]:
    """Получить профиль пользователя из API."""
    response = requests.get(f"{BASE_URL}/user/{user_id}", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _fetch_recommendations(user_id: int, top_k: int) -> List[Dict[str, Any]]:
    """Получить рекомендации из API (unified hot/cold start).

    Эндпоинт автоопределяет сценарий и возвращает список вида
    ``[{"movie_id", "title", "genres", "score"}]``.
    """
    response = requests.get(
        f"{BASE_URL}/recommend/{user_id}",
        params={"top_k": top_k},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return _extract_movies(response.json())


def _fetch_history(user_id: int) -> List[Dict[str, Any]]:
    """Получить историю оценок пользователя из API."""
    response = requests.get(f"{HISTORY_URL}/{user_id}/history", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _fetch_search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Найти фильмы по названию через API ``GET /search``."""
    response = requests.get(
        SEARCH_URL,
        params={"query": query, "limit": limit},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return _extract_movies(response.json())


# --------------------------------------------------------------------------- #
# Страницы основного приложения
# --------------------------------------------------------------------------- #
def _render_recommendations_page(user_id: int) -> None:
    """Страница персональных рекомендаций.

    Рекомендации НЕ загружаются автоматически: запрос к API выполняется только
    по нажатию кнопки «Рекомендовать фильмы». Backend сам определяет
    hot/cold start по истории оценок пользователя.
    """
    st.subheader("Recommendations")

    top_k = st.number_input(
        "Количество фильмов", min_value=1, max_value=50, value=12, step=1
    )

    if st.button("Рекомендовать фильмы", type="primary", use_container_width=True):
        try:
            results = _fetch_recommendations(user_id, int(top_k))
        except (requests.exceptions.RequestException, ValueError) as error:
            _show_error(error)
        else:
            st.session_state["recommendations"] = results

    recommendations = st.session_state.get("recommendations")
    if recommendations is not None:
        _render_movie_grid(
            user_id,
            recommendations,
            key_prefix="rec",
            empty_message="Рекомендации пока пусты.",
        )


def _render_search_page(user_id: int) -> None:
    """Страница поиска фильмов по названию с выдачей сеткой карточек."""
    st.subheader("🔍 Search Movies")

    with st.form("search_form"):
        query = st.text_input("Название фильма", key="search_query")
        submitted = st.form_submit_button("Search", type="primary")

    if submitted:
        if not query.strip():
            st.warning("Введите название фильма.")
        else:
            try:
                results = _fetch_search(query.strip())
            except (requests.exceptions.RequestException, ValueError) as error:
                _show_error(error)
            else:
                st.session_state["search_results"] = results

    results = st.session_state.get("search_results")
    if results is None:
        return

    if not results:
        st.info("По вашему запросу ничего не найдено.")
        return

    st.caption(f"Найдено фильмов: {len(results)}")
    _render_movie_grid(user_id, results, key_prefix="search")


def _render_history(history: List[Dict[str, Any]]) -> None:
    """Показать историю оценок пользователя таблицей."""
    if not history:
        st.info("Вы ещё не оценили ни одного фильма.")
        return

    frame = pd.DataFrame(history)
    frame = frame.rename(columns={"movie_id": "Movie ID", "title": "Title", "rating": "Rating"})
    frame.index = range(1, len(frame) + 1)
    st.dataframe(frame, use_container_width=True)


def _render_profile_page(user_id: int, username: str) -> None:
    """Страница профиля: данные пользователя, число оценок и история."""
    st.subheader("👤 My Profile")

    try:
        profile = _fetch_profile(user_id)
    except (requests.exceptions.RequestException, ValueError) as error:
        _show_error(error)
        profile = None

    if profile:
        st.write(
            {
                "username": profile.get("username", username),
                "age": profile.get("age"),
                "gender": profile.get("gender"),
                "occupation": profile.get("occupation"),
            }
        )

    try:
        history = _fetch_history(user_id)
    except (requests.exceptions.RequestException, ValueError) as error:
        _show_error(error)
        return

    st.metric("Total ratings", len(history))
    st.subheader("Rating history")
    _render_history(history)


# --------------------------------------------------------------------------- #
# Основное приложение с боковой навигацией
# --------------------------------------------------------------------------- #
def _main_page() -> None:
    """Основной экран для вошедшего пользователя с навигацией в сайдбаре."""
    user_id = int(st.session_state["user_id"])
    username = st.session_state["username"]

    if "current_page" not in st.session_state:
        st.session_state["current_page"] = PAGES[0]

    with st.sidebar:
        st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)
        st.markdown(f"### 👤 {username}")

        for page in PAGES:
            is_active = page == st.session_state["current_page"]
            if st.button(
                page,
                key=f"nav_{page}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state["current_page"] = page
                st.rerun()

        st.divider()
        st.button("Logout", on_click=_logout, use_container_width=True)

    current_page = st.session_state["current_page"]
    if current_page == PAGES[0]:
        _render_recommendations_page(user_id)
    elif current_page == PAGES[1]:
        _render_search_page(user_id)
    else:
        _render_profile_page(user_id, username)


# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(page_title="Rexter", page_icon="🎬", layout="centered")

    if "user_id" not in st.session_state:
        _auth_page()
    else:
        _main_page()


if __name__ == "__main__":
    main()
