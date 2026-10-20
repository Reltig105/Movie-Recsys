/**
 * Монохромные SVG-иконки для левого меню (стиль обводки, как в Яндекс Музыке).
 * Цвет наследуется от текста через `currentColor`.
 */

function Svg({ className = 'h-6 w-6', children }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

/** Рекомендации — киноплёнка. */
export function IconFilm(props) {
  return (
    <Svg {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <path d="M7.5 4v16M16.5 4v16" />
      <path d="M3 8.5h4.5M3 15.5h4.5M16.5 8.5H21M16.5 15.5H21" />
    </Svg>
  )
}

/** Профиль — пользователь. */
export function IconUser(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="8" r="3.8" />
      <path d="M4.5 20c0-3.6 3.4-5.5 7.5-5.5s7.5 1.9 7.5 5.5" />
    </Svg>
  )
}

/** Кино по вайбу — искры. */
export function IconSparkles(props) {
  return (
    <Svg {...props}>
      <path d="M11 3.5l1.6 3.9 3.9 1.6-3.9 1.6L11 14.5 9.4 10.6 5.5 9l3.9-1.6z" />
      <path d="M18.5 14.5l.8 1.9 1.9.8-1.9.8-.8 1.9-.8-1.9-1.9-.8 1.9-.8z" />
    </Svg>
  )
}

/** Поиск — лупа. */
export function IconSearch(props) {
  return (
    <Svg {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.6-3.6" />
    </Svg>
  )
}

/** О сайте — информация. */
export function IconInfo(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.5" />
      <path d="M12 7.8h.01" />
    </Svg>
  )
}

/** Выход — дверь со стрелкой. */
export function IconLogout(props) {
  return (
    <Svg {...props}>
      <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
      <path d="M10.5 8.5L14 12l-3.5 3.5" />
      <path d="M14 12H3.5" />
    </Svg>
  )
}
