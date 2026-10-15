"""Streamlit-фронтенд рекомендательной системы фильмов.

Фронтенд полностью независим от ML: он не импортирует модель, а обращается
к REST API (см. ``api.py``) и рисует ответ.

Запуск (в двух терминалах):
    1) backend:   uvicorn api:app --host 127.0.0.1 --port 8000
    2) frontend:  streamlit run app.py
"""

from __future__ import annotations

from typing import Any

import requests
import streamlit as st

# Адрес эндпоинта backend'а.
API_URL = "http://127.0.0.1:8000/recommend"
# Таймаут запроса: первый вызов обучает SVD-модель и может быть долгим.
REQUEST_TIMEOUT = 60  # секунд


def fetch_recommendations(user_id: int, top_k: int) -> dict[str, Any]:
    """Запросить рекомендации у REST API.

    Args:
        user_id: Идентификатор пользователя.
        top_k: Сколько рекомендаций запросить.

    Returns:
        Распарсенный JSON-ответ API.

    Raises:
        requests.exceptions.RequestException: при сетевых ошибках или HTTP-ошибках.
    """
    response = requests.get(
        API_URL,
        params={"user_id": user_id, "top_k": top_k},
        timeout=REQUEST_TIMEOUT,
    )
    # Превращаем 4xx/5xx в исключение HTTPError.
    response.raise_for_status()
    return response.json()


def render_response(payload: dict[str, Any]) -> None:
    """Красиво отрисовать ответ API.

    Args:
        payload: JSON от ``/recommend`` вида
            ``{"user_id": ..., "top_k": ..., "recommendations": [...]}``.
    """
    recommendations: list[Any] = payload.get("recommendations", [])
    user_id = payload.get("user_id")

    st.success(f"Готово! Получено {len(recommendations)} рекомендаций для user_id={user_id}.")

    st.subheader("Рекомендации")
    if recommendations:
        # 👇 ДЛЯ ML-ИНЖЕНЕРА: чтобы показывать названия вместо item_id, подтяните
        # каталог фильмов (словарь {item_id: title}) — например, отдельным
        # эндпоинтом `/movies` на backend'е — и подставляйте title здесь.
        for rank, item in enumerate(recommendations, start=1):
            st.write(f"**{rank}.** {item}")
    else:
        st.warning("API вернул пустой список рекомендаций.")

    # Полный JSON — удобно для отладки фронт-бэк стыка.
    with st.expander("Полный ответ API (JSON)"):
        st.json(payload)


def main() -> None:
    st.set_page_config(page_title="Movie Recommendation System", page_icon="🎬", layout="centered")
    st.title("Movie Recommendation System")

    # --- Параметры запроса ---
    with st.sidebar:
        st.header("Параметры")
        user_id = st.number_input("User ID", min_value=1, value=1, step=1)
        top_k = st.slider("Количество рекомендаций (top_k)", min_value=5, max_value=20, value=10, step=1)

    # --- Запрос к API по кнопке ---
    if st.button("Получить рекомендации", type="primary"):
        try:
            payload = fetch_recommendations(int(user_id), int(top_k))
        except requests.exceptions.ConnectionError:
            st.error(
                f"Не удалось подключиться к API ({API_URL}). "
                "Проверьте, что backend запущен: `uvicorn api:app --port 8000`."
            )
        except requests.exceptions.Timeout:
            st.error("API не ответил вовремя. Попробуйте ещё раз чуть позже.")
        except requests.exceptions.HTTPError as error:
            detail = error.response.text if error.response is not None else str(error)
            status = error.response.status_code if error.response is not None else "?"
            st.error(f"API вернул ошибку {status}: {detail}")
        except Exception as error:  # noqa: BLE001 - на всякий случай, чтобы UI не падал
            st.error(f"Непредвиденная ошибка: {error}")
        else:
            # Сохраняем результат, чтобы он не пропадал при перерисовке страницы.
            st.session_state["payload"] = payload

    # --- Вывод результата ---
    payload = st.session_state.get("payload")
    if payload:
        render_response(payload)


if __name__ == "__main__":
    main()
