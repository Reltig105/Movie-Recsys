import { useEffect, useRef, useState } from 'react'
import api from '../api'
import { loadSession, saveSession } from '../session'
import { pluralize } from '../utils/pluralize'

const WATCH_GOAL = 365

function extractErrorMessage(error, fallback) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return fallback
}

function EyeIcon({ off = false }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-5 w-5"
      aria-hidden="true"
    >
      <path d="M2.25 12s3.5-6.75 9.75-6.75S21.75 12 21.75 12s-3.5 6.75-9.75 6.75S2.25 12 2.25 12Z" />
      <circle cx="12" cy="12" r="3" />
      {off && <path d="m3 3 18 18" />}
    </svg>
  )
}

function Profile({ userId = 1 }) {
  const [profile, setProfile] = useState(null)
  const [error, setError] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [savingId, setSavingId] = useState(null)
  const [rateError, setRateError] = useState(null)
  const historyRef = useRef(null)
  const [scrollState, setScrollState] = useState({ value: 0, max: 0 })
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [showPasswordValues, setShowPasswordValues] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordBusy, setPasswordBusy] = useState(false)
  const [passwordError, setPasswordError] = useState(null)
  const [passwordSuccess, setPasswordSuccess] = useState(null)
  const [watchlistBusyId, setWatchlistBusyId] = useState(null)
  const [watchlistError, setWatchlistError] = useState(null)

  useEffect(() => {
    let cancelled = false

    api
      .get('/api/v1/profile', { params: { user_id: userId } })
      .then(({ data }) => {
        if (!cancelled) setProfile(data)
      })
      .catch((err) => {
        console.error('Не удалось загрузить профиль:', err)
        if (!cancelled) setError('Не удалось загрузить профиль')
      })

    return () => {
      cancelled = true
    }
  }, [userId])

  const watched = profile?.watched_count ?? 0
  const hours = profile?.hours ?? 0
  const filmWord = pluralize(watched, 'фильм', 'фильма', 'фильмов')
  const hourWord = pluralize(hours, 'час', 'часа', 'часов')
  const percent = Math.min(100, Math.round((watched / WATCH_GOAL) * 100))
  const history = profile?.history ?? []
  const genres = profile?.favorite_genres ?? []
  const watchlist = profile?.watchlist ?? []
  const displayName = profile?.name?.trim() || 'Пользователь'
  const initials =
    displayName
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0])
      .join('')
      .toUpperCase() || '?'

  // Держим ползунок и горизонтальную прокрутку истории в синхроне.
  useEffect(() => {
    const element = historyRef.current
    if (!element) return undefined

    const sync = () => {
      setScrollState({
        value: element.scrollLeft,
        max: Math.max(0, element.scrollWidth - element.clientWidth),
      })
    }

    sync()
    element.addEventListener('scroll', sync, { passive: true })

    let observer
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(sync)
      observer.observe(element)
    }

    return () => {
      element.removeEventListener('scroll', sync)
      observer?.disconnect()
    }
  }, [history.length])

  // Колесо мыши над историей прокручивает её по горизонтали: иначе карусель
  // приходится тянуть за ползунок. Слушатель нативный и не passive — в
  // React-обработчике onWheel preventDefault() не срабатывает.
  useEffect(() => {
    const element = historyRef.current
    if (!element) return undefined

    const onWheel = (event) => {
      // Горизонтальный жест трекпада браузер обрабатывает сам.
      if (Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return

      const max = Math.max(0, element.scrollWidth - element.clientWidth)
      if (max === 0) return

      const step =
        event.deltaMode === 1
          ? 24
          : event.deltaMode === 2
            ? element.clientWidth
            : 1
      const delta = event.deltaY * step

      // На краю списка отдаём событие странице, иначе она не прокрутится.
      const atStart = element.scrollLeft <= 0 && delta < 0
      const atEnd = element.scrollLeft >= max - 1 && delta > 0
      if (atStart || atEnd) return

      event.preventDefault()
      element.scrollLeft = Math.min(max, Math.max(0, element.scrollLeft + delta))
    }

    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [history.length])

  const scrollPercent =
    scrollState.max > 0 ? (scrollState.value / scrollState.max) * 100 : 0

  const handleHistoryScroll = (event) => {
    const element = historyRef.current
    if (!element) return

    const max = Math.max(0, element.scrollWidth - element.clientWidth)
    const next = (Number(event.target.value) / 100) * max
    element.scrollLeft = next
    setScrollState({ value: next, max })
  }

  const handleRate = async (movieId, value) => {
    setSavingId(movieId)
    setRateError(null)

    try {
      // POST /rate — upsert по паре (user_id, movie_id).
      await api.post('/rate', {
        user_id: userId,
        movie_id: movieId,
        rating: value,
      })
      setProfile((prev) =>
        prev
          ? {
              ...prev,
              history: prev.history.map((movie) =>
                movie.id === movieId ? { ...movie, rating: value } : movie,
              ),
            }
          : prev,
      )
      setEditingId(null)
    } catch (err) {
      console.error('Не удалось обновить оценку:', err)
      setRateError('Не удалось сохранить оценку')
    } finally {
      setSavingId(null)
    }
  }

  const handleChangePassword = async (event) => {
    event.preventDefault()
    setPasswordError(null)
    setPasswordSuccess(null)

    if (newPassword.length < 6) {
      setPasswordError('Новый пароль должен содержать минимум 6 символов')
      return
    }
    if (newPassword !== confirmPassword) {
      setPasswordError('Пароли не совпадают')
      return
    }

    setPasswordBusy(true)
    try {
      const { data } = await api.post('/api/v1/password', {
        current_password: currentPassword,
        new_password: newPassword,
      })

      // Сервер меняет токен сессии — сохраняем его, чтобы пользователя
      // не разлогинило после смены пароля.
      const saved = loadSession()
      if (saved && data?.session_token) {
        saveSession({ ...saved, token: data.session_token })
      }

      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setShowPasswordValues(false)
      setPasswordOpen(false)
      setPasswordSuccess('Пароль обновлён')
    } catch (err) {
      console.error('Не удалось изменить пароль:', err)
      setPasswordError(
        extractErrorMessage(err, 'Не удалось изменить пароль'),
      )
    } finally {
      setPasswordBusy(false)
    }
  }

  const handleWatchlistRemove = async (movieId) => {
    setWatchlistBusyId(movieId)
    setWatchlistError(null)

    try {
      await api.delete(`/api/v1/watchlist/${movieId}`)
      setProfile((prev) =>
        prev
          ? {
              ...prev,
              watchlist: (prev.watchlist ?? []).filter(
                (movie) => movie.id !== movieId,
              ),
            }
          : prev,
      )
    } catch (err) {
      console.error('Не удалось убрать фильм из «Хочу посмотреть»:', err)
      setWatchlistError('Не удалось убрать фильм из списка')
    } finally {
      setWatchlistBusyId(null)
    }
  }

  return (
    <div className="h-full w-full overflow-y-auto px-10 py-10">
      <h1 className="mb-8 text-3xl font-semibold text-brown-700">
        Профиль
      </h1>

      {error && <p className="text-sm text-clay-600">{error}</p>}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        {/* Left column */}
        <div className="flex flex-col gap-8">
          <div className="rounded-3xl bg-sand-50 p-7 shadow-[0_14px_36px_rgba(107,79,58,0.10)]">
            <div className="flex items-center gap-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-clay-400 to-brown-500 text-xl font-semibold text-white">
                {initials}
              </div>
              <div>
                <div className="text-xl font-semibold text-brown-700">
                  {displayName}
                </div>
                <span className="mt-1 inline-block rounded-full bg-sand-200 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-brown-500">
                  Профиль
                </span>
              </div>
            </div>

            <div className="mt-7">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="font-medium text-brown-600">
                  Насмотренность: {watched} {filmWord}
                </span>
                <span className="text-brown-400">{percent}%</span>
              </div>
              <div className="h-2.5 w-full overflow-hidden rounded-full bg-sand-200">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-clay-400 to-honey-400 transition-all duration-700"
                  style={{ width: `${percent}%` }}
                />
              </div>
            </div>
          </div>

          <div className="rounded-3xl bg-sand-50 p-7 shadow-[0_14px_36px_rgba(107,79,58,0.10)]">
            <h2 className="text-lg font-semibold text-brown-700">Аккаунт</h2>

            <dl className="mt-5 space-y-4">
              <div className="flex items-start justify-between gap-4">
                <dt className="text-sm text-brown-500">Почта</dt>
                <dd className="min-w-0 break-all text-right text-sm font-medium text-brown-700">
                  {profile?.email || 'Не указана'}
                </dd>
              </div>

              <div className="flex items-center justify-between gap-4">
                <dt className="text-sm text-brown-500">Пароль</dt>
                <dd className="flex items-center gap-2">
                  <span
                    className="font-mono text-sm tracking-[0.18em] text-brown-700"
                    aria-label="Пароль скрыт"
                  >
                    ••••••••
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setPasswordOpen((value) => !value)
                      setPasswordError(null)
                    }}
                    aria-label={
                      passwordOpen ? 'Скрыть смену пароля' : 'Показать пароль'
                    }
                    aria-expanded={passwordOpen}
                    className="rounded-full p-1.5 text-brown-500 transition hover:bg-sand-200 hover:text-brown-700"
                  >
                    <EyeIcon off={passwordOpen} />
                  </button>
                </dd>
              </div>
            </dl>

            {passwordSuccess && (
              <p className="mt-4 text-sm text-sage-500">{passwordSuccess}</p>
            )}

            {passwordOpen && (
              <form
                onSubmit={handleChangePassword}
                className="mt-5 rounded-2xl bg-sand-100 p-4"
              >
                <p className="text-xs leading-relaxed text-brown-500">
                  Пароль хранится в виде необратимого хеша, поэтому показать его
                  нельзя. Здесь можно задать новый пароль.
                </p>

                <label className="mt-4 block text-xs font-medium text-brown-600">
                  Текущий пароль
                  <input
                    type={showPasswordValues ? 'text' : 'password'}
                    value={currentPassword}
                    onChange={(event) => setCurrentPassword(event.target.value)}
                    autoComplete="current-password"
                    required
                    className="mt-1.5 w-full rounded-xl border-none bg-sand-50 px-3 py-2.5 text-sm text-brown-700 outline-none ring-1 ring-sand-200 transition focus:ring-2 focus:ring-clay-400"
                  />
                </label>

                <label className="mt-3 block text-xs font-medium text-brown-600">
                  Новый пароль
                  <input
                    type={showPasswordValues ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                    autoComplete="new-password"
                    minLength={6}
                    required
                    className="mt-1.5 w-full rounded-xl border-none bg-sand-50 px-3 py-2.5 text-sm text-brown-700 outline-none ring-1 ring-sand-200 transition focus:ring-2 focus:ring-clay-400"
                  />
                </label>

                <label className="mt-3 block text-xs font-medium text-brown-600">
                  Повторите новый пароль
                  <input
                    type={showPasswordValues ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    autoComplete="new-password"
                    minLength={6}
                    required
                    className="mt-1.5 w-full rounded-xl border-none bg-sand-50 px-3 py-2.5 text-sm text-brown-700 outline-none ring-1 ring-sand-200 transition focus:ring-2 focus:ring-clay-400"
                  />
                </label>

                <button
                  type="button"
                  onClick={() => setShowPasswordValues((value) => !value)}
                  className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-brown-500 transition hover:text-brown-700"
                >
                  <EyeIcon off={showPasswordValues} />
                  {showPasswordValues ? 'Скрыть введённые пароли' : 'Показать введённые пароли'}
                </button>

                {passwordError && (
                  <p className="mt-3 text-xs text-clay-600">{passwordError}</p>
                )}

                <div className="mt-4 flex gap-2">
                  <button
                    type="submit"
                    disabled={passwordBusy}
                    className="flex-1 rounded-xl bg-clay-500 px-3 py-2.5 text-xs font-semibold text-white transition hover:bg-clay-600 disabled:opacity-60"
                  >
                    {passwordBusy ? 'Сохраняем…' : 'Сохранить пароль'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setPasswordOpen(false)
                      setCurrentPassword('')
                      setNewPassword('')
                      setConfirmPassword('')
                      setPasswordError(null)
                      setShowPasswordValues(false)
                    }}
                    className="rounded-xl bg-sand-200 px-3 py-2.5 text-xs font-semibold text-brown-700 transition hover:bg-sand-300"
                  >
                    Отмена
                  </button>
                </div>
              </form>
            )}
          </div>

          <div className="rounded-3xl bg-sand-50 p-7 shadow-[0_14px_36px_rgba(107,79,58,0.10)]">
            <h2 className="text-lg font-semibold text-brown-700">
              Твой кинобагаж
            </h2>
            <p className="mt-2 text-[15px] text-brown-500">
              {watched} {filmWord}, {hours} {hourWord}
            </p>

            <div className="mt-6 flex flex-wrap gap-2">
              {genres.map((genre) => (
                <span
                  key={genre}
                  className="rounded-full bg-[#e2d7cc] px-4 py-1.5 text-sm font-medium text-[#4a3b32]"
                >
                  {genre}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-8">
          <div className="rounded-3xl bg-sand-50 p-7 shadow-[0_14px_36px_rgba(107,79,58,0.10)]">
            <h2 className="mb-5 text-lg font-semibold text-brown-700">
              История твоих оценок
            </h2>

            {rateError && (
              <p className="-mt-2 mb-3 text-xs text-clay-600">{rateError}</p>
            )}

            {history.length === 0 ? (
              <p className="text-sm text-brown-400">
                Пока нет оценок — пройдите калибровку.
              </p>
            ) : (
              <>
                <div
                  ref={historyRef}
                  className="-mx-1 flex gap-4 overflow-x-auto px-1 pb-3 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                >
                  {history.map((movie) => (
                    <div
                      key={movie.id}
                      className="w-[130px] shrink-0 rounded-2xl bg-sand-100 p-2 shadow-[0_8px_20px_rgba(107,79,58,0.08)]"
                    >
                      <div className="relative overflow-hidden rounded-xl bg-sand-200">
                        <img
                          src={movie.poster_url}
                          alt={movie.title}
                          className="h-[170px] w-full object-cover"
                        />
                        {movie.rating != null && (
                          <span className="absolute right-1.5 top-1.5 rounded-full bg-[#3e322b] px-2 py-0.5 text-[11px] font-semibold text-sand-50">
                            ★ {movie.rating}
                          </span>
                        )}
                      </div>
                      <p className="mt-2 truncate text-[12px] font-medium text-brown-700">
                        {movie.title}
                      </p>
                      {editingId === movie.id ? (
                        <div className="mt-2">
                          <div
                            className="flex items-center justify-center gap-0.5"
                            role="group"
                            aria-label={`Оценка фильма ${movie.title}`}
                          >
                            {[1, 2, 3, 4, 5].map((star) => (
                              <button
                                key={star}
                                type="button"
                                onClick={() => handleRate(movie.id, star)}
                                disabled={savingId === movie.id}
                                aria-label={`Оценка ${star}`}
                                className={`text-base leading-none transition hover:text-honey-400 disabled:opacity-50 ${
                                  (movie.rating ?? 0) >= star
                                    ? 'text-honey-400'
                                    : 'text-sand-300'
                                }`}
                              >
                                ★
                              </button>
                            ))}
                          </div>
                          <button
                            type="button"
                            onClick={() => setEditingId(null)}
                            className="mt-1 w-full text-[10px] font-medium text-brown-400 transition hover:text-brown-600"
                          >
                            Отмена
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setEditingId(movie.id)
                            setRateError(null)
                          }}
                          className="mt-2 w-full rounded-xl border border-[#c9bdae] bg-transparent py-1.5 text-[11px] font-medium text-[#705d53] transition-colors hover:border-[#705d53] hover:text-[#4a3b32]"
                        >
                          Изменить оценку
                        </button>
                      )}
                    </div>
                  ))}
                </div>

                {scrollState.max > 0 && (
                  <div className="mt-1 flex items-center gap-3">
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="any"
                      value={scrollPercent}
                      onChange={handleHistoryScroll}
                      aria-label="Прокрутка истории оценок"
                      className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-sand-200 accent-clay-500"
                    />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      <div className="mt-8 rounded-3xl bg-sand-50 p-7 shadow-[0_14px_36px_rgba(107,79,58,0.10)]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-brown-700">
            Хочу посмотреть
          </h2>
          <span className="rounded-full bg-sand-200 px-3 py-1 text-xs font-semibold text-brown-600">
            {watchlist.length}
          </span>
        </div>

        {watchlistError && (
          <p className="mt-3 text-sm text-clay-600">{watchlistError}</p>
        )}

        {watchlist.length === 0 ? (
          <p className="mt-3 text-sm text-brown-400">
            Пока пусто. Добавляйте фильмы кнопкой «Хочу посмотреть» на странице
            рекомендаций.
          </p>
        ) : (
          <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {watchlist.map((movie) => (
              <div
                key={movie.id}
                className="rounded-2xl bg-sand-100 p-2 shadow-[0_8px_20px_rgba(107,79,58,0.08)]"
              >
                <div className="relative overflow-hidden rounded-xl bg-sand-200">
                  <img
                    src={movie.poster_url}
                    alt={movie.title}
                    className="h-[210px] w-full object-cover"
                  />
                  <button
                    type="button"
                    onClick={() => handleWatchlistRemove(movie.id)}
                    disabled={watchlistBusyId === movie.id}
                    aria-label={`Убрать «${movie.title}» из списка`}
                    className="absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full bg-[#3e322b]/85 text-sm font-bold text-sand-50 transition hover:bg-[#3e322b] disabled:opacity-50"
                  >
                    ✕
                  </button>
                </div>
                <p className="mt-2 line-clamp-2 min-h-[2.5rem] text-[13px] font-medium leading-tight text-brown-700">
                  {movie.title}
                </p>
                <p className="mt-1 text-[11px] text-brown-500">
                  {movie.year ?? '—'}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default Profile
