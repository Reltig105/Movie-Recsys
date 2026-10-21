import { useEffect, useRef, useState } from 'react'
import api from '../api'
import { pluralize } from '../utils/pluralize'

const WATCH_GOAL = 365

function Profile({ userId = 1 }) {
  const [profile, setProfile] = useState(null)
  const [error, setError] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [savingId, setSavingId] = useState(null)
  const [rateError, setRateError] = useState(null)
  const historyRef = useRef(null)
  const [scrollState, setScrollState] = useState({ value: 0, max: 0 })

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
                АП
              </div>
              <div>
                <div className="text-xl font-semibold text-brown-700">
                  {profile?.name ?? 'Алексей Панин'}
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
    </div>
  )
}

export default Profile
