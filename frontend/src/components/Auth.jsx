import { useState } from 'react'
import api from '../api'

function extractErrorMessage(err) {
  const detail = err.response?.data?.detail

  if (typeof detail === 'string') {
    return detail
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item === 'string' ? item : item?.msg))
      .filter(Boolean)
    if (messages.length > 0) {
      return messages.join('; ')
    }
  }

  if (detail && typeof detail === 'object' && typeof detail.msg === 'string') {
    return detail.msg
  }

  return 'Произошла ошибка при подключении к серверу'
}

function Auth({ onLogin }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const isLogin = mode === 'login'

  const handleSubmit = async (event) => {
    event.preventDefault()

    if (!username.trim() || !password.trim()) {
      setError('Введите логин и пароль')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const { data } = await api.post(isLogin ? '/login' : '/register', {
        username: username.trim(),
        password,
      })

      if (data?.user_id == null) {
        setError('Сервер не вернул идентификатор пользователя')
        return
      }

      onLogin(data.user_id)
    } catch (err) {
      console.error('Ошибка авторизации:', err)
      setError(extractErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const toggleMode = () => {
    setMode(isLogin ? 'register' : 'login')
    setError(null)
  }

  return (
    <div style={styles.wrapper}>
      <form style={styles.card} onSubmit={handleSubmit}>
        <h1 style={styles.title}>{isLogin ? 'Вход' : 'Регистрация'}</h1>

        <label style={styles.label}>
          Логин
          <input
            style={styles.input}
            type="text"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
          />
        </label>

        <label style={styles.label}>
          Пароль
          <input
            style={styles.input}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={isLogin ? 'current-password' : 'new-password'}
          />
        </label>

        {error && <p style={styles.error}>{error}</p>}

        <button style={styles.submit} type="submit" disabled={loading}>
          {loading ? 'Подождите…' : isLogin ? 'Войти' : 'Зарегистрироваться'}
        </button>

        <button style={styles.toggle} type="button" onClick={toggleMode}>
          {isLogin ? 'Нет аккаунта? Зарегистрироваться' : 'Уже есть аккаунт? Войти'}
        </button>
      </form>
    </div>
  )
}

const styles = {
  wrapper: {
    minHeight: '100svh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '24px',
    boxSizing: 'border-box',
  },
  card: {
    width: '100%',
    maxWidth: '360px',
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
    padding: '28px',
    border: '1px solid #e5e4e7',
    borderRadius: '12px',
    textAlign: 'left',
  },
  title: {
    margin: 0,
    fontSize: '28px',
  },
  label: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    fontSize: '15px',
  },
  input: {
    padding: '10px 12px',
    fontSize: '15px',
    borderRadius: '6px',
    border: '1px solid #e5e4e7',
    background: 'transparent',
  },
  error: {
    margin: 0,
    color: '#dc2626',
    fontSize: '14px',
  },
  submit: {
    padding: '10px 18px',
    fontSize: '15px',
    fontWeight: 500,
    border: 'none',
    borderRadius: '8px',
    background: '#aa3bff',
    color: '#fff',
    cursor: 'pointer',
  },
  toggle: {
    padding: 0,
    background: 'none',
    border: 'none',
    color: '#aa3bff',
    fontSize: '14px',
    cursor: 'pointer',
  },
}

export default Auth
