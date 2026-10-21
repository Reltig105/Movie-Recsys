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
│   ├── ml_pipeline/              # Офлайн-контур LightFM
│   │   ├── build_features.py     # interactions.npz + item_features.npz + маппинги id
│   │   ├── train_lightfm.py      # WARP-обучение и экспорт эмбеддингов + FAISS
│   │   └── Dockerfile            # Linux-образ для обучения (OpenMP)
│   ├── recommender/              # Онлайн-контур: FAISS, кулдаун, exploit/explore
│   │   ├── engine.py             # инференс по эмбеддингам
│   │   └── cooldown.py           # показы за 24 ч (Redis → память)
│   ├── download_data.py          # Скачивание и распаковка MovieLens 32M
│   └── requirements.txt          # fastapi, uvicorn, sqlalchemy, asyncpg, aiohttp, pydantic
├── api.py                        # Шим: реэкспорт backend.api:app
├── docker-compose.yml            # PostgreSQL 16 (rexter_db) + Redis (кулдаун)
├── deploy/                       # Прод: Caddyfile, systemd-юнит, compose для VPS
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
позволяет обновлять без блокировки чтений). Из `ratings` лента делает только
индексную выборку «что пользователь уже оценил» (для исключения из выдачи),
никаких агрегаций по 32M строк на горячем пути нет. Если артефактов модели нет,
`/api/v1/feed` целиком собирается из `movies` + `movie_stats`, как раньше.

### Эндпоинты `backend/api.py`

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/health` | Health-check |
| `GET` | `/api/v1/movies?limit=` | Первые N фильмов (смоук-тест данных) |
| `POST` | `/register` | Регистрация пользователя (выдаёт `session_token`) |
| `POST` | `/login` | Вход (проверка пароля, выдаёт `session_token`) |
| `POST` | `/logout` | Выход: токен гасится в БД 🔒 |
| `GET` | `/user/{user_id}` | Профиль пользователя |
| `POST` | `/rate` | Сохранить оценку (upsert) 🔒 |
| `GET` | `/user/{user_id}/history` | История оценок 🔒 |
| `GET` | `/search?query=` | Поиск фильмов по названию |
| `GET` | `/recommend/{user_id}` | Рекомендации (hot/cold start) |
| `GET` | `/api/v1/calibration-movies` | Фильмы для онбординга |
| `POST` | `/api/v1/rate` | Оценка из v1-фронтенда 🔒 |
| `GET` | `/api/v1/feed` | Лента-карусель |
| `GET` | `/api/v1/profile` | Профиль: статистика, жанры, история 🔒 |

🔒 — нужен заголовок `X-Session-Token` (токен из `/register` или `/login`), и
только для своей учётной записи. Каталог (лента, рекомендации, поиск) открыт.

Корневой `api.py` — тонкий шим, реэкспортирующий `backend.api:app`, поэтому
запуск `uvicorn api:app` также работает. Пароли хранятся как PBKDF2-HMAC-SHA256
(только stdlib), схема создаётся автоматически на старте приложения.

## 🚀 Публикация в интернете

Полная инструкция по VPS — в [deploy/README.md](deploy/README.md) (DNS, Caddy с
авто-HTTPS, systemd, перенос базы дампом). Кратко, что уже подготовлено:

- FastAPI раздаёт собранный фронтенд из `frontend/dist` с SPA-fallback —
  один origin, без CORS (`npm run build` в `frontend/`);
- `frontend/src/api.js` использует относительный адрес API, а Vite в dev
  проксирует те же пути в uvicorn (`server.proxy`), поэтому `npm run dev`
  работает без настройки;
- запись оценок, профиль и история защищены токеном (`X-Session-Token`),
  вход/регистрация ограничены 10 попытками в минуту с IP;
- `deploy/`: `docker-compose.prod.yml` (Postgres и Redis только на `127.0.0.1`),
  `Caddyfile`, `rexter.service`; секреты — в `.env` (образец `.env.example`).

## 🧩 Рекомендательная система: LightFM (офлайн + онлайн)

### Офлайн-данные (`backend/ml_pipeline/build_features.py`)

```bash
python -m backend.ml_pipeline.build_features            # ~1–2 мин, ~1 ГБ RAM
python -m backend.ml_pipeline.build_features --limit 2000000 --chunk-size 500000
```

Скрипт читает PostgreSQL курсором (asyncpg) порциями и складывает в
`data/processed/` готовые к обучению разреженные матрицы:

| Файл | Что внутри |
| --- | --- |
| `interactions.npz` | CSR `users × movies` (200 949 × 87 585), значения — `score` (float32) |
| `item_features.npz` | CSR `movies × features` (87 585 × 219 302), multi-hot `movie:*`, `genre:*`, `director:*`, `cast:*` |
| `user_mapping.pkl` / `movie_mapping.pkl` | `{"raw_to_index": {…}, "index_to_raw": np.int32[…]}` |
| `user_ids.npy` / `movie_ids.npy` | те же биекции без pickle — их читает обучающий контейнер |
| `item_feature_names.pkl` | имена колонок `item_features` |

`movie_id` MovieLens не непрерывны (до 292 757 при 87 585 фильмах), поэтому обе
оси перенумерованы в `0…N-1`; обратное преобразование — `index_to_raw[индекс]`.
Признаки фильма гибридные: identity `movie:<id>` (модель запоминает конкретный
фильм — качество exploit) плюс контент `genre:*`, `director:*`, `cast:*`.
Отсутствующее значение превращается в `<unknown>` соответствующего типа, чтобы
у фильма не осталось пустой строки признаков.

Память: 32 млн взаимодействий — три массива `int32/int32/float32` (≈ 384 МБ)
плюс столько же на момент сборки CSR; порция pandas-фрейма (1 млн строк)
освобождается сразу. Артефакты не коммитятся — `data/` в `.gitignore`.

### Офлайн-обучение (`train_lightfm.py`)

```bash
# LightFM 1.17 — Cython-расширение 2020 года: на Windows WARP падает с access
# violation, на Linux работает и использует все ядра через OpenMP.
docker build -t rexter-train backend/ml_pipeline
docker run --rm -v "%CD%:/app" rexter-train \
    python backend/ml_pipeline/train_lightfm.py --epochs 10 --components 64
