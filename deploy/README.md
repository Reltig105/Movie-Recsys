# Публикация Rexter на VPS

Схема: **интернет → Caddy (443, HTTPS) → uvicorn 127.0.0.1:8000 → PostgreSQL в Docker
(127.0.0.1:5432)**. Статику собранного фронтенда отдаёт сам FastAPI, поэтому
браузер и API живут на одном origin и CORS не нужен.

Нужно: Ubuntu 22.04/24.04, 2 vCPU, 4 ГБ RAM, 40 ГБ SSD, домен.
PostgreSQL опубликован только на localhost — снаружи порт 5432 недоступен.

## 1. DNS

A-запись домена (например, `rexter.example.com`) → IP сервера. AAAA не нужна,
если у сервера нет IPv6.

## 2. Базовые пакеты

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git curl caddy docker.io docker-compose-plugin nodejs npm
sudo systemctl enable --now docker caddy
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
```

Открыты только SSH, HTTP и HTTPS: 8000 и 5432 наружу не смотрят.

## 3. Пользователь и код

```bash
sudo useradd --system --create-home --home-dir /opt/rexter --shell /bin/bash rexter
sudo -u rexter git clone <URL-репозитория> /opt/rexter
sudo -u rexter python3 -m venv /opt/rexter/venv
sudo -u rexter /opt/rexter/venv/bin/pip install -r /opt/rexter/backend/requirements.txt
```

## 4. Секреты

```bash
sudo -u rexter cp /opt/rexter/.env.example /opt/rexter/.env
sudo -u rexter nano /opt/rexter/.env     # пароль БД + REXTER_DATABASE_URL
sudo chmod 600 /opt/rexter/.env
```

`.env` в `.gitignore` и в репозиторий не попадает. Пароль в
`REXTER_DATABASE_URL` должен совпадать с `POSTGRES_PASSWORD`.

## 5. База данных

```bash
cd /opt/rexter
sudo -u rexter docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d
```

Перенос данных с локальной машины (Windows):

```powershell
docker exec rexter-postgres pg_dump -U postgres -Fc -d rexter_db > rexter.dump
scp rexter.dump user@server:/tmp/rexter.dump
```

На сервере:

```bash
sudo docker exec -i rexter-postgres pg_restore -U rexter -d rexter_db --no-owner --clean --if-exists < /tmp/rexter.dump
sudo runuser -u rexter -- bash -c 'cd /opt/rexter && set -a && . ./.env && set +a && venv/bin/python -m backend.refresh_stats'
```

`refresh_stats` после восстановления обязателен: materialized view
`movie_stats` создаётся/наполняется заново (10–60 с в зависимости от диска).

Если база пустая, вместо restore достаточно:

```bash
sudo runuser -u rexter -- bash -c 'cd /opt/rexter && set -a && . ./.env && set +a && venv/bin/python -m backend.load_movielens --reset && venv/bin/python -m backend.refresh_stats'
```

## 6. Сборка фронтенда

```bash
cd /opt/rexter/frontend
sudo -u rexter npm ci
sudo -u rexter npm run build
test -f /opt/rexter/frontend/dist/index.html && echo "dist готов"
```

FastAPI раздаёт `frontend/dist` при старте: без него отдаётся только API
(и 404 на корне). Пересобирать фронтенд после `git pull` нужно всегда.

## 7. systemd

```bash
sudo cp /opt/rexter/deploy/rexter.service /etc/systemd/system/rexter.service
sudo systemctl daemon-reload
sudo systemctl enable --now rexter
sudo systemctl status rexter
curl -s http://127.0.0.1:8000/health
```

Юнит запускает uvicorn с `--proxy-headers`: без этого ограничение попыток
входа увидит один IP (адрес Caddy) вместо реального клиента.

## 8. Caddy и HTTPS

```bash
sudo sed 's/rexter\.example\.com/ВАШ.ДОМЕН/' /opt/rexter/deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy сам получит сертификат Let's Encrypt (нужны открытые 80/443 и
корректная A-запись). Проверка:

```bash
curl -sI https://ВАШ.ДОМЕН | head -1
curl -s https://ВАШ.ДОМЕН/health
```

## 9. Обновление версии

```bash
cd /opt/rexter
sudo -u rexter git pull
sudo -u rexter venv/bin/pip install -r backend/requirements.txt
sudo -u rexter bash -c 'cd frontend && npm ci && npm run build'
sudo systemctl restart rexter
```

## 10. Бэкап

```bash
sudo docker exec rexter-postgres pg_dump -U rexter -Fc -d rexter_db > /var/backups/rexter-$(date +%F).dump
```

Поставьте в cron (например, ежедневно в 4:00) и храните 7 последних копий.
Дамп занимает ~1 ГБ, сама база — ~4,2 ГБ.

## Что учтено в коде под такую схему

- **Один origin.** `frontend/src/api.js` использует относительный `baseURL`,
  поэтому абсолютных `localhost` в сборке нет.
- **Dev не сломан.** Vite проксирует `/api`, `/login`, `/register`, `/logout`,
  `/rate`, `/recommend`, `/user`, `/search`, `/health` в uvicorn
  (`frontend/vite.config.js`), так что `npm run dev` работает без CORS.
- **Авторизация.** `/register` и `/login` выдают `session_token`; запись оценок,
  профиль и история требуют заголовок `X-Session-Token` и проверяют, что
  пользователь обращается к своим данным. `/logout` гасит токен в БД.
- **Антибрутфорс.** 10 попыток входа/регистрации в минуту с одного IP
  (в памяти процесса).
- **CORS** включается только при заданном `REXTER_ALLOWED_ORIGINS` — по
  умолчанию middleware отсутствует.
- **Секреты** только в `.env`, база слушает `127.0.0.1`, порт наружу не
  публикуется.

## Что осталось за рамками

- Мониторинг/алерты, ротация логов, CDN для постеров.
- Полноценный rate-limit на все эндпоинты (сейчас только логин/регистрация).
- Автоматические миграции схемы (сейчас `create_all` + ручные ALTER из README).
