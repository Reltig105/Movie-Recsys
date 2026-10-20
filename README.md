# 🎬 Rexter — сервис персональных рекомендаций кино

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
├── backend/                      # Единый бэкенд (FastAPI + PostgreSQL, только asyncpg)
│   ├── api.py                    # REST API: аутентификация, оценки, лента, профиль
│   ├── database.py               # Async-движок (только PostgreSQL), Base, get_session
│   ├── models.py                 # ORM-модели: Movie, User, Rating
│   ├── tmdb.py                   # Клиент TMDB (async aiohttp) и links.csv
│   ├── load_movielens.py         # Пакетная загрузка MovieLens 32M через asyncpg COPY
│   ├── enrich_movies.py          # Обогащение каталога TMDB (asyncio + Semaphore 40)
│   ├── refresh_stats.py          # movie_stats (materialized view) по 32M оценок
│   ├── ml_pipeline/              # Офлайн-матрицы для LightFM (scipy.sparse)
│   │   └── build_features.py     # interactions.npz + item_features.npz + маппинги id
│   ├── download_data.py          # Скачивание и распаковка MovieLens 32M
│   └── requirements.txt          # fastapi, uvicorn, sqlalchemy, asyncpg, aiohttp, pydantic
├── api.py                        # Шим: реэкспорт backend.api:app
├── docker-compose.yml            # PostgreSQL 16 (rexter_db)
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

## 🗄️ Слой данных: PostgreSQL + SQLAlchemy 2.0 (async) + TMDB

Слой данных — асинхронный SQLAlchemy 2.0 (`backend/database.py`, `backend/models.py`)
поверх **PostgreSQL** через драйвер `asyncpg`. Модели: `Movie`, `User`, `Rating`.
SQLite и другие драйверы не поддерживаются: `database.py` падает при импорте,
если строка подключения не начинается с `postgresql+asyncpg://` (fail fast, без
«тихого» fallback). Строка задаётся `REXTER_DATABASE_URL` / `DATABASE_URL`, по
умолчанию — контейнер из `docker-compose.yml`.

```bash
# 1. Поднять PostgreSQL
docker compose up -d

# 2. Установить зависимости
pip install -r backend/requirements.txt

# 3. Задать ключ TMDB
$env:TMDB_API_KEY = "ваш_ключ"          # Windows PowerShell
# export TMDB_API_KEY="ваш_ключ"        # Linux/macOS

# 4. Полный датасет MovieLens 32M (87 585 фильмов + 32 млн оценок).
# load_movielens заливает строки «сырым» COPY, а индексы, UNIQUE и FK на ratings
# строит уже ПОСЛЕ заливки — production-приём для десятков миллионов строк.
python -m backend.load_movielens --reset    # пакетный asyncpg COPY
python -m backend.enrich_movies             # TMDB-обогащение каталога (async)
python -m backend.enrich_movies --missing   # добор постеров/описаний (англ. фолбэк)
python -m backend.refresh_stats             # movie_stats: агрегация 32M ratings

# 5. Поднять API и проверить связку
uvicorn backend.api:app --host 127.0.0.1 --port 8000
# GET http://127.0.0.1:8000/api/v1/movies
```

`load_movielens` заливает данные нативным `asyncpg COPY` (десятки тыс. строк/с),
`enrich_movies` — полностью асинхронный (`asyncio` + `aiohttp`,
`asyncio.Semaphore(40)`), идемпотентный и возобновляемый: повторный запуск
дообрабатывает только фильмы без постера, а `--missing` — ещё и фильмы без
описания. Маппинг MovieLens→TMDB берётся из официального `data/links.csv`
(не хардкодится). Если `overview` в основной локали (`--language`, по умолчанию
`ru-RU`) пуст, описание подтягивается из английской локали (`--no-fallback`
отключает это). Название обогащение не подменяет: в `movies.title` остаётся
английское название из `movies.csv`, TMDB-овское пишется только в пустое поле
(так же и постер/описание/жанры/режиссёр/актёры — пустое значение не затирает
сохранённое).

Если постер или описание так и не нашлись, включается добор по названию: строгий
поиск `GET /search/movie` по названию и году (исправляет случай, когда
`links.csv` ссылается на «пустой» дубликат TMDB, а соседняя запись с тем же
названием и годом имеет постер). Найденная запись **дополняет** основную только
пустыми полями — название никогда не подменяется. Фильмы, у которых постер
отсутствует, но остальные поля есть, тоже записываются: раньше запись целиком
пропускалась, если у TMDB не было картинки.

Окончательный ответ TMDB (200/404) фиксируется в `movies.tmdb_checked_at`,
поэтому `--missing` не дёргает повторно фильмы, для которых на TMDB данных нет
вовсе; сетевые сбои не помечаются и подбираются следующим запуском. Обновление
аддитивное (`COALESCE`) — пустое значение из TMDB не затирает уже сохранённое.

