import { IconFilm, IconInfo, IconLogout, IconSearch, IconSparkles, IconUser } from './icons'

const MENU_ITEMS = [
  { id: 'recommendations', Icon: IconFilm, label: 'Рекомендации' },
  { id: 'profile', Icon: IconUser, label: 'Профиль' },
  { id: 'vibe', Icon: IconSparkles, label: 'Кино по вайбу' },
  { id: 'search', Icon: IconSearch, label: 'Поиск' },
]

const BOTTOM_ITEMS = [{ id: 'about', Icon: IconInfo, label: 'О сайте' }]

function Sidebar({ active = 'recommendations', onNavigate, onLogout }) {
  const itemClass = (isActive) =>
    [
      'flex w-full items-center gap-4 rounded-xl px-5 py-3.5 text-left text-[17px] transition-colors',
      isActive
        ? "relative bg-[#DCD1C3] font-medium text-gray-900 before:absolute before:left-0 before:top-1/2 before:h-6 before:w-[3px] before:-translate-y-1/2 before:rounded-full before:bg-[#705d53] before:content-['']"
        : 'bg-transparent text-brown-600/70 hover:bg-[#EBE5DB]/50 hover:text-brown-700',
    ].join(' ')

  return (
    <aside className="flex h-screen w-[240px] shrink-0 flex-col justify-between rounded-r-[32px] bg-sand-100 px-5 py-7 shadow-[8px_0_30px_rgba(107,79,58,0.06)]">
      <div>
        <div className="flex items-center gap-2.5 px-2 py-1">
          <IconFilm className="h-7 w-7 shrink-0 text-clay-500" />
          <span className="text-[21px] font-bold tracking-tight text-brown-700">
            Rexter
          </span>
        </div>

        <nav className="mt-6 flex flex-col gap-1.5">
          {MENU_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={itemClass(item.id === active)}
              onClick={() => onNavigate?.(item.id)}
            >
              <item.Icon className="h-6 w-6 shrink-0" />
              {item.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="flex flex-col gap-1.5">
        {BOTTOM_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={itemClass(false)}
            onClick={() => onNavigate?.(item.id)}
          >
            <item.Icon className="h-6 w-6 shrink-0" />
            {item.label}
          </button>
        ))}
        <button type="button" className={itemClass(false)} onClick={onLogout}>
          <IconLogout className="h-6 w-6 shrink-0" />
          Выход
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