```

Что происходит:

1. WARP (Weighted Approximate-Rank Pairwise) — ранжирующая функция потерь:
   «хорошему» фильму противопоставляется случайный несмотренный, и модель
   штрафуется, если плохой оказался выше. Оценки ниже порога
   `--positive-threshold` (по умолчанию 4.0) в обучение не идут — иначе фильм
   с оценкой 1.0 считался бы «положительным».
2. Вектор фильма не хранится отдельно: `q_i = Σ e_f` по его признакам, поэтому
   новинка без оценок получает осмысленный вектор из жанра/режиссёра/актёров.
3. Смещения складываются так, чтобы онлайн-поиск остался одним скалярным
   произведением: `b_i` — последняя координата вектора фильма, у пользователя
   там `1.0`; `b_u` хранится отдельно.

Экспорт в `data/artifacts/`:

| Файл | Размер | Что внутри |
| --- | --- | --- |
| `user_embeddings.npy` | ~52 МБ | 200 949 × 65 (`p_u` + координата 1.0) |
| `item_embeddings.npy` | ~22 МБ | 87 585 × 65 (`q_i` + `b_i`) |
| `user_biases.npy` | <1 МБ | `b_u` |
| `faiss.index` | ~22 МБ | `IndexFlatIP` по векторам фильмов |
| `movie_ids.npy` / `user_ids.npy` | <1 МБ | обратный маппинг индексов в id |
| `lightfm_meta.json` | — | d, эпохи, порог, nnz, версии, время обучения |

> Перед переобучением остановите API: на Windows он держит `.npy` через `mmap`,
> и контейнер не сможет перезаписать файлы (`OSError: Invalid argument`).
> На Linux файлы заменяются на лету.

### Онлайн-выдача (`backend/recommender`)

1. **Вектор пользователя.** Для обученного — строка из `user_embeddings.npy`.
   Для зарегистрировавшегося после обучения — взвешенное среднее векторов его
   последних оценок (`score − 3`): холодный старт без переобучения.
2. **Поиск.** FAISS `IndexFlatIP` возвращает 500 кандидатов за миллисекунды.
   Внутреннее произведение, а не косинус: WARP обучал именно его, нормализация
   уничтожила бы выученную «силу» вектора и смещение.
3. **Фильтрация.** Убираются уже оценённые фильмы (индексная выборка по
   `ratings`), показанные за 24 часа (Redis sorted set) и фильмы без постера.
4. **Exploit & Explore.** 5 лучших + 2 случайных из хвоста топ-100 — выдача не
   повторяется при каждом обновлении.
5. **Маппинг.** `movie_index → movie_id` через `movie_ids.npy`, один
   `SELECT` по первичному ключу `movies` — и JSON на фронтенд.

Если Redis недоступен или артефактов нет, приложение не падает: кулдаун
переходит в память процесса, а лента — на популярное из `movie_stats`.

## 🧠 Модели

| Модель | Файл | Что делает |
| --- | --- | --- |
| **LightFM (WARP)** | `backend/ml_pipeline/train_lightfm.py` + `backend/recommender/` | Гибридные рекомендации: identity + жанры/режиссёр/актёры, FAISS-поиск, холодный старт |
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
- [x] LightFM (WARP) в проде: офлайн-обучение, FAISS, Redis-кулдаун, exploit/explore.
- [ ] Метрики ранжирования (`HitRate@K`, `NDCG@K`) на отложенной по времени выборке.
- [ ] Матричная факторизация на PyTorch и двухуровневый пайплайн (retrieval → reranking).
