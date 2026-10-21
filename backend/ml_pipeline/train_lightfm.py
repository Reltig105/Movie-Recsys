"""Обучение LightFM (WARP) и экспорт артефактов для онлайн-инференса.

Офлайн-контур целиком::

    interactions.npz + item_features.npz
        ──► LightFM(loss="warp", no_components=64)
        ──► user_embeddings.npy / item_embeddings.npy
        ──► faiss.index (IndexFlatIP) + lightfm_meta.json

Почему WARP: задача — не предсказать точную оценку, а отсортировать каталог.
WARP (Weighted Approximate-Rank Pairwise) штрафует модель за «плохой» фильм,
который оказался выше «хорошего», и потому оптимизирует именно порядок выдачи.

Смещения. Скор LightFM — это ``p_u · q_i + b_u + b_i``. Чтобы онлайн-поиск
остался одним скалярным произведением, ``b_i`` дописывается последней
координатой вектора фильма, а у вектора пользователя эта координата равна
``1.0``. ``b_u`` одинаков для всех фильмов пользователя и на порядок выдачи не
влияет — он хранится отдельно (``user_biases.npy``) и прибавляется к скору.

Почему внутреннее произведение, а не косинус: WARP обучает именно скалярное
произведение, а L2-нормализация уничтожила бы выученную «силу» вектора и
смещение. FAISS ``IndexFlatIP`` считает ровно ту величину, которую оптимизировал
LightFM, поэтому ранжирование совпадает с ``model.predict``.

Запуск (Linux, OpenMP; на Windows LightFM 1.17 падает в WARP — используйте
Docker-образ из ``backend/ml_pipeline/Dockerfile``)::

    docker build -t rexter-train backend/ml_pipeline
    docker run --rm -v "%CD%:/app" rexter-train \
        python backend/ml_pipeline/train_lightfm.py --epochs 10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import sparse

logger = logging.getLogger("rexter.train")

PROCESSED_DIR = Path("data/processed")
ARTIFACTS_DIR = Path("data/artifacts")


def _fmt(value: int) -> str:
    """Число с пробелами между разрядами (``32 000 213``)."""
    return f"{value:,}".replace(",", " ")


def _size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024


def _load_csr(path: Path) -> sparse.csr_matrix:
    """Прочитать CSR из ``.npz`` и привести индексы к каноническому виду."""
    if not path.exists():
        raise FileNotFoundError(
            f"нет {path} — сначала соберите матрицы: "
            "python -m backend.ml_pipeline.build_features"
        )
    matrix = sparse.load_npz(path).tocsr()
    matrix.sort_indices()
    return matrix


def _load_ids(path: Path) -> np.ndarray:
    """Внешние id (порядок = внутренние индексы).

    ``.npy`` — основной формат: его читает и обучающий контейнер на numpy 1.26.
    ``.pkl`` оставлен как резерв для артефактов, собранных старой версией.
    """
    npy_path = path.with_suffix(".npy")
    if npy_path.exists():
        return np.load(npy_path).astype(np.int64, copy=False)
    if not path.exists():
        raise FileNotFoundError(
            f"нет {npy_path} — пересоберите матрицы: "
            "python -m backend.ml_pipeline.build_features"
        )
    import pickle

    import numpy.core  # noqa: F401  (нужен для pickle из numpy 2.x)
    import numpy.core.multiarray  # noqa: F401
    import numpy.core.numeric  # noqa: F401

    # Pickle из numpy 2.x ссылается на ``numpy._core``; в numpy 1.x его нет.
    sys.modules.setdefault("numpy._core", np.core)
    sys.modules.setdefault("numpy._core.multiarray", np.core.multiarray)
    sys.modules.setdefault("numpy._core.numeric", np.core.numeric)
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    return np.asarray(payload["index_to_raw"], dtype=np.int64)


def train(
    processed_dir: Path,
    artifacts_dir: Path,
    epochs: int,
    components: int,
    threads: int,
    learning_rate: float,
    item_alpha: float,
    user_alpha: float,
    max_sampled: int,
    seed: int,
    limit_users: int | None,
    positive_threshold: float | None,
) -> int:
    """Обучить модель, экспортировать эмбеддинги и FAISS-индекс."""
    from lightfm import LightFM
    import lightfm as lightfm_pkg

    started = time.perf_counter()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Читаю матрицы из %s", processed_dir)
    interactions = _load_csr(processed_dir / "interactions.npz")
    item_features = _load_csr(processed_dir / "item_features.npz")
    user_ids = _load_ids(processed_dir / "user_mapping.pkl")
    movie_ids = _load_ids(processed_dir / "movie_mapping.pkl")

    if interactions.shape[1] != item_features.shape[0]:
        raise ValueError(
            "размеры не совпадают: interactions "
            f"{interactions.shape}, item_features {item_features.shape}"
        )
    if interactions.shape[0] != user_ids.size:
        raise ValueError("interactions и user_mapping рассинхронизированы")
    if interactions.shape[1] != movie_ids.size:
        raise ValueError("interactions и movie_mapping рассинхронизированы")

    if limit_users is not None:
        limit = max(1, min(limit_users, interactions.shape[0]))
        interactions = interactions[:limit]
        user_ids = user_ids[:limit]
        logger.warning("Ограничение: только %s пользователей (проверка)", _fmt(limit))

    # WARP — ранжирующая функция потерь: «положительные» взаимодействия важны
    # фактом наличия, а не значением оценки. Оценки ниже порога (нейтральные и
    # отрицательные) в обучение не идут, иначе фильм с оценкой 1.0 тянул бы
    # профиль пользователя к себе как «хороший».
    if positive_threshold is not None:
        coo = interactions.tocoo()
        mask = coo.data >= positive_threshold
        interactions = sparse.csr_matrix(
            (
                np.ones(int(mask.sum()), dtype=np.float32),
                (coo.row[mask], coo.col[mask]),
            ),
            shape=interactions.shape,
        )
        interactions.sort_indices()
        logger.info(
            "Порог «понравилось» ≥ %.1f: %s из %s взаимодействий",
            positive_threshold,
            _fmt(interactions.nnz),
            _fmt(int(mask.size)),
        )

    logger.info(
        "Данные: %s × %s, взаимодействий — %s, признаков — %s",
        _fmt(interactions.shape[0]),
        _fmt(interactions.shape[1]),
        _fmt(interactions.nnz),
        _fmt(item_features.shape[1]),
    )

    model = LightFM(
        loss="warp",
        no_components=components,
        learning_rate=learning_rate,
        item_alpha=item_alpha,
        user_alpha=user_alpha,
        max_sampled=max_sampled,
        random_state=seed,
    )

    logger.info(
        "Обучение: epochs=%s, d=%s, threads=%s, lr=%s, max_sampled=%s",
        epochs,
        components,
        threads,
        learning_rate,
        max_sampled,
    )
    fit_started = time.perf_counter()
    model.fit(
        interactions,
        item_features=item_features,
        epochs=epochs,
        num_threads=threads,
        verbose=True,
    )
    fit_elapsed = time.perf_counter() - fit_started
    logger.info(
        "Обучение завершено за %.1f мин (%.0f вз/с)",
        fit_elapsed / 60,
        interactions.nnz * epochs / max(fit_elapsed, 1e-9),
    )

    # q_i = Σ e_f по признакам фильма; b_i — тоже линейная функция признаков.
    item_vectors = np.asarray(item_features @ model.item_embeddings, dtype=np.float32)
    item_biases = np.asarray(item_features @ model.item_biases, dtype=np.float32).ravel()
    user_vectors = np.asarray(model.user_embeddings, dtype=np.float32)
    user_biases = np.asarray(model.user_biases, dtype=np.float32).ravel()

    if user_vectors.shape[0] != interactions.shape[0]:
        raise RuntimeError(
            f"эмбеддингов пользователей {user_vectors.shape[0]}, "
            f"а строк {interactions.shape[0]}"
        )

    # Дописываем смещение фильма последней координатой, у пользователя — 1.0.
    item_aug = np.hstack(
        [item_vectors, item_biases.reshape(-1, 1)]
    ).astype(np.float32, copy=False)
    user_aug = np.hstack(
        [user_vectors, np.ones((user_vectors.shape[0], 1), dtype=np.float32)]
    ).astype(np.float32, copy=False)

    # Сверка: свёрнутое смещение не должно менять скор модели.
    rng = np.random.default_rng(seed)
    active_users = np.diff(interactions.indptr) > 0
    sample_pool = np.flatnonzero(active_users)
    if sample_pool.size == 0:
        sample_pool = np.arange(user_aug.shape[0])
    sample_users = rng.choice(sample_pool, size=min(256, sample_pool.size), replace=False)
    sample_items = rng.integers(0, item_aug.shape[0], size=sample_users.size)
    reference = model.predict(
        sample_users,
        sample_items,
        item_features=item_features,
        num_threads=threads,
    )
    folded = (user_aug[sample_users] * item_aug[sample_items]).sum(axis=1)
    folded = folded + user_biases[sample_users]
    max_diff = float(np.abs(reference - folded).max())
    logger.info("Проверка скоринга: max |predict − folded| = %.2e", max_diff)
    if max_diff > 1e-3:
        raise RuntimeError(
            f"эмбеддинги не воспроизводят model.predict (расхождение {max_diff:.4f})"
        )

    # Пользователи без единой оценки ≥ порога не получали градиента: LightFM
    # оставил бы им случайный вектор. Обнуляем — онлайн-контур увидит это и
    # соберёт профиль из фактических оценок пользователя.
    inactive = int((~active_users).sum())
    if inactive:
        user_aug[~active_users] = 0.0
        user_biases[~active_users] = 0.0
        logger.info("Пользователей без положительных оценок: %s", _fmt(inactive))

    np.save(artifacts_dir / "item_embeddings.npy", item_aug)
    np.save(artifacts_dir / "user_embeddings.npy", user_aug)
    np.save(artifacts_dir / "user_biases.npy", user_biases)
    np.save(artifacts_dir / "movie_ids.npy", movie_ids)
    np.save(artifacts_dir / "user_ids.npy", user_ids)

    index_name = "numpy"
    try:
        import faiss

        index = faiss.IndexFlatIP(int(item_aug.shape[1]))
        index.add(np.ascontiguousarray(item_aug))
        faiss.write_index(index, str(artifacts_dir / "faiss.index"))
        index_name = "faiss.IndexFlatIP"
        logger.info("FAISS-индекс: %s векторов размерности %s", _fmt(index.ntotal), index.d)
    except ImportError:  # pragma: no cover — faiss есть в обучающем образе
        logger.warning("faiss не установлен — онлайн-поиск пойдёт через numpy")

    elapsed = time.perf_counter() - started
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lightfm_version": lightfm_pkg.__version__,
        "loss": "warp",
        "components": components,
        "dim": int(item_aug.shape[1]),
        "epochs": epochs,
        "seed": seed,
        "learning_rate": learning_rate,
        "item_alpha": item_alpha,
        "user_alpha": user_alpha,
        "max_sampled": max_sampled,
        "positive_threshold": positive_threshold,
        "users": int(user_aug.shape[0]),
        "items": int(item_aug.shape[0]),
        "interactions": int(interactions.nnz),
        "item_features": int(item_features.shape[1]),
        "item_feature_nnz": int(item_features.nnz),
        "bias_folded": True,
        "index": index_name,
        "fit_seconds": round(fit_elapsed, 1),
        "elapsed_seconds": round(elapsed, 1),
        "numpy_version": np.__version__,
        "python_version": sys.version.split()[0],
    }
    (artifacts_dir / "lightfm_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for name in (
        "user_embeddings.npy",
        "item_embeddings.npy",
        "user_biases.npy",
        "user_ids.npy",
        "movie_ids.npy",
        "faiss.index",
    ):
        path = artifacts_dir / name
        if path.exists():
            logger.info("%s — %.1f МБ", name, _size_mb(path))
    logger.info("Готово за %.1f мин", elapsed / 60)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Обучить LightFM (WARP) и экспортировать эмбеддинги."
    )
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS_DIR)
    parser.add_argument("--epochs", type=int, default=10, help="проходов по данным")
    parser.add_argument("--components", type=int, default=64, help="размерность d")
    parser.add_argument(
        "--threads",
        type=int,
        default=os.cpu_count() or 1,
        help="потоков OpenMP (по умолчанию все ядра)",
    )
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--item-alpha", type=float, default=1e-6)
    parser.add_argument("--user-alpha", type=float, default=1e-6)
    parser.add_argument("--max-sampled", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--limit-users",
        type=int,
        help="обучить только на первых N пользователях (быстрая проверка)",
    )
    parser.add_argument(
        "--positive-threshold",
        type=float,
        default=4.0,
        help="оценки ниже порога не считаются положительными (0 — отключить)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    return train(
        processed_dir=args.processed_dir,
        artifacts_dir=args.artifacts_dir,
        epochs=args.epochs,
        components=args.components,
        threads=args.threads,
        learning_rate=args.learning_rate,
        item_alpha=args.item_alpha,
        user_alpha=args.user_alpha,
        max_sampled=args.max_sampled,
        seed=args.seed,
        limit_users=args.limit_users,
        positive_threshold=args.positive_threshold or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
