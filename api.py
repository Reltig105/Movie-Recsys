"""Совместимость: канонический backend находится в ``backend/api.py``.

Этот модуль оставлен, чтобы запуск ``uvicorn api:app`` продолжал работать.
Актуальные команды запуска:

    uvicorn backend.api:app --host 127.0.0.1 --port 8000
    uvicorn api:app --host 127.0.0.1 --port 8000   # эквивалентно
"""

from __future__ import annotations

from backend.api import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
