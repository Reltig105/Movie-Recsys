import { Component } from 'react'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Ошибка рендера приложения:', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: '32px', textAlign: 'left' }}>
          <h1 style={{ marginTop: 0 }}>Что-то пошло не так</h1>
          <p>{String(this.state.error?.message || this.state.error)}</p>
          <button type="button" onClick={() => this.setState({ error: null })}>
            Попробовать снова
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary
