import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import api from '../api'
import FeedFilters from './FeedFilters'

// --- Cover Flow геометрия --------------------------------------------------
const CARD_W = 260
const CARD_H = 390
const SPACING = 210 // расстояние между центрами соседних карточек
const TRACK_H = 440 // высота трека под карточки + тени
const WHEEL_COOLDOWN = 220
// После 20 оценок рекомендации уже считаются достаточно «разогретыми».
const RATING_GOAL = 20
const PROGRESS_RADIUS = 20
const PROGRESS_CIRCUMFERENCE = 2 * Math.PI * PROGRESS_RADIUS

const EMPTY_FILTERS = {
  country: '',
  genre: '',
  yearFrom: '',
  yearTo: '',
}

/**
 * Параметры карточки в зависимости от удаления от активной.
 * Затемнение применяется ТОЛЬКО к постеру (`posterDim`), а не ко всей
 * карточке — так подписи на боковых карточках остаются контрастными.
 */
function coverFlowState(distance) {
  const d = Math.abs(distance)

  if (d === 0) {
    return { scale: 1.05, rotateY: 0, zIndex: 30, posterDim: 0, hidden: false }
  }

  const rotateY = (distance < 0 ? 1 : -1) * Math.min(32, 18 + d * 6)

  if (d === 1) {
    return { scale: 0.8, rotateY, zIndex: 20, posterDim: 0.4, hidden: false }
  }
  if (d === 2) {
    return { scale: 0.66, rotateY, zIndex: 10, posterDim: 0.62, hidden: false }
  }
  return { scale: 0.55, rotateY, zIndex: 0, posterDim: 0.8, hidden: true }
}

function RatingProgress({ ratedCount, goal = RATING_GOAL }) {
  const percent = Math.min(100, Math.round((ratedCount / goal) * 100))
  const offset = PROGRESS_CIRCUMFERENCE - (percent / 100) * PROGRESS_CIRCUMFERENCE

  return (
    <div className="absolute left-10 top-8 z-30 flex w-[calc(100%-21rem)] max-w-[34rem] items-center gap-4 rounded-3xl border border-sand-300/70 bg-sand-50/90 px-5 py-3 shadow-[0_8px_20px_rgba(107,79,58,0.12)] backdrop-blur">
      <div
        className="relative h-12 w-12 shrink-0"
        role="img"
        aria-label={`Оценено ${percent}% фильмов`}
      >
        <svg viewBox="0 0 48 48" className="h-12 w-12 -rotate-90">
          <circle
            cx="24"
            cy="24"
            r={PROGRESS_RADIUS}
            fill="none"
            strokeWidth="4"
            className="text-sand-200"
            stroke="currentColor"
          />
          <circle
            cx="24"
            cy="24"
            r={PROGRESS_RADIUS}
            fill="none"
            strokeWidth="4"
            strokeLinecap="round"
            stroke="currentColor"
            className="text-clay-500 transition-all duration-500"
            style={{
              strokeDasharray: PROGRESS_CIRCUMFERENCE,
              strokeDashoffset: offset,
            }}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-[11px] font-bold text-brown-700">
          {percent}%
        </span>
      </div>

      <div className="min-w-0">
        <p className="text-sm font-semibold leading-tight text-brown-700">
          {ratedCount >= goal
            ? 'Отличная выборка! Рекомендации становятся точнее'
            : 'Оцените больше фильмов — рекомендации станут точнее'}
        </p>
        <p className="mt-1 text-xs leading-tight text-brown-500">
          {ratedCount >= goal
            ? `Оценено ${ratedCount} фильмов`
            : `Оценено ${ratedCount} из ${goal} фильмов`}
        </p>
      </div>
    </div>
  )
}

