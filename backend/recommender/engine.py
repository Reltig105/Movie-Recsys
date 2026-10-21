"""Онлайн-инференс: эмбеддинги LightFM + FAISS + кулдаун + exploit/explore.

Пайплайн одного запроса::

    user_id ──► вектор p_u (из user_embeddings.npy или из истории оценок)
            ──► FAISS IndexFlatIP: топ-K кандидатов
            ──► минус уже оценённые / показанные / без постера
            ──► 5 exploit + 2 explore
            ──► movie_id + score

Тяжёлой математики здесь нет: модель уже обучена, поэтому запрос — это одно
скалярное произведение (87 585 × 65) и пара индексных выборок из PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Rating
from .cooldown import DEFAULT_TTL, ShowHistory

logger = logging.getLogger(__name__)

#: Сколько кандидатов после фильтрации участвует в exploit/explore.
CANDIDATE_LIMIT = 100

#: Сколько соседей просить у FAISS (с запасом на уже оценённые фильмы).
SEARCH_POOL = 500

#: Доля explore в выдаче: на 7 карточек — 5 exploit + 2 explore.
EXPLORE_SHARE = 2 / 7

#: Для холодного старта достаточно последних оценок пользователя.
COLD_PROFILE_LIMIT = 100


@dataclass(frozen=True)
class ScoredItem:
    """Фильм и его скор LightFM (``p_u · q_i + b_u + b_i``)."""

    movie_id: int
    score: float


def select_explore_exploit(
    candidates: list[ScoredItem], top_k: int, rng: random.Random
) -> list[ScoredItem]:
    """Собрать выдачу: лучшие (exploit) + случайные из хвоста (explore).

    Хвост — вторая половина топ-100 кандидатов: там ещё есть релевантные
    фильмы, но уже не «очевидный» топ, поэтому выдача не выглядит одинаковой
    при каждом обновлении.
    """
    if not candidates:
        return []
    top_k = max(1, top_k)
    exploit_count = max(1, round(top_k * (1 - EXPLORE_SHARE)))
    exploit = candidates[:exploit_count]
    chosen = {item.movie_id for item in exploit}

    explore_count = max(0, top_k - len(exploit))
    tail_start = max(len(exploit), int(len(candidates) * 0.6))
    tail = [item for item in candidates[tail_start:] if item.movie_id not in chosen]
    if len(tail) < explore_count:
        tail = [
            item for item in candidates[len(exploit) :] if item.movie_id not in chosen
        ]
    explore = rng.sample(tail, min(explore_count, len(tail)))

    result = exploit + explore
    if len(result) < top_k:
        chosen = {item.movie_id for item in result}
        for item in candidates:
            if item.movie_id in chosen:
                continue
            result.append(item)
            chosen.add(item.movie_id)
            if len(result) >= top_k:
                break
    return result[:top_k]


class RecommenderEngine:
    """Загруженные артефакты LightFM и логика выдачи."""

    def __init__(
        self,
        *,
        item_vectors: np.ndarray,
        user_vectors: np.ndarray,
        user_biases: np.ndarray,
        movie_ids: np.ndarray,
        user_ids: np.ndarray,
        index,
        meta: dict,
        cooldown: ShowHistory,
    ) -> None:
        self._item_vectors = item_vectors
        self._user_vectors = user_vectors
        self._user_biases = user_biases
        self._movie_ids = movie_ids
        self._user_ids = user_ids
        self._index = index
        self.meta = meta
        self.cooldown = cooldown

    # ------------------------------------------------------------------ #
    # Загрузка артефактов
    # ------------------------------------------------------------------ #
    @property
    def is_ready(self) -> bool:
        return self._item_vectors.size > 0

    @property
    def dim(self) -> int:
        return int(self._item_vectors.shape[1])

    @property
    def n_items(self) -> int:
        return int(self._item_vectors.shape[0])

    @property
    def n_users(self) -> int:
        return int(self._user_vectors.shape[0])

    @property
    def backend(self) -> str:
        return "faiss" if self._index is not None else "numpy"

    @classmethod
    async def load(
        cls,
        artifacts_dir: Path,
        redis_url: str | None,
        cooldown_ttl: int = DEFAULT_TTL,
    ) -> "RecommenderEngine | None":
        """Загрузить артефакты (в потоке) и подключить кулдаун.

        ``None`` — артефактов нет: приложение продолжит работать на популярном
        каталоге, а не упадёт на старте.
        """
        artifacts_dir = Path(artifacts_dir)
        if not (artifacts_dir / "item_embeddings.npy").exists():
            logger.warning(
                "Артефактов LightFM нет в %s — лента останется популярной. "
                "Обучение: backend/ml_pipeline/train_lightfm.py",
                artifacts_dir,
            )
            return None

        engine = await asyncio.to_thread(cls._load_sync, artifacts_dir, redis_url, cooldown_ttl)
        await engine.cooldown.connect()
        logger.info(
            "Рекомендации готовы: %s фильмов × %s, пользователей — %s, "
            "поиск — %s, кулдаун — %s",
            f"{engine.n_items:,}".replace(",", " "),
            engine.dim,
            f"{engine.n_users:,}".replace(",", " "),
            engine.backend,
            engine.cooldown.backend,
        )
        return engine

    @classmethod
    def _load_sync(
        cls, artifacts_dir: Path, redis_url: str | None, cooldown_ttl: int
    ) -> "RecommenderEngine":
        item_vectors = np.load(artifacts_dir / "item_embeddings.npy")
        # Векторы пользователей — 52 МБ; читаем через mmap, нужна одна строка.
        user_vectors = np.load(artifacts_dir / "user_embeddings.npy", mmap_mode="r")
        user_biases = np.load(artifacts_dir / "user_biases.npy")
        movie_ids = np.load(artifacts_dir / "movie_ids.npy").astype(np.int64, copy=False)
        user_ids = np.load(artifacts_dir / "user_ids.npy").astype(np.int64, copy=False)

        if item_vectors.dtype != np.float32:
            item_vectors = item_vectors.astype(np.float32)
        if user_vectors.dtype != np.float32:
            user_vectors = np.asarray(user_vectors, dtype=np.float32)
        if item_vectors.ndim != 2 or user_vectors.ndim != 2:
            raise ValueError("эмбеддинги должны быть двумерными")
        if item_vectors.shape[1] != user_vectors.shape[1]:
            raise ValueError("размерности item/user эмбеддингов не совпадают")
        if movie_ids.size != item_vectors.shape[0]:
            raise ValueError("movie_ids не совпадает с числом векторов фильмов")
        if user_ids.size != user_vectors.shape[0]:
            raise ValueError("user_ids не совпадает с числом векторов пользователей")

        index = None
        index_path = artifacts_dir / "faiss.index"
        if index_path.exists():
            try:
                import faiss

                # faiss.read_index() зовёт C++ fopen, который на Windows не
                # понимает не-ASCII пути (``Документы``). Читаем байты через
                # Python и десериализуем — путь любой.
                blob = np.fromfile(index_path, dtype=np.uint8)
                index = faiss.deserialize_index(blob)
                if index.ntotal != item_vectors.shape[0]:
                    raise ValueError(
                        f"в FAISS {index.ntotal} векторов, ожидалось "
                        f"{item_vectors.shape[0]}"
                    )
            except ImportError:
                logger.warning("faiss не установлен — поиск через numpy")
                index = None
            except Exception as exc:  # noqa: BLE001 — битый индекс не должен ронять старт
                logger.warning("FAISS-индекс не прочитан (%s) — поиск через numpy", exc)
                index = None

        meta_path = artifacts_dir / "lightfm_meta.json"
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists()
            else {}
        )
        return cls(
            item_vectors=item_vectors,
            user_vectors=user_vectors,
            user_biases=user_biases,
            movie_ids=movie_ids,
            user_ids=user_ids,
            index=index,
            meta=meta,
            cooldown=ShowHistory(redis_url, cooldown_ttl),
        )

    # ------------------------------------------------------------------ #
    # Поиск
    # ------------------------------------------------------------------ #
    def _search(self, vector: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Топ-``k`` индексов фильмов по скалярному произведению."""
        k = max(1, min(k, self.n_items))
        query = np.ascontiguousarray(vector.reshape(1, -1), dtype=np.float32)
        if self._index is not None:
            scores, indices = self._index.search(query, k)
            return indices[0].astype(np.int64, copy=False), scores[0]
        scores = self._item_vectors @ query[0]
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return top, scores[top]

    def _search_allowed(
        self,
        vector: np.ndarray,
        k: int,
        positions: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Топ-``k`` только среди разрешённых позиций каталога.

        FAISS ``IndexFlatIP`` не умеет фильтровать по подмножеству, а брать
        верхушку полного поиска нельзя: фильм нужной страны может лежать ниже.
        Поэтому при активных фильтрах считаем скалярное произведение по всем
        87 585 векторам (5,7 млн умножений) и выбираем из маски.
        """
        k = max(1, min(k, int(positions.size)))
        scores = self._item_vectors @ np.asarray(vector, dtype=np.float32)
        subset = scores[positions]
        if positions.size <= k:
            order = np.argsort(-subset)
            return positions[order], subset[order]
        top = np.argpartition(-subset, k - 1)[:k]
        top = top[np.argsort(-subset[top])]
        return positions[top], subset[top]

    def _lookup_user(self, user_id: int) -> int | None:
        position = int(np.searchsorted(self._user_ids, user_id))
        if position < self._user_ids.size and int(self._user_ids[position]) == user_id:
            return position
        return None

    async def _profile(
        self, session: AsyncSession, user_id: int
    ) -> tuple[np.ndarray, float] | None:
        """Вектор пользователя: обученный LightFM или собранный из его оценок."""
        position = self._lookup_user(user_id)
        if position is not None:
            vector = np.array(self._user_vectors[position], dtype=np.float32, copy=True)
            # Пользователь мог не иметь ни одной оценки ≥ порога: его вектор
            # остался нулевым. Тогда честнее собрать профиль из оценок.
            if float(np.linalg.norm(vector[:-1])) > 1e-6:
                return vector, float(self._user_biases[position])
        return await self._cold_profile(session, user_id)

    async def _cold_profile(
        self, session: AsyncSession, user_id: int
    ) -> tuple[np.ndarray, float] | None:
        """Холодный старт: среднее векторов оценённых фильмов с весами оценок.

        Пользователь, зарегистрировавшийся после обучения, отсутствует в
        ``user_embeddings.npy``, но его 5–10 оценок уже лежат в БД. Взвешенное
        среднее ``q_i`` даёт осмысленный профиль без переобучения модели.
        """
        rows = (
            await session.execute(
                select(Rating.movie_id, Rating.score)
                .where(Rating.user_id == user_id)
                .order_by(Rating.timestamp.desc().nulls_last(), Rating.id.desc())
                .limit(COLD_PROFILE_LIMIT)
            )
        ).all()
        if not rows:
            return None

        raw_ids = np.fromiter((int(row[0]) for row in rows), dtype=np.int64, count=len(rows))
        positions = np.searchsorted(self._movie_ids, raw_ids)
        inside = positions < self._movie_ids.size
        positions = np.clip(positions, 0, self._movie_ids.size - 1)
        found = inside & (self._movie_ids[positions] == raw_ids)
        if not found.any():
            return None

        vectors = np.asarray(self._item_vectors[positions[found], :-1], dtype=np.float32)
        weights = np.asarray(
            [float(row[1]) - 3.0 for row, ok in zip(rows, found.tolist()) if ok],
            dtype=np.float32,
        )
        total = float(np.abs(weights).sum())
        if total < 1e-6:
            profile = vectors.mean(axis=0)
        else:
            profile = (vectors * weights[:, None]).sum(axis=0) / total

        norm = float(np.linalg.norm(profile))
        if norm > 0:
            profile = profile / norm
        vector = np.concatenate([profile, np.ones(1, dtype=np.float32)])
        return vector.astype(np.float32, copy=False), 0.0

    async def _rated_movie_ids(self, session: AsyncSession, user_id: int) -> set[int]:
        """Уже оценённые фильмы — их бессмысленно рекомендовать повторно."""
        rows = await session.scalars(
            select(Rating.movie_id).where(Rating.user_id == user_id)
        )
        return set(rows.all())

    # ------------------------------------------------------------------ #
    # Выдача
    # ------------------------------------------------------------------ #
    async def recommend(
        self,
        session: AsyncSession,
        user_id: int,
        *,
        top_k: int = 7,
        exclude_ids: set[int] | None = None,
        unavailable: frozenset[int] | set[int] = frozenset(),
        seed: int | None = None,
        allowed_ids: set[int] | None = None,
    ) -> list[ScoredItem] | None:
        """Собрать ``top_k`` рекомендаций.

        ``allowed_ids`` ограничивает пул кандидатов (фильтры ленты); ``None`` —
        без ограничений. ``None`` в результате — персональный профиль построить
        нельзя (нет ни обученного вектора, ни оценок); вызывающий код
        откатывается на популярное.
        """
        top_k = max(1, int(top_k))
        profile = await self._profile(session, user_id)
        if profile is None:
            return None
        vector, user_bias = profile

        allowed_positions: np.ndarray | None = None
        if allowed_ids is not None:
            if not allowed_ids:
                return None
            allowed = np.fromiter(
                allowed_ids, dtype=np.int64, count=len(allowed_ids)
            )
            positions = np.searchsorted(self._movie_ids, allowed)
            positions = np.clip(positions, 0, self.n_items - 1)
            found = self._movie_ids[positions] == allowed
            allowed_positions = np.unique(positions[found])
            if allowed_positions.size == 0:
                return None

        excluded: set[int] = set(unavailable)
        if exclude_ids:
            excluded |= {int(movie_id) for movie_id in exclude_ids}
        excluded |= await self._rated_movie_ids(session, user_id)
        excluded |= await self.cooldown.recent(user_id)

        k = min(
            self.n_items,
            max(SEARCH_POOL, top_k * 10, len(excluded) + CANDIDATE_LIMIT),
        )
        if allowed_positions is not None:
            indices, scores = self._search_allowed(vector, k, allowed_positions)
        else:
            indices, scores = self._search(vector, k)

        candidates: list[ScoredItem] = []
        for index, score in zip(indices.tolist(), scores.tolist()):
            movie_id = int(self._movie_ids[index])
            if movie_id in excluded:
                continue
            candidates.append(ScoredItem(movie_id=movie_id, score=float(score) + user_bias))
            if len(candidates) >= CANDIDATE_LIMIT:
                break

        if not candidates:
            return None

        picked = select_explore_exploit(candidates, top_k, random.Random(seed))
        await self.cooldown.record(user_id, [item.movie_id for item in picked])
        return picked
