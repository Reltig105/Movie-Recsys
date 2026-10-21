import axios from 'axios'
import { getToken } from './session'

// Пустой baseURL = текущий origin. В dev запросы уходят на Vite и проксируются
// в uvicorn (см. server.proxy в vite.config.js), в проде статику и API отдаёт
// один и тот же процесс — CORS не нужен.
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? '',
  timeout: 20000,
})

// Токен сессии из localStorage → заголовок, который проверяет backend.
api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers['X-Session-Token'] = token
  }
  return config
})

export default api