> На живой базе, созданной до появления `tmdb_checked_at`, колонку нужно добавить
> один раз: `ALTER TABLE movies ADD COLUMN IF NOT EXISTS tmdb_checked_at timestamptz;`
> (при `load_movielens --reset` она создаётся из `backend/models.py` автоматически).

Оценки хранятся **без округления**: шкала MovieLens — 0.5…5.0 с шагом 0.5, поэтому
`Rating.score` объявлен как `double precision` (целочисленный тип терял 31% значений).
Если база заливалась старой версией скрипта, тип и значения обновляются так:

```bash
# 1. ALTER COLUMN TYPE блокируется materialized view, поэтому сначала удаляем её
docker exec rexter-postgres psql -U postgres -d rexter_db \
  -c "DROP MATERIALIZED VIEW IF EXISTS movie_stats;" \
  -c "ALTER TABLE ratings ALTER COLUMN score TYPE double precision USING score::double precision;"

# 2. TRUNCATE + COPY заново: каталог и TMDB-обогащение не затрагиваются
python -m backend.load_movielens --reload-ratings

# 3. Вернуть предрасчитанную статистику
python -m backend.refresh_stats
```

`--reload-ratings` снимает индексы и констрейнты `ratings`, делает `TRUNCATE`
(дешевле 32 млн `DELETE`), заливает `ratings.csv` через `COPY` и строит объекты
обратно — тем же путём, что и первичная загрузка.

Агрегаты по оценкам не считаются в API: `python -m backend.refresh_stats`
создаёт materialized view `movie_stats(movie_id, avg_rating, ratings_count)`
одним `GROUP BY` по 32M строк и обновляет её через
`REFRESH MATERIALIZED VIEW CONCURRENTLY` (уникальный индекс по `movie_id`
позволяет обновлять без блокировки чтений). `/api/v1/feed` читает только
`movies` + `movie_stats` и к `ratings` не обращается.

### Эндпоинты `backend/api.py`

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/health` | Health-check |
| `GET` | `/api/v1/movies?limit=` | Первые N фильмов (смоук-тест данных) |
| `POST` | `/register` | Регистрация пользователя |
| `POST` | `/login` | Вход (проверка пароля) |
| `GET` | `/user/{user_id}` | Профиль пользователя |
| `POST` | `/rate` | Сохранить оценку (upsert) |
| `GET` | `/user/{user_id}/history` | История оценок |
| `GET` | `/search?query=` | Поиск фильмов по названию |
| `GET` | `/recommend/{user_id}` | Рекомендации (hot/cold start) |
| `GET` | `/api/v1/calibration-movies` | Фильмы для онбординга |
| `POST` | `/api/v1/rate` | Оценка из v1-фронтенда |
| `GET` | `/api/v1/feed` | Лента-карусель |
| `GET` | `/api/v1/profile` | Профиль: статистика, жанры, история |

Корневой `api.py` — тонкий шим, реэкспортирующий `backend.api:app`, поэтому
запуск `uvicorn api:app` также работает. Пароли хранятся как PBKDF2-HMAC-SHA256
(только stdlib), схема создаётся автоматически на старте приложения.

## 🧩 Офлайн-данные для LightFM (`backend/ml_pipeline`)

```bash
python -m backend.ml_pipeline.build_features            # ~4 мин, ~1 ГБ RAM
python -m backend.ml_pipeline.build_features --limit 2000000 --chunk-size 500000
```

Скрипт читает PostgreSQL курсором (asyncpg) порциями и складывает в
`data/processed/` готовые к обучению разреженные матрицы:

| Файл | Что внутри |
| --- | --- |
| `interactions.npz` | CSR `users × movies` (200 948 × 87 585), значения — `score` (float32) |
| `item_features.npz` | CSR `movies × features` (87 585 × 131 717), multi-hot жанров, режиссёра и актёров |
| `user_mapping.pkl` | `{"raw_to_index": {…}, "index_to_raw": np.int32[…]}` |
| `movie_mapping.pkl` | то же для фильмов |
| `item_feature_names.pkl` | имена колонок `item_features` (`genre:*`, `director:*`, `cast:*`) |

`movie_id` MovieLens не непрерывны (до 292 757 при 87 585 фильмах), поэтому обе
оси перенумерованы в `0…N-1`; обратное преобразование — `index_to_raw[индекс]`.
Жанры/режиссёр/актёры — строки через запятую из `movies`; отсутствующее значение
превращается в признак `<unknown>` соответствующего типа, чтобы у фильма не
осталось пустой строки признаков. NaN-ы (фильмы без режиссёра/актёров) и пустые
жанры обрабатываются так же.

Память: 32 млн взаимодействий — три массива `int32/int32/float32` (≈ 384 МБ)
плюс столько же на момент сборки CSR; порция pandas-фрейма (1 млн строк)
освобождается сразу. Артефакты не коммитятся — `data/` в `.gitignore`.

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
