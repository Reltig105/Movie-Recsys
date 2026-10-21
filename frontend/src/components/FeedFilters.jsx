import { useState } from 'react'

function FeedFilters({ open, options, value, onApply, onReset, onClose }) {
  const [draft, setDraft] = useState(value)

  if (!open) return null

  const setField = (field) => (event) =>
    setDraft((prev) => ({ ...prev, [field]: event.target.value }))

  const yearsValid =
    !draft.yearFrom ||
    !draft.yearTo ||
    Number(draft.yearFrom) <= Number(draft.yearTo)

  const selectClass =
    'mt-1.5 w-full rounded-xl border border-sand-300 bg-white/80 px-3 py-2 text-sm text-brown-700 outline-none transition focus:border-clay-400 focus:ring-2 focus:ring-clay-400/20'

  return (
    <div
      className="absolute right-10 top-[76px] z-50 w-[340px] max-w-[calc(100vw-2rem)] rounded-3xl border border-sand-300/70 bg-sand-50/95 p-5 shadow-[0_20px_48px_rgba(107,79,58,0.22)] backdrop-blur"
      onWheel={(event) => event.stopPropagation()}
    >
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-brown-700">Фильтры</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Закрыть фильтры"
          className="rounded-full px-2 py-1 text-lg leading-none text-brown-400 transition hover:bg-sand-200 hover:text-brown-600"
        >
          ✕
        </button>
      </div>

      <div className="flex flex-col gap-4">
        <label className="text-sm font-medium text-brown-600">
          Страна производства
          <select
            value={draft.country}
            onChange={setField('country')}
            className={selectClass}
          >
            <option value="">Любая страна</option>
            {(options?.countries ?? []).map((country) => (
              <option key={country.code} value={country.code}>
                {country.name}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm font-medium text-brown-600">
          Жанр
          <select
            value={draft.genre}
            onChange={setField('genre')}
            className={selectClass}
          >
            <option value="">Любой жанр</option>
            {(options?.genres ?? []).map((genre) => (
              <option key={genre} value={genre}>
                {genre}
              </option>
            ))}
          </select>
        </label>

        <div>
          <span className="text-sm font-medium text-brown-600">
            Год выпуска
          </span>
          <div className="mt-1.5 flex items-center gap-2">
            <input
              type="number"
              inputMode="numeric"
              value={draft.yearFrom}
              onChange={setField('yearFrom')}
              placeholder={options?.year_min ? `от ${options.year_min}` : 'от'}
              aria-label="Год выпуска от"
              className={selectClass}
            />
            <span className="text-brown-400">—</span>
            <input
              type="number"
              inputMode="numeric"
              value={draft.yearTo}
              onChange={setField('yearTo')}
              placeholder={options?.year_max ? `до ${options.year_max}` : 'до'}
              aria-label="Год выпуска до"
              className={selectClass}
            />
          </div>
          {!yearsValid && (
            <p className="mt-1.5 text-xs text-clay-600">
              Год «от» не может быть больше года «до».
            </p>
          )}
        </div>
      </div>

      <div className="mt-5 flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onReset}
          className="rounded-xl px-3 py-2 text-sm font-semibold text-brown-500 transition hover:bg-sand-200 hover:text-brown-700"
        >
          Сбросить
        </button>
        <button
          type="button"
          onClick={() => onApply(draft)}
          disabled={!yearsValid}
          className="rounded-xl bg-brown-600 px-5 py-2.5 text-sm font-semibold text-sand-50 shadow-[0_10px_24px_rgba(107,79,58,0.24)] transition hover:bg-brown-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Применить
        </button>
      </div>
    </div>
  )
}

export default FeedFilters
