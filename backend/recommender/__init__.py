"""Онлайн-контур рекомендаций Rexter.

Офлайн-обучение (``backend.ml_pipeline.train_lightfm``) экспортирует векторы
пользователей и фильмов; этот пакет только читает их и отвечает за:

* векторный поиск кандидатов (FAISS, с numpy-fallback);
* холодный старт для пользователей, которых не было в обучении;
* кулдаун показов (Redis, с fallback в память процесса);
* смешивание exploit/explore.
"""

from .cooldown import ShowHistory
from .engine import RecommenderEngine, ScoredItem

__all__ = ["RecommenderEngine", "ScoredItem", "ShowHistory"]
