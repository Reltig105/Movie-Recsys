

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

from ml.src.config import TEST_PATH, TRAIN_PATH

# Колонки, обязательные во входном датафрейме с оценками.
REQUIRED_COLUMNS: tuple[str, ...] = ("user_id", "item_id", "rating")


def load_train_ratings(path: Path | str = TRAIN_PATH) -> pd.DataFrame:
    
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Файл с тренировочными оценками не найден: {csv_path}. "
            "Сначала сформируйте сплиты (ml/src/split_data.py)."
        )
    return pd.read_csv(csv_path)


class SVDRecommender:
   
    def __init__(self, n_components: int = 20, random_state: int = 42) -> None:
        if n_components < 1:
            raise ValueError(f"n_components должен быть >= 1, получено {n_components}.")
        if random_state < 0:
            raise ValueError(f"random_state должен быть >= 0, получено {random_state}.")

        self.n_components: int = n_components
        self.random_state: int = random_state

        # Маппинги id <-> индекс.
        self.user2idx: dict[int, int] = {}
        self.idx2user: dict[int, int] = {}
        self.item2idx: dict[int, int] = {}
        self.idx2item: dict[int, int] = {}

        # Обучаемые артефакты.
        self.interaction_matrix: csr_matrix | None = None
        self.user_embeddings: np.ndarray | None = None
        self.item_embeddings: np.ndarray | None = None
        self.explained_variance_ratio: np.ndarray | None = None

        self.is_fitted: bool = False
        self._rated_items: list[np.ndarray] = []
        self._popular_items: np.ndarray = np.empty(0, dtype=np.int64)

    # ------------------------------------------------------------------ #
    # Обучение
    # ------------------------------------------------------------------ #
    def fit(self, train_df: pd.DataFrame) -> "SVDRecommender":
       
        self._validate_ratings(train_df)
        self._build_mappings(train_df)

        matrix = self._build_interaction_matrix(train_df)
        n_users, n_items = matrix.shape
        if self.n_components > min(n_users, n_items):
            raise ValueError(
                f"n_components={self.n_components} превосходит размерность матрицы "
                f"({n_users} x {n_items}). Уменьшите n_components."
            )

        svd = TruncatedSVD(n_components=self.n_components, random_state=self.random_state)
        self.user_embeddings = svd.fit_transform(matrix)  # (n_users, n_components)
        self.item_embeddings = svd.components_.T  # (n_items, n_components)
        self.explained_variance_ratio = svd.explained_variance_ratio_

        self.interaction_matrix = matrix
        self._build_rated_items(matrix)
        self._compute_popularity(train_df)
        self.is_fitted = True
        return self

    def _validate_ratings(self, df: pd.DataFrame) -> None:
        """Проверить корректность входного датафрейма с оценками."""
        if df is None or df.empty:
            raise ValueError("Тренировочный датафрейм пуст — обучать модель нечем.")

        missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise ValueError(
                f"В тренировочном датафрейме отсутствуют обязательные колонки: {missing}. "
                f"Ожидается {list(REQUIRED_COLUMNS)}."
            )

    def _build_mappings(self, df: pd.DataFrame) -> None:
        """Построить маппинги id <-> индекс по уникальным значениям сплита."""
        users = np.sort(df["user_id"].unique())
        items = np.sort(df["item_id"].unique())

        self.user2idx = {int(uid): idx for idx, uid in enumerate(users)}
        self.idx2user = {idx: int(uid) for idx, uid in enumerate(users)}
        self.item2idx = {int(iid): idx for idx, iid in enumerate(items)}
        self.idx2item = {idx: int(iid) for idx, iid in enumerate(items)}

    def _build_interaction_matrix(self, df: pd.DataFrame) -> csr_matrix:
        """Построить разреженную CSR-матрицу оценок размера (n_users, n_items)."""
        rows = df["user_id"].map(self.user2idx).to_numpy(dtype=np.int64)
        cols = df["item_id"].map(self.item2idx).to_numpy(dtype=np.int64)
        values = df["rating"].to_numpy(dtype=np.float32)

        matrix = csr_matrix(
            (values, (rows, cols)),
            shape=(len(self.user2idx), len(self.item2idx)),
        )
        # Дубликаты пар (user, item) суммируются при сборке CSR.
        matrix.sum_duplicates()
        return matrix

    def _build_rated_items(self, matrix: csr_matrix) -> None:
        """Запомнить индексы фильмов, которые каждый пользователь оценил в train."""
        indices = matrix.indices
        indptr = matrix.indptr
        self._rated_items = [
            indices[indptr[user_idx] : indptr[user_idx + 1]].copy()
            for user_idx in range(matrix.shape[0])
        ]

    def _compute_popularity(self, df: pd.DataFrame) -> None:
        
        stats = df.groupby("item_id")["rating"].agg(["count", "mean"])
        counts = stats["count"].to_numpy(dtype=np.float64)
        means = stats["mean"].to_numpy(dtype=np.float64)
        global_mean = float(df["rating"].mean())
        m = float(counts.mean()) if counts.size else 0.0

        weighted = (counts / (counts + m)) * means + (m / (counts + m)) * global_mean

        scores = np.full(len(self.item2idx), -np.inf, dtype=np.float64)
        item_indices = [self.item2idx[int(iid)] for iid in stats.index]
        scores[item_indices] = weighted

        # Индексы фильмов, отсортированные по убыванию популярности.
        self._popular_items = np.argsort(-scores).astype(np.int64)

    # ------------------------------------------------------------------ #
    # Инференс
    # ------------------------------------------------------------------ #
    def recommend(self, user_id: int, top_k: int = 10) -> list[int]:
        """Вернуть top-K рекомендаций фильмов для пользователя.

        Скор фильма = скалярное произведение латентного вектора пользователя и
        латентного вектора фильма. Фильмы, уже оценённые пользователем в train,
        маскируются значением ``-inf``.

        Args:
            user_id: Исходный идентификатор пользователя.
            top_k: Количество рекомендаций.

        Returns:
            Список исходных ``item_id`` длиной не более ``top_k``.

        Raises:
            RuntimeError: Если модель ещё не обучена.
            ValueError: Если ``top_k < 1`` или ``user_id`` не является целым числом.
        """
        self._check_fitted()
        if not isinstance(user_id, (int, np.integer)):
            raise TypeError(f"user_id должен быть целым числом, получено {type(user_id).__name__}.")
        if top_k < 1:
            raise ValueError(f"top_k должен быть >= 1, получено {top_k}.")

        # Edge case: холодный пользователь (нет истории в train) -> популярное.
        if user_id not in self.user2idx:
            return self._popular_recommendations(top_k)

        user_idx = self.user2idx[int(user_id)]
        scores = self._scores_for_user(user_idx)

        # Маскируем фильмы, которые пользователь уже оценил в train.
        rated = self._rated_items[user_idx]
        if rated.size:
            scores = scores.copy()
            scores[rated] = -np.inf

        top_indices = self._top_k_indices(scores, top_k)
        return [self.idx2item[int(idx)] for idx in top_indices]

    def _scores_for_user(self, user_idx: int) -> np.ndarray:
        """Скоры всех фильмов для пользователя (скалярное произведение векторов)."""
        assert self.user_embeddings is not None and self.item_embeddings is not None
        user_vector = self.user_embeddings[user_idx]  # (n_components,)
        return user_vector @ self.item_embeddings.T  # (n_items,)

    @staticmethod
    def _top_k_indices(scores: np.ndarray, top_k: int) -> np.ndarray:
        """Индексы top-K максимальных скоров с игнорированием замаскированных (-inf)."""
        valid = np.flatnonzero(np.isfinite(scores))
        if valid.size <= top_k:
            return valid[np.argsort(-scores[valid])]

        # Частичная сортировка O(n) вместо полной: берём top_k, затем сортируем их.
        candidates = np.argpartition(-scores, top_k)[:top_k]
        return candidates[np.argsort(-scores[candidates])]

    def _popular_recommendations(self, top_k: int) -> list[int]:
        """Топ популярных фильмов для пользователя без истории взаимодействий."""
        top_indices = self._popular_items[:top_k]
        return [self.idx2item[int(idx)] for idx in top_indices]

    def _check_fitted(self) -> None:
        """Убедиться, что модель обучена."""
        if not self.is_fitted:
            raise RuntimeError("Модель не обучена. Сначала вызовите fit(train_df).")


def main() -> None:
    """Обучить модель на train-сплите и показать рекомендации для тестового юзера."""
    train_df = load_train_ratings()
    model = SVDRecommender(n_components=20, random_state=42).fit(train_df)

    print(f"Пользователей в train: {len(model.user2idx)}")
    print(f"Фильмов в train: {len(model.item2idx)}")
    print(f"Матрица взаимодействий: {model.interaction_matrix.shape}")
    explained = float(model.explained_variance_ratio.sum()) if model.explained_variance_ratio is not None else 0.0
    print(f"Доля объяснённой дисперсии (20 компонент): {explained:.4f}\n")

    test_df = pd.read_csv(TEST_PATH)
    test_user_id = next(
        (int(uid) for uid in test_df["user_id"].drop_duplicates() if int(uid) in model.user2idx),
        None,
    )
    if test_user_id is None:
        print("В тестовом сплите нет пользователей, присутствующих в train.")
        return

    top_5 = model.recommend(user_id=test_user_id, top_k=5)
    print(f"Топ-5 рекомендаций для тестового пользователя {test_user_id}:")
    for rank, item_id in enumerate(top_5, start=1):
        print(f"  [{rank}] item_id={item_id}")


if __name__ == "__main__":
    main()
