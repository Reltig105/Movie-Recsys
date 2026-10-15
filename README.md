# 🎬 Movie-Recsys — Movie Recommendation Service

Минималистичный сервис персональных рекомендаций кино. Проект развивается по
стратегии **ML-First**: сначала сильные офлайн-модели, затем масштабирование.

Датасет: [MovieLens 100k](https://grouplens.org/datasets/movielens/100k/).

## ✨ Возможности

- **EDA**: анализ разреженности матрицы оценок, распределения рейтингов, «длинного хвоста».
- **Обогащение данных**: подтягивание синопсисов, постеров и жанров через TMDB API.
- **Content-Based модель**: профили фильмов (TF-IDF по описаниям) и косинусное сходство для поиска «похожих фильмов».
- **Бейзлайн**: предсказание по среднему рейтингу фильма (`ItemMeanBaseline`) с оценкой RMSE.
- **Матричная факторизация**: `SVDRecommender` на `TruncatedSVD` с генерацией top-K рекомендаций и маскированием уже просмотренного.
- **Streamlit UI**: минималистичный веб-интерфейс ввода `user_id` и вывода рекомендаций.

## 🗂️ Структура проекта

```
movie-recsys/
├── app.py                        # Streamlit-интерфейс (пока на mock-заглушке)
├── requirements.txt
├── DESIGN.md                     # System Design: цели, метрики, архитектура, схема данных
├── ROADMAP.md                    # План развития на 10 месяцев
├── LOG.md                        # Инженерный журнал
├── .opencode/agents/architect.md # read-only агент-архитектор
└── ml/
    ├── data/
    │   ├── download_data-related скрипты и сырые данные MovieLens
    │   └── fetch_tmdb.py         # загрузка метаданных из TMDB
    └── src/
        ├── config.py             # пути к данным
        ├── download_data.py      # скачивание и распаковка ml-100k
        ├── explore_data.py       # EDA: sparsity, распределения, топ фильмов
        ├── split_data.py         # хронологическое разбиение train/test
        ├── metrics.py            # RMSE
        ├── data/db_init.py       # инициализация SQLite-хранилища метаданных
        └── models/
            ├── baseline.py       # ItemMeanBaseline
            ├── content_model.py  # content-based (TF-IDF + cosine)
            └── svd_model.py      # SVDRecommender (матричная факторизация)
```

## 🚀 Установка

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate      # Linux/macOS
pip install -r requirements.txt
```

## 🧭 Быстрый старт

Все команды запускаются **из корня репозитория**.

```bash
# 1. Скачать датасет MovieLens 100k
python -m ml.src.download_data

# 2. Разведочный анализ
python -m ml.src.explore_data

# 3. Хронологическое разбиение на train/test
python -m ml.src.split_data

# 4. Бейзлайн (ItemMeanBaseline)
python -m ml.src.models.baseline

# 5. Матричная факторизация + рекомендации
python -m ml.src.models.svd_model

# 6. Веб-интерфейс
streamlit run app.py
```

## 🧠 Модели

| Модель | Файл | Что делает |
| --- | --- | --- |
| `ItemMeanBaseline` | `ml/src/models/baseline.py` | Предсказание рейтинга по среднему фильма + fallback на глобальное среднее |
| Content-Based | `ml/src/models/content_model.py` | TF-IDF по синопсисам, косинусное сходство, поиск похожих фильмов |
| `SVDRecommender` | `ml/src/models/svd_model.py` | Матричная факторизация (`TruncatedSVD`, 20 компонент), top-K рекомендации, cold-start по популярности |

## 📊 Метрики качества

- **RMSE / MAE** — точность предсказания рейтингов.
- **HitRate@K / NDCG@K** — качество ранжирования.
- **Diversity** — разнообразие выдачи.

## 🗺️ Дальнейшие шаги

Полный план — в [ROADMAP.md](ROADMAP.md), проектные решения — в [DESIGN.md](DESIGN.md).

Ближайшее:
- [ ] Подключить реальную `SVDRecommender` в `app.py` вместо mock-заглушки.
- [ ] Метрики ранжирования (`HitRate@K`, `NDCG@K`).
- [ ] Матричная факторизация на PyTorch и двухуровневый пайплайн (retrieval → reranking).
