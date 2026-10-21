# Rexter

Сайт с персональной лентой фильмов. Пользователь регистрируется, ставит оценки
от 0.5 до 5 и получает подборку. Каталог собран из MovieLens 32M: 87 585
фильмов и 32 000 213 оценок; постеры, описания, режиссёры и актёры подтянуты
из TMDB.

Бэкенд — FastAPI и PostgreSQL (SQLAlchemy 2.x + asyncpg). Рекомендации считает
LightFM, векторы ищет FAISS, показы запоминаются в Redis. Фронтенд — React и
Vite; в продакшене статику раздаёт FastAPI, поэтому CORS не нужен.

## Что умеет

- регистрация, вход, профиль и история оценок;
- лента из 5 «уверенных» и 2 «неожиданных» фильмов без повторов в течение суток;
- рекомендации по обученному вектору или по последним оценкам (холодный старт);
- поиск по каталогу и калибровка вкусов при регистрации;
- фильмы без постера остаются в каталоге, но не попадают в рекомендации;
- если модели или Redis нет, сервис продолжает работать: лента собирается из
  статистики, кулдаун уходит в память процесса.

## Требования

- Python 3.12+ (проверено на 3.14);
- Node.js 20.19+ (или 22.12+);
- Docker с PostgreSQL 16 и Redis 7;
- ключ TMDB — только для обогащения каталога.

## Запуск

```bash
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux/macOS
pip install -r backend/requirements.txt
docker compose up -d
```

База стартует пустой. Данные загружаются один раз:

```bash
python -m backend.download_data            # архив MovieLens 32M (~1 ГБ)
python -m backend.load_movielens --reset   # 32 млн оценок через asyncpg COPY

$env:TMDB_API_KEY="ваш_ключ"               # export TMDB_API_KEY=... в Linux
python -m backend.enrich_movies
python -m backend.enrich_movies --missing  # добор постеров и описаний
python -m backend.refresh_stats            # статистика для ленты
```

`enrich_movies` — самый долгий шаг, тысячи запросов к TMDB. Его можно
пропустить: сайт запустится, но без постеров и описаний.

API:

```bash
uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Фронтенд в режиме разработки (Vite проксирует запросы к API на порт 8000):

```bash
cd frontend
npm ci
npm run dev
```

Сайт — http://127.0.0.1:5173, документация API — http://127.0.0.1:8000/docs.

Проверка продакшен-сборки:

```bash
cd frontend && npm run build
uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

FastAPI отдаст `frontend/dist` и клиентские маршруты (SPA-fallback).

## Структура

```
backend/
  api.py              REST API и раздача фронтенда
  database.py         подключение к PostgreSQL, сессии
  models.py           Movie, User, Rating, movie_stats
  load_movielens.py   загрузка каталога и оценок
  enrich_movies.py    TMDB: постеры, описания, режиссёры, актёры
  refresh_stats.py    materialized view movie_stats
  ml_pipeline/        сборка матриц и обучение LightFM
  recommender/        онлайн-выдача, FAISS, кулдаун
frontend/src/         React-приложение
deploy/               Caddy, systemd, compose для VPS
data/                 датасет, матрицы и артефакты (в git не входит)
```

`ml/`, `app.py`, `run.py` и корневой `requirements.txt` — ранние эксперименты
(Streamlit, SVD, контентная модель). Текущий сервис их не использует.

## Данные

MovieLens 32M: 87 585 фильмов, 200 949 пользователей, 32 000 213 оценок.
Идентификаторы фильмов не непрерывны (до 292 757), поэтому при обучении обе оси
перенумерованы в `0…N−1`; обратное преобразование лежит в `movie_ids.npy` и
`user_ids.npy`.

Загрузчик пишет строки через COPY, а индексы и ограничения на `ratings` строит
после заливки — на 32 млн строк это быстрее, чем обновлять индексы построчно.
Оценки хранятся в `double precision` без округления, шаг 0.5 сохраняется.

Перезагрузка только оценок, без каталога и TMDB-данных:

```bash
python -m backend.load_movielens --reload-ratings
python -m backend.refresh_stats
```

Обогащение аддитивное: пустое значение из TMDB не затирает сохранённое, название
фильма не подменяется. Ответы TMDB (200/404) отмечаются в
`movies.tmdb_checked_at`, поэтому `--missing` не запрашивает повторно фильмы,
которых там нет; сетевые сбои не помечаются и подбираются следующим запуском.

`movie_stats` — materialized view с уникальным индексом. Она обновляется только
командой `python -m backend.refresh_stats`, агрегаций по 32 млн строк в API нет.

## Рекомендации

Сначала собираются матрицы, затем обучается модель:

```bash
python -m backend.ml_pipeline.build_features

docker build -t rexter-train backend/ml_pipeline
docker run --rm -v "${PWD}:/app" rexter-train \
    python backend/ml_pipeline/train_lightfm.py --epochs 10 --components 64
```

