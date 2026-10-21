/**
 * Выбирает правильную русскую форму существительного для числа.
 *
 * Пример: pluralize(45, 'фильм', 'фильма', 'фильмов') → 'фильмов'.
 */
export function pluralize(count, one, few, many) {
  const value = Math.abs(Number(count))
  if (!Number.isFinite(value)) return many

  const integer = Math.floor(value)
  const lastTwo = integer % 100
  if (lastTwo >= 11 && lastTwo <= 14) return many

  const last = integer % 10
  if (last === 1) return one
  if (last >= 2 && last <= 4) return few
  return many
}
