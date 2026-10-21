import { useEffect, useState } from 'react'
import api from '../api'
import { saveSession } from '../session'

const REACTIONS = [
  { label: '🔥 Топчик', value: 5 },
  { label: '👍 Хорошо', value: 4 },
  { label: '👎 Не зашло', value: 1 },
]

const CALIBRATION_SIZE = 10

function extractErrorMessage(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item === 'string' ? item : item?.msg))
      .filter(Boolean)
    if (messages.length > 0) return messages.join('; ')
  }
  return 'Произошла ошибка при подключении к серверу'
}

function ProgressBar({ step, total }) {
  const percent = Math.min(100, Math.round((step / total) * 100))
  return (
    <div className="w-full">
      <div className="mb-2 flex items-center justify-between text-xs font-medium text-brown-500">
        <span>
          Шаг {step} из {total}
        </span>
        <span>{percent}%</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-sand-200">
        <div
          className="h-full rounded-full bg-gradient-to-r from-sage-400 to-honey-400 transition-all duration-500"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  )
}

function Onboarding({ onComplete }) {
  const [step, setStep] = useState('register')
  const [mode, setMode] = useState('register')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [userId, setUserId] = useState(1)
  const [token, setToken] = useState(null)
  const [authError, setAuthError] = useState(null)
  const [busy, setBusy] = useState(false)

  const [movies, setMovies] = useState([])
  const [index, setIndex] = useState(0)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (step !== 'calibration' || movies.length > 0) return

    let cancelled = false
    api
      .get('/api/v1/calibration-movies')
      .then(({ data }) => {
        if (!cancelled) setMovies(Array.isArray(data) ? data : [])
      })
      .catch((error) => {
        console.error('Не удалось загрузить фильмы для калибровки:', error)
        if (!cancelled) setMovies([])
      })

    return () => {
      cancelled = true
    }
  }, [step, movies.length])

  const total = movies.length || CALIBRATION_SIZE
  const current = movies[index]

  const tryAuth = async (endpoint) => {
    const { data } = await api.post(endpoint, {
      username: username.trim(),
      password,
    })
    return data
  }

  const handleSubmitAuth = async (event) => {
    event.preventDefault()
    if (!username.trim() || !password.trim()) {
      setAuthError('Введите логин и пароль')
      return
    }

    setBusy(true)
    setAuthError(null)
    try {
      let auth
      try {
        auth = await tryAuth(mode === 'register' ? '/register' : '/login')
      } catch (error) {
        // Логин занят — пробуем войти, чтобы не блокировать пользователя.
        if (mode === 'register') {
          auth = await tryAuth('/login')
        } else {
          throw error
        }
      }
      if (auth?.user_id == null || !auth.session_token) {
        setAuthError('Сервер не вернул идентификатор пользователя')
        return
      }
      const account = {
        userId: auth.user_id,
        username: auth.username ?? username.trim(),
        token: auth.session_token,
      }
      setUserId(account.userId)
      setToken(account.token)
      // Токен нужен сразу: калибровка ставит оценки до конца онбординга.
      saveSession({ ...account, hasOnboarded: false })
      setStep('welcome')
    } catch (error) {
      console.error('Ошибка авторизации:', error)
      setAuthError(extractErrorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const goToHotStart = () => {
    setStep('loader')
    window.setTimeout(() => setStep('hotstart'), 2200)
  }

  const advance = () => {
    if (index + 1 >= movies.length) goToHotStart()
    else setIndex((value) => value + 1)
  }

  const handleRate = async (value) => {
    if (!current || saving) return
    setSaving(true)
    try {
      await api.post('/api/v1/rate', {
        user_id: userId,
        movie_id: current.id,
        rating: value,
      })
    } catch (error) {
      console.error('Не удалось сохранить оценку:', error)
    } finally {
      setSaving(false)
      advance()
    }
  }

  const shell =
    'w-full max-w-[560px] rounded-3xl bg-sand-50 p-10 shadow-[0_18px_45px_rgba(107,79,58,0.14)]'

  return (
    <div className="flex min-h-screen items-center justify-center bg-sand-100 px-6 py-12">
      {step === 'register' && (
        <form onSubmit={handleSubmitAuth} className={`${shell} animate-float-in`}>
          <h1 className="text-center text-2xl font-semibold text-brown-700">
            {mode === 'register' ? 'Создайте аккаунт' : 'Вход'}
          </h1>
          <p className="mt-2 text-center text-sm text-brown-500">
            Чтобы собрать вашу персональную киноленту
          </p>

          <label className="mt-8 block text-sm font-medium text-brown-600">
            Логин
            <input
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              className="mt-2 w-full rounded-2xl border-none bg-sand-100 px-4 py-3 text-[15px] text-brown-700 outline-none ring-1 ring-sand-200 transition focus:ring-2 focus:ring-clay-400"
            />
          </label>

          <label className="mt-4 block text-sm font-medium text-brown-600">
            Пароль
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              className="mt-2 w-full rounded-2xl border-none bg-sand-100 px-4 py-3 text-[15px] text-brown-700 outline-none ring-1 ring-sand-200 transition focus:ring-2 focus:ring-clay-400"
            />
          </label>

          {authError && (
            <p className="mt-4 text-sm text-clay-600">{authError}</p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="mt-8 w-full rounded-2xl bg-clay-500 px-8 py-4 text-[15px] font-semibold text-white shadow-[0_12px_28px_rgba(201,123,90,0.35)] transition hover:bg-clay-600 disabled:opacity-60"
          >
            {busy ? 'Подождите…' : mode === 'register' ? 'Зарегистрироваться' : 'Войти'}
          </button>

          <button
            type="button"
            onClick={() => {
              setMode(mode === 'register' ? 'login' : 'register')
              setAuthError(null)
            }}
            className="mt-4 w-full text-center text-sm text-brown-500 underline decoration-sand-300 underline-offset-4 hover:text-brown-700"
          >
            {mode === 'register' ? 'Уже есть аккаунт? Войти' : 'Нет аккаунта? Зарегистрироваться'}
          </button>
        </form>
      )}

      {step === 'welcome' && (
        <div className={`${shell} animate-float-in text-center`}>
          <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-3xl bg-sand-200 text-4xl">
            📖
          </div>
          <h1 className="mt-8 text-3xl font-semibold text-brown-700">
            Добро пожаловать, Алексей!
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-brown-500">
            Ответьте на несколько вопросов о кино — и мы соберём персональную
            киноленту на основе ваших вкусов.
          </p>
          <button
            type="button"
            onClick={() => setStep('calibration')}
            className="mt-10 w-full rounded-2xl bg-clay-500 px-8 py-4 text-[15px] font-semibold text-white shadow-[0_12px_28px_rgba(201,123,90,0.35)] transition hover:bg-clay-600"
          >
            Начать настройку
          </button>
        </div>
      )}

      {step === 'calibration' && (
        <div className={`${shell} animate-float-in`}>
          <ProgressBar step={Math.min(index + 1, total)} total={total} />

          {current ? (
            <div className="mt-8">
              <div className="mx-auto w-[200px] overflow-hidden rounded-3xl bg-sand-200 shadow-[0_12px_30px_rgba(107,79,58,0.16)]">
                <img
                  src={current.poster_url}
                  alt={current.title}
                  className="h-[300px] w-full object-cover"
                />
              </div>
              <h2 className="mt-5 text-center text-xl font-semibold text-brown-700">
                {current.title}
              </h2>
              <p className="mt-1 text-center text-sm text-brown-500">
                {current.genres?.split('|').filter(Boolean).join(' · ')}
              </p>

              <div className="mt-8 grid grid-cols-3 gap-3">
                {REACTIONS.map((reaction) => (
                  <button
                    key={reaction.value}
                    type="button"
                    disabled={saving}
                    onClick={() => handleRate(reaction.value)}
                    className="rounded-2xl bg-sand-200 px-2 py-4 text-sm font-semibold text-brown-700 transition hover:bg-sand-300 disabled:opacity-50"
                  >
                    {reaction.label}
                  </button>
                ))}
              </div>

              <button
                type="button"
                disabled={saving}
                onClick={advance}
                className="mt-5 w-full text-center text-sm font-medium text-brown-500 underline decoration-sand-300 underline-offset-4 transition hover:text-brown-700"
              >
                ⏭️ Не смотрел(а) / Пропустить
              </button>
            </div>
          ) : (
            <div className="mt-10 flex flex-col items-center gap-4 py-10">
              <div className="h-16 w-16 animate-spin-dotted rounded-full border-4 border-dotted border-clay-400" />
              <p className="text-sm text-brown-500">Загружаем фильмы…</p>
            </div>
          )}
        </div>
      )}

      {step === 'loader' && (
        <div className="flex flex-col items-center gap-8">
          <div className="h-24 w-24 animate-spin-dotted rounded-full border-4 border-dotted border-clay-400" />
          <p className="text-lg font-medium text-brown-600">
            Анализируем ваши вкусы...
          </p>
        </div>
      )}

      {step === 'hotstart' && (
        <div className={`${shell} animate-float-in text-center`}>
          <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-3xl bg-sand-200 text-4xl">
            🍿
          </div>
          <h1 className="mt-8 text-3xl font-semibold text-brown-700">
            Твоя кинолента ждет! 🍿
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-brown-500">
            Мы учли ваши оценки и подготовили персональную подборку из 7 фильмов.
          </p>
          <button
            type="button"
            onClick={() => onComplete({ userId, username: username.trim(), token })}
            className="mt-10 w-full rounded-2xl bg-clay-500 px-8 py-4 text-[15px] font-bold text-white shadow-[0_12px_28px_rgba(201,123,90,0.35)] transition hover:bg-clay-600"
          >
            Погнали! 🚀
          </button>
        </div>
      )}
    </div>
  )
}

export default Onboarding