function StarRating({ value, onChange }) {
  return (
    <div className="flex gap-1.5" role="group" aria-label="Оценка фильма">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          onClick={() => onChange(star)}
          aria-label={`Оценка ${star}`}
          aria-pressed={value >= star}
          className={`text-3xl leading-none transition-all duration-150 hover:scale-110 ${
            value >= star
              ? 'text-honey-400 drop-shadow-[0_2px_5px_rgba(224,164,88,0.35)]'
              : 'text-sand-400 hover:text-honey-400'
          }`}
        >
          ★
        </button>
      ))}
    </div>
  )
}

function CarouselFeed({ userId = 1 }) {
  const [movies, setMovies] = useState([])
  const [activeIndex, setActiveIndex] = useState(0)
  const [ratings, setRatings] = useState({})
  const [descOpenId, setDescOpenId] = useState(null)
  const [feedRequest, setFeedRequest] = useState(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [filterOptions, setFilterOptions] = useState(null)
  const [error, setError] = useState(null)
  const [watchlistIds, setWatchlistIds] = useState(() => new Set())
  const [watchlistBusyId, setWatchlistBusyId] = useState(null)
  const [watchlistError, setWatchlistError] = useState(null)
  const wheelLockedAt = useRef(0)

  useEffect(() => {
    let cancelled = false
    const params = { user_id: userId }
    // Параметры есть только после «Обновить»: тогда бэкенд отдаёт свежую
    // подборку, исключая уже показанные фильмы.
    if (feedRequest) {
      params.seed = feedRequest.seed
      if (feedRequest.exclude) params.exclude_ids = feedRequest.exclude
    }
    if (filters.country) params.country = filters.country
    if (filters.genre) params.genre = filters.genre
    if (filters.yearFrom) params.year_from = Number(filters.yearFrom)
    if (filters.yearTo) params.year_to = Number(filters.yearTo)

    api
      .get('/api/v1/feed', { params })
      .then(({ data }) => {
        if (cancelled) return
        const list = Array.isArray(data) ? data : []
        setMovies(list)
        setActiveIndex(Math.floor(Math.max(0, list.length - 1) / 2))
        setError(null)
      })
      .catch((err) => {
        console.error('Не удалось загрузить ленту рекомендаций:', err)
        if (!cancelled) setError('Не удалось загрузить рекомендации')
      })
      .finally(() => {
        if (!cancelled) {
          setIsRefreshing(false)
          setIsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [userId, feedRequest, filters])

  // Справочники фильтров не зависят от выдачи: загружаем один раз.
  useEffect(() => {
    let cancelled = false

    api
      .get('/api/v1/filters')
      .then(({ data }) => {
        if (!cancelled) setFilterOptions(data)
      })
      .catch((err) => {
        console.error('Не удалось загрузить фильтры:', err)
      })

    return () => {
      cancelled = true
    }
  }, [])

  // Кнопка «Обновить»: новый seed + список прошлых id → другой набор фильмов.
  const handleRefresh = () => {
    if (isRefreshing) return
    setIsRefreshing(true)
    setDescOpenId(null)
    setFiltersOpen(false)
    setFeedRequest({
      seed: Math.floor(Math.random() * 1_000_000_000),
      exclude: movies.map((movie) => movie.id).join(','),
    })
  }

  const handleApplyFilters = (next) => {
    setFilters({ ...next })
    setFiltersOpen(false)
    setDescOpenId(null)
    // Новая подборка: прошлые id больше не нужно исключать.
    setFeedRequest(null)
  }

  const handleResetFilters = () => {
    setFilters(EMPTY_FILTERS)
    setFiltersOpen(false)
    setDescOpenId(null)
    setFeedRequest(null)
  }

  // Подтягиваем уже проставленные оценки, чтобы звёзды были предзаполнены.
  useEffect(() => {
    let cancelled = false

    api
      .get(`/user/${userId}/history`)
      .then(({ data }) => {
        if (cancelled) return
        const map = {}
        for (const entry of Array.isArray(data) ? data : []) {
          map[entry.movie_id] = entry.rating
        }
        setRatings(map)
      })
      .catch((err) => {
        console.error('Не удалось загрузить историю оценок:', err)
      })

    return () => {
      cancelled = true
    }
  }, [userId])

  // Какие фильмы уже лежат в «Хочу посмотреть» — чтобы кнопка была в
  // правильном состоянии и не добавляла один фильм повторно.
  useEffect(() => {
    let cancelled = false

    api
      .get('/api/v1/watchlist')
      .then(({ data }) => {
        if (cancelled) return
        setWatchlistIds(
          new Set(
            (Array.isArray(data) ? data : []).map((movie) => movie.id),
          ),
        )
      })
      .catch((err) => {
        console.error('Не удалось загрузить список «Хочу посмотреть»:', err)
      })

    return () => {
      cancelled = true
    }
  }, [userId])

  const active = movies[activeIndex]
  const count = movies.length
  const ratedCount = Object.keys(ratings).length
  const activeInWatchlist = active ? watchlistIds.has(active.id) : false
  const activeRating = active ? ratings[active.id] ?? 0 : 0
  const activeGenres = (active?.genres ?? '')
    .split(',')
    .map((genre) => genre.trim())
    .filter(Boolean)
  const activeFilterCount = [
    filters.country,
    filters.genre,
    filters.yearFrom || filters.yearTo,
  ].filter(Boolean).length

  // Описание открыто только для текущего активного фильма: при смене
  // карточки оно автоматически сворачивается, отдельный эффект не нужен.
  const isDescOpen = active ? descOpenId === active.id : false

  const toggleDesc = () =>
    setDescOpenId((prev) => (active && prev === active.id ? null : active?.id ?? null))

  const selectIndex = (index) => setActiveIndex(index)

  // Прокрутка колёсиком сдвигает карусель влево/вправо (с троттлингом).
  const handleWheel = (event) => {
    if (count < 2 || Math.abs(event.deltaY) < 2) return

    const now = Date.now()
    if (now - wheelLockedAt.current < WHEEL_COOLDOWN) return
    wheelLockedAt.current = now

    const direction = event.deltaY > 0 ? 1 : -1
    setActiveIndex((prev) => Math.min(count - 1, Math.max(0, prev + direction)))
  }

  // Оценка сохраняется в БД и сразу отражается в профиле.
  const handleRate = async (value) => {
    if (!active) return

    setRatings((prev) => ({ ...prev, [active.id]: value }))

    try {
      await api.post('/api/v1/rate', {
        user_id: userId,
        movie_id: active.id,
        rating: value,
      })
    } catch (err) {
      console.error('Не удалось сохранить оценку:', err)
    }
  }

  const handleWatchlistToggle = async () => {
    if (!active || watchlistBusyId === active.id) return

    const movieId = active.id
    const wasSaved = watchlistIds.has(movieId)
    setWatchlistBusyId(movieId)
    setWatchlistError(null)

    try {
      if (wasSaved) {
        await api.delete(`/api/v1/watchlist/${movieId}`)
      } else {
        await api.post('/api/v1/watchlist', { movie_id: movieId })
      }

      setWatchlistIds((prev) => {
        const next = new Set(prev)
        if (wasSaved) next.delete(movieId)
        else next.add(movieId)
        return next
      })
    } catch (err) {
      console.error('Не удалось обновить список «Хочу посмотреть»:', err)
      setWatchlistError('Не удалось обновить список «Хочу посмотреть»')
    } finally {
      setWatchlistBusyId(null)
    }
  }

  return (
    <div
      className="relative flex flex-1 flex-col items-center justify-center overflow-hidden px-10 pb-8 pt-24"
      onWheel={handleWheel}
    >
      <RatingProgress ratedCount={ratedCount} />

      {/* Фильтры и обновление рекомендаций — сверху справа */}
      <div className="absolute right-10 top-8 z-40 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setFiltersOpen((prev) => !prev)}
          aria-expanded={filtersOpen}
          className="inline-flex items-center gap-2 rounded-2xl border border-sand-300/70 bg-sand-50/90 px-4 py-2.5 text-sm font-semibold text-brown-700 shadow-[0_8px_20px_rgba(107,79,58,0.12)] backdrop-blur transition hover:bg-sand-100"
        >
          <span aria-hidden="true">☰</span>
          Фильтры
          {activeFilterCount > 0 && (
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-clay-500 px-1.5 text-[11px] font-bold text-white">
              {activeFilterCount}
            </span>
          )}
        </button>

        <button
          type="button"
          onClick={handleRefresh}
          disabled={isRefreshing}
          aria-label="Обновить рекомендации"
          className="inline-flex items-center gap-2 rounded-2xl border border-sand-300/70 bg-sand-50/90 px-4 py-2.5 text-sm font-semibold text-brown-700 shadow-[0_8px_20px_rgba(107,79,58,0.12)] backdrop-blur transition hover:bg-sand-100 disabled:cursor-wait disabled:opacity-60"
        >
          <motion.span
            animate={isRefreshing ? { rotate: 360 } : { rotate: 0 }}
            transition={
              isRefreshing
                ? { repeat: Infinity, duration: 0.9, ease: 'linear' }
                : { duration: 0.2 }
            }
            className="inline-block leading-none"
          >
            ⟳
          </motion.span>
          {isRefreshing ? 'Обновляем…' : 'Обновить'}
        </button>
      </div>

      <FeedFilters
        key={filtersOpen ? 'open' : 'closed'}
        open={filtersOpen}
        options={filterOptions}
        value={filters}
        onApply={handleApplyFilters}
        onReset={handleResetFilters}
        onClose={() => setFiltersOpen(false)}
      />

      {error && <p className="mb-4 text-sm text-clay-600">{error}</p>}

      {isLoading && movies.length === 0 && (
        <p className="mb-4 text-sm text-brown-400">Подбираем фильмы…</p>
      )}

      {!isLoading && !error && movies.length === 0 && (
        <div className="flex flex-col items-center gap-4 text-center">
          <p className="text-base text-brown-500">
            {activeFilterCount > 0
              ? 'По выбранным фильтрам ничего не нашлось.'
              : 'Пока нет рекомендаций.'}
          </p>
          {activeFilterCount > 0 && (
            <button
              type="button"
              onClick={handleResetFilters}
              className="rounded-xl bg-brown-600 px-5 py-2.5 text-sm font-semibold text-sand-50 transition hover:bg-brown-700"
            >
              Сбросить фильтры
            </button>
          )}
        </div>
      )}

      {/* Горизонтальный трек Cover Flow */}
      {movies.length > 0 && (
      <div className="relative w-full" style={{ height: TRACK_H }}>
        {movies.map((movie, index) => {
          const distance = index - activeIndex
          const { scale, rotateY, zIndex, posterDim, hidden } =
            coverFlowState(distance)
          const isActive = distance === 0

          return (
            <motion.div
              key={movie.id}
              initial={false}
              animate={{ x: distance * SPACING, scale, rotateY, opacity: hidden ? 0 : 1 }}
              transition={{ type: 'spring', stiffness: 220, damping: 30, mass: 0.9 }}
              onClick={() => !isActive && selectIndex(index)}
              className={`absolute left-1/2 top-1/2 overflow-hidden rounded-[20px] bg-sand-200 ${
                isActive
                  ? 'cursor-default shadow-[0_20px_40px_rgba(44,37,31,0.15)]'
                  : 'cursor-pointer shadow-[0_16px_36px_rgba(107,79,58,0.18)]'
              }`}
              style={{
                width: CARD_W,
                height: CARD_H,
                marginLeft: -CARD_W / 2,
                marginTop: -CARD_H / 2,
                zIndex,
                transformPerspective: 1200,
              }}
            >
              <img
                src={movie.poster_url}
                alt={movie.title}
                className="h-full w-full object-cover"
                draggable={false}
              />

              {/* Мягкая бежевая вуаль отодвигает постер вглубь, не трогая текст */}
              <motion.div
                initial={false}
                animate={{ opacity: posterDim }}
                className="pointer-events-none absolute inset-0 bg-sand-100"
              />

              {/* Подпись на карточке: название без кнопок. У активной карточки
                  название показывается в блоке ниже, поэтому здесь скрыто. */}
              <motion.div
                initial={false}
                animate={{ opacity: isActive ? 0 : 1 }}
                className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center rounded-b-[16px] bg-[#e9e2d7] px-3 py-2.5"
              >
                <span className="line-clamp-2 text-center text-base font-semibold leading-tight text-[#5c4e46]">
                  {movie.title}
                </span>
              </motion.div>
            </motion.div>
          )
        })}
      </div>
      )}

      {/* Блок активного фильма: название, год, звёзды и «О фильме» */}
      {movies.length > 0 && (
      <div className="mt-6 min-h-[150px] w-full max-w-[680px] text-center">
        <AnimatePresence mode="wait">
          {active && (
            <motion.div
              key={active.id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.28, ease: 'easeOut' }}
              className="flex w-full flex-col items-center gap-3"
            >
              <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-3">
                <h1 className="text-3xl font-bold leading-tight text-brown-700">
                  {active.title}
                </h1>
                <button
                  type="button"
                  onClick={toggleDesc}
                  aria-expanded={isDescOpen}
                  className="inline-flex items-center gap-1.5 rounded-2xl bg-brown-600 px-5 py-2.5 text-sm font-semibold text-sand-50 shadow-[0_12px_28px_rgba(107,79,58,0.28)] transition hover:bg-brown-700"
                >
                  О фильме
                  <motion.span
                    animate={{ rotate: isDescOpen ? 180 : 0 }}
                    transition={{ duration: 0.2, ease: 'easeOut' }}
                    className="inline-block leading-none"
                  >
                    ▾
                  </motion.span>
                </button>

                <button
                  type="button"
                  onClick={handleWatchlistToggle}
                  disabled={watchlistBusyId === active.id}
                  aria-pressed={activeInWatchlist}
                  className={`inline-flex items-center gap-1.5 rounded-2xl px-5 py-2.5 text-sm font-semibold shadow-[0_12px_28px_rgba(107,79,58,0.18)] transition disabled:cursor-wait disabled:opacity-60 ${
                    activeInWatchlist
                      ? 'bg-clay-500 text-white hover:bg-clay-600'
                      : 'bg-sand-50 text-brown-700 hover:bg-sand-100'
                  }`}
                >
                  <span aria-hidden="true">
                    {activeInWatchlist ? '✓' : '+'}
                  </span>
                  {activeInWatchlist
                    ? 'В «Хочу посмотреть»'
                    : 'Хочу посмотреть'}
                </button>
              </div>

              {watchlistError && (
                <p className="text-xs text-clay-600">{watchlistError}</p>
              )}

              <p className="flex flex-wrap items-center justify-center gap-x-2 text-[14px] font-medium uppercase tracking-[0.06em] text-[#705d53]">
                <span>{active.year ?? '—'}</span>
                {activeGenres.length > 0 && (
                  <>
                    <span aria-hidden="true">•</span>
                    <span>{activeGenres.join(' • ')}</span>
                  </>
                )}
              </p>

              {/* Спойлер-фри описание: плавно выезжает сразу под кнопкой */}
              <AnimatePresence initial={false}>
                {isDescOpen && (
                  <motion.div
                    key="synopsis"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.3, ease: 'easeInOut' }}
                    className="w-full overflow-hidden"
                  >
                    <p className="mx-auto max-w-[560px] rounded-2xl border-l-4 border-brown-300/70 bg-sand-50/70 px-5 py-4 text-left text-sm leading-relaxed text-brown-600/90 backdrop-blur-sm">
                      {active.synopsis ||
                        'Описание для этого фильма пока недоступно.'}
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>

              <StarRating value={activeRating} onChange={handleRate} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      )}
    </div>
  )
}

export default CarouselFeed
