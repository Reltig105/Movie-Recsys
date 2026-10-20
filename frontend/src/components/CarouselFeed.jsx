import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import api from '../api'

// --- Cover Flow геометрия --------------------------------------------------
const CARD_W = 260
const CARD_H = 390
const SPACING = 210 // расстояние между центрами соседних карточек
const TRACK_H = 440 // высота трека под карточки + тени
const WHEEL_COOLDOWN = 220

/**
 * Параметры карточки в зависимости от удаления от активной.
 * Затемнение применяется ТОЛЬКО к постеру (`posterDim`), а не ко всей
 * карточке — так подписи на боковых карточках остаются контрастными.
 */
function coverFlowState(distance) {
  const d = Math.abs(distance)

  if (d === 0) {
    return { scale: 1, rotateY: 0, zIndex: 30, posterDim: 0, hidden: false }
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

function StarRating({ value, onChange }) {
  return (
    <div className="flex gap-1.5" role="group" aria-label="Оценка фильма">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          onClick={() => onChange(star)}
          aria-label={`Оценка ${star}`}
          className={`text-3xl leading-none transition-transform hover:scale-110 ${
            value >= star ? 'text-honey-400' : 'text-sand-300'
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
  const [error, setError] = useState(null)
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
        if (!cancelled) setIsRefreshing(false)
      })

    return () => {
      cancelled = true
    }
  }, [userId, feedRequest])

  // Кнопка «Обновить»: новый seed + список прошлых id → другой набор фильмов.
  const handleRefresh = () => {
    if (isRefreshing) return
    setIsRefreshing(true)
    setDescOpenId(null)
    setFeedRequest({
      seed: Math.floor(Math.random() * 1_000_000_000),
      exclude: movies.map((movie) => movie.id).join(','),
    })
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

  const active = movies[activeIndex]
  const count = movies.length
  const activeRating = active ? ratings[active.id] ?? 0 : 0

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

  return (
    <div
      className="relative flex flex-1 flex-col items-center justify-center overflow-hidden px-10 py-8"
      onWheel={handleWheel}
    >
      {/* Кнопка обновления рекомендаций — сверху справа */}
      <button
        type="button"
        onClick={handleRefresh}
        disabled={isRefreshing}
        aria-label="Обновить рекомендации"
        className="absolute right-10 top-8 z-40 inline-flex items-center gap-2 rounded-2xl border border-sand-300/70 bg-sand-50/90 px-4 py-2.5 text-sm font-semibold text-brown-700 shadow-[0_8px_20px_rgba(107,79,58,0.12)] backdrop-blur transition hover:bg-sand-100 disabled:cursor-wait disabled:opacity-60"
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

      {error && <p className="mb-4 text-sm text-clay-600">{error}</p>}

      {/* Горизонтальный трек Cover Flow */}
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
              className={`absolute left-1/2 top-1/2 overflow-hidden rounded-3xl bg-sand-200 ${
                isActive
                  ? 'cursor-default shadow-[0_28px_60px_rgba(107,79,58,0.32)]'
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
                className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center bg-sand-50/95 px-3 py-2.5"
              >
                <span className="line-clamp-2 text-center text-base font-semibold leading-tight text-brown-700">
                  {movie.title}
                </span>
              </motion.div>
            </motion.div>
          )
        })}
      </div>

      {/* Блок активного фильма: название, год, звёзды и «О фильме» */}
      <div className="mt-8 min-h-[150px] w-full max-w-[680px] text-center">
        <AnimatePresence mode="wait">
          {active && (
            <motion.div
              key={active.id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.28, ease: 'easeOut' }}
              className="flex w-full flex-col items-center gap-4"
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
              </div>

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

              <p className="text-lg font-bold text-brown-500">
                {active.year ?? '—'}
              </p>
              <p className="text-xs font-medium uppercase tracking-wider text-brown-400">
                {active.genres?.split('|').filter(Boolean).join(' · ')}
              </p>

              <StarRating value={activeRating} onChange={handleRate} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

export default CarouselFeed
