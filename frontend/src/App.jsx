import { useEffect, useState } from 'react'
import api from './api'
import { clearSession, loadSession, saveSession } from './session'
import Onboarding from './components/Onboarding'
import Sidebar from './components/Sidebar'
import CarouselFeed from './components/CarouselFeed'
import Profile from './components/Profile'

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
  const [session, setSession] = useState(loadSession)
  // Старые сохранения без токена бесполезны: сервер отклонит запросы на запись,
  // поэтому такого пользователя отправляем пройти онбординг заново.
  const [hasOnboarded, setHasOnboarded] = useState(() => {
    const saved = loadSession()
    return Boolean(saved?.hasOnboarded && saved?.token)
  })
  const [section, setSection] = useState('recommendations')

  // Сохраняем прогресс, чтобы после перезагрузки не проходить онбординг заново.
  useEffect(() => {
    if (session) saveSession({ ...session, hasOnboarded })
  }, [session, hasOnboarded])

  const handleComplete = (next) => {
    setSession(next)
    setHasOnboarded(true)
  }

  const handleLogout = () => {
    // Гасим токен на сервере; если сети нет — всё равно выходим локально.
    api.post('/logout').catch(() => {})
    clearSession()
    setSession(null)
    setHasOnboarded(false)
    setSection('recommendations')
  }

  const currentUserId = session?.userId ?? 1

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
          <div className="flex flex-1 items-center justify-center px-8 text-center">
            <p className="max-w-md text-lg font-medium leading-relaxed text-brown-600">
              Сейчас тут пусто, но скоро здесь появится что то очень крутое
            </p>
          </div>
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
