import { useEffect, useState } from 'react'
import Onboarding from './components/Onboarding'
import Sidebar from './components/Sidebar'
import CarouselFeed from './components/CarouselFeed'
import Profile from './components/Profile'

const STORAGE_KEY = 'kinolenta:session'

/** Читаем сохранённую сессию: онбординг запрашиваем только при первом заходе. */
function loadSession() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function Placeholder({ icon, title, text }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 text-center">
      <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-sand-50 text-4xl shadow-[0_14px_36px_rgba(107,79,58,0.10)]">
        {icon}
      </div>
      <h1 className="text-2xl font-semibold text-brown-700">{title}</h1>
      <p className="max-w-sm text-sm text-brown-500">{text}</p>
    </div>
  )
}

function App() {
  const saved = loadSession()
  const [hasOnboarded, setHasOnboarded] = useState(
    () => Boolean(saved?.hasOnboarded),
  )
  const [currentUserId, setCurrentUserId] = useState(() => saved?.userId ?? 1)
  const [section, setSection] = useState('recommendations')

  // Сохраняем прогресс, чтобы после перезагрузки не проходить онбординг заново.
  useEffect(() => {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ hasOnboarded, userId: currentUserId }),
      )
    } catch {
      // localStorage может быть недоступен (приватный режим) — просто игнорируем.
    }
  }, [hasOnboarded, currentUserId])

  const handleComplete = (userId) => {
    if (userId != null) setCurrentUserId(userId)
    setHasOnboarded(true)
  }

  const handleLogout = () => {
    try {
      window.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // ignore
    }
    setHasOnboarded(false)
    setCurrentUserId(1)
    setSection('recommendations')
  }

  if (!hasOnboarded) {
    return <Onboarding onComplete={handleComplete} />
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-sand-100">
      <Sidebar
        active={section}
        onNavigate={setSection}
        onLogout={handleLogout}
      />

      <main className="flex flex-1 overflow-hidden">
        {section === 'profile' && <Profile userId={currentUserId} />}

        {section === 'recommendations' && (
          <CarouselFeed userId={currentUserId} />
        )}

        {section === 'vibe' && (
          <Placeholder
            icon="✨"
            title="Кино по вайбу"
            text="Опишите настроение словами — например, «дождливый неонуар под меланхолию» — и мы подберём кино."
          />
        )}

        {section === 'search' && (
          <Placeholder
            icon="🔍"
            title="Поиск"
            text="Скоро здесь появится поиск по каталогу фильмов."
          />
        )}

        {section === 'about' && (
          <Placeholder
            icon="ℹ️"
            title="О сайте"
            text="«Rexter» — минималистичный сервис персональных рекомендаций кино."
          />
        )}
      </main>
    </div>
  )
}

export default App
