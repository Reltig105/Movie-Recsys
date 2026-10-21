import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// В dev фронтенд и API живут на одном origin: запросы к API проксируются в
// uvicorn. В проде статику и API отдаёт один процесс FastAPI — схема та же,
// поэтому в коде фронтенда нет абсолютных адресов и CORS не нужен.
const API_TARGET = process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000'
const API_PATHS = [
  '/api',
  '/login',
  '/register',
  '/logout',
  '/rate',
  '/recommend',
  '/user',
  '/search',
  '/health',
  '/docs',
  '/openapi.json',
]

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, API_TARGET])),
  },
})