`build_features` читает базу порциями и складывает в `data/processed/`:

| Файл | Содержимое |
| --- | --- |
| `interactions.npz` | CSR 200 949 × 87 585, оценки |
| `item_features.npz` | CSR 87 585 × 219 302, multi-hot признаки |
| `user_ids.npy`, `movie_ids.npy` | сопоставление внутренних индексов и id |

Признаки фильма: identity `movie:<id>`, жанры, режиссёр и актёры. Пропущенные
значения заменяются на `<unknown>`, чтобы у фильма не было пустой строки
признаков.

Обучение запускается в Linux-контейнере: LightFM 1.17 на Windows падает в WARP.
В обучение идут только оценки ≥ 4.0 (15 938 238 взаимодействий), функция потерь
WARP, `d=64`, около 4–5 минут на 12 ядрах. Смещения сложены в векторы, чтобы
онлайн-поиск остался одним скалярным произведением.

В `data/artifacts/` появляются:

| Файл | Размер | Что внутри |
| --- | --- | --- |
| `user_embeddings.npy` | ~52 МБ | 200 949 × 65: `p_u` и координата `1.0` |
| `item_embeddings.npy` | ~22 МБ | 87 585 × 65: `q_i` и `b_i` |
| `user_biases.npy` | <1 МБ | `b_u` |
| `faiss.index` | ~22 МБ | `IndexFlatIP` по векторам фильмов |
| `movie_ids.npy`, `user_ids.npy` | <1 МБ | обратный маппинг индексов |
| `lightfm_meta.json` | — | параметры обучения и версии |

Если артефактов нет, сервис стартует на популярных фильмах из `movie_stats`.

Один онлайн-запрос:

1. берётся вектор пользователя; для нового пользователя — взвешенное среднее
   векторов его последних оценок;
2. FAISS ищет 500 ближайших фильмов по внутреннему произведению;
3. убираются оценённые, показанные за 24 часа и фильмы без постера;
4. из топ-100 выбираются 5 лучших и 2 случайных из хвоста.

Показы лежат в Redis (`rexter:shown:<user_id>`, TTL 24 часа). Если Redis
недоступен, они хранятся в памяти процесса.

> Перед переобучением на Windows остановите API: он держит `.npy` через `mmap`,
> и запись в те же файлы завершится ошибкой.

## API

| Метод | Путь | Назначение | Токен |
| --- | --- | --- | --- |
| GET | `/health` | Проверка сервиса | — |
| GET | `/api/v1/movies?limit=` | Первые фильмы каталога | — |
| POST | `/register` | Регистрация, выдаёт `session_token` | — |
| POST | `/login` | Вход, выдаёт `session_token` | — |
| POST | `/logout` | Завершение сессии | да |
| GET | `/user/{user_id}` | Профиль | — |
| POST | `/rate` | Сохранить оценку (upsert) | да |
| GET | `/user/{user_id}/history` | История оценок | да |
| GET | `/search?query=` | Поиск по названию | — |
| GET | `/recommend/{user_id}` | Рекомендации | — |
| GET | `/api/v1/calibration-movies` | Фильмы для онбординга | — |
| POST | `/api/v1/rate` | Оценка из текущего фронтенда | да |
| GET | `/api/v1/feed` | Лента-карусель | — |
| GET | `/api/v1/profile` | Статистика, жанры, история | да |

Токен передаётся в заголовке `X-Session-Token` и даёт доступ только к своей
учётной записи. Каталог, поиск и рекомендации открыты.

## Настройки

Значения по умолчанию рассчитаны на `docker-compose.yml`. В продакшене
переменные задаются через `.env` (образец — `.env.example`).

| Переменная | По умолчанию | Зачем |
| --- | --- | --- |
| `REXTER_DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/rexter_db` | Подключение к PostgreSQL |
| `REXTER_REDIS_URL` | `redis://127.0.0.1:6379/0` | Кулдаун показов |
| `REXTER_COOLDOWN_TTL` | `86400` | Сколько секунд помнить показ |
| `REXTER_ARTIFACTS_DIR` | `data/artifacts` | Каталог модели |
| `REXTER_ALLOWED_ORIGINS` | пусто | CORS, только для отдельного домена |
| `REXTER_LOG_LEVEL` | `INFO` | Уровень логов приложения |
| `TMDB_API_KEY` | — | Обогащение каталога |

## Деплой

Схема: Caddy с автоматическим HTTPS → uvicorn на `127.0.0.1:8000` → PostgreSQL и
Redis в Docker, наружу не открыты. Пошаговая инструкция — в
[deploy/README.md](deploy/README.md).

## Документы

- [DESIGN.md](DESIGN.md) — как устроены данные, офлайн-обучение и онлайн-выдача.
- [LOG.md](LOG.md) — журнал разработки с найденными проблемами.
- [deploy/README.md](deploy/README.md) — установка на VPS.
