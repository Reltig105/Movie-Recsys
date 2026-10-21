// Единственное место, где живёт сессия пользователя (localStorage).
// Токен нужен и axios-интерцептору (api.js), и экрану онбординга, поэтому
// чтение/запись вынесены из компонентов.

const STORAGE_KEY = 'kinolenta:session'

/** Сохранённая сессия: `{ userId, username, token, hasOnboarded }` или null. */
export function loadSession() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveSession(session) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
  } catch {
    // localStorage может быть недоступен (приватный режим) — просто игнорируем.
  }
}

export function clearSession() {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}

/** Токен текущей сессии (null, если пользователь ещё не входил). */
export function getToken() {
  return loadSession()?.token ?? null
}
