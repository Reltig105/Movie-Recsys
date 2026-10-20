import { useEffect, useState } from 'react'
import api from '../api'

const SLIDERS = [
  {
    id: 'mood',
    label: 'Настроение',
    min: 0,
    max: 100,
    labels: ['Спокойное', 'Тёплое', 'Бодрое'],
  },
  {
    id: 'risk',
    label: 'Степень риска',
    min: 0,
    max: 100,
    labels: ['Проверенное', 'Смелое', 'Экспериментальное'],
  },
  {
    id: 'dynamics',
    label: 'Динамика',
    min: 0,
    max: 100,
    labels: ['Размеренная', 'Умеренная', 'Стремительная'],
  },
  {
    id: 'era',
    label: 'Эпоха',
    min: 1950,
    max: 2020,
    labels: ['Ретро', 'Классика', 'Современное'],
  },
]

const WATCH_GOAL = 365

/** Подбираем изящную текстовую подпись под текущее значение ползунка. */
function sliderLabel(slider, value) {
  const ratio = (value - slider.min) / (slider.max - slider.min)
  const index = Math.min(
    slider.labels.length - 1,
    Math.max(0, Math.floor(ratio * slider.labels.length)),
  )
  return slider.labels[index]
}

function Slider({ slider, value, onChange }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-brown-600">
          {slider.label}
        </span>
        <span className="text-sm font-medium text-brown-500">
          {sliderLabel(slider, value)}
        </span>
      </div>
      <input
        type="range"
        min={slider.min}
        max={slider.max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        aria-label={slider.label}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-sand-200 accent-clay-500"
      />
    </div>
  )
}

function Profile({ userId = 1 }) {
  const [profile, setProfile] = useState(null)
  const [error, setError] = useState(null)
  const [settings, setSettings] = useState({
    mood: 55,
    risk: 30,
    dynamics: 65,
    era: 1995,
  })

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
  const percent = Math.min(100, Math.round((watched / WATCH_GOAL) * 100))
  const history = profile?.history ?? []
  const genres = profile?.favorite_genres ?? []

  const updateSetting = (id, value) =>
    setSettings((prev) => ({ ...prev, [id]: value }))

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
                  Насмотренность: {watched} фильма
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
              {watched} фильма, {hours} часов
            </p>

            <div className="mt-6 flex flex-wrap gap-2">
              {genres.map((genre) => (
                <span
                  key={genre}
                  className="rounded-full bg-sand-200 px-4 py-1.5 text-sm font-medium text-brown-600"
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
            <h2 className="mb-6 text-lg font-semibold text-brown-700">
              Настрой свою киноленту
            </h2>
            <div className="flex flex-col gap-6">
              {SLIDERS.map((slider) => (
                <Slider
                  key={slider.id}
                  slider={slider}
                  value={settings[slider.id]}
                  onChange={(value) => updateSetting(slider.id, value)}
                />
              ))}
            </div>
          </div>

          <div className="rounded-3xl bg-sand-50 p-7 shadow-[0_14px_36px_rgba(107,79,58,0.10)]">
            <h2 className="mb-5 text-lg font-semibold text-brown-700">
              История твоих оценок
            </h2>

            {history.length === 0 ? (
              <p className="text-sm text-brown-400">
                Пока нет оценок — пройдите калибровку.
              </p>
            ) : (
              <div className="-mx-1 flex gap-4 overflow-x-auto px-1 pb-3 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
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
                        <span className="absolute right-1.5 top-1.5 rounded-full bg-brown-600/90 px-2 py-0.5 text-[11px] font-semibold text-sand-50">
                          ★ {movie.rating}
                        </span>
                      )}
                    </div>
                    <p className="mt-2 truncate text-[12px] font-medium text-brown-700">
                      {movie.title}
                    </p>
                    <button
                      type="button"
                      className="mt-2 w-full rounded-xl bg-sand-200 py-1.5 text-[11px] font-medium text-brown-600 transition hover:bg-sand-300"
                    >
                      Изменить вайбинг
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default Profile
