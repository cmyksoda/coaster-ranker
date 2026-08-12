import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// Without this, any render error unmounts the whole tree and leaves a blank page
// with nothing but a console message. Show what broke instead.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Coaster Ranker crashed:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="cr-app" style={{ padding: '40px 20px' }}>
        <div style={{ maxWidth: 700, margin: '0 auto' }}>
          <h1 className="cr-display" style={{ fontSize: 34, letterSpacing: '0.05em', margin: '0 0 10px' }}>
            SOMETHING BROKE
          </h1>
          <p className="cr-text-soft" style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 18 }}>
            Your ranking progress is saved. Reload the page to pick up where you left off.
          </p>
          <pre className="cr-mono" style={{
            fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            border: '1px solid var(--border)', background: 'var(--surface)',
            padding: 14, marginBottom: 18,
          }}>
            {String(this.state.error && (this.state.error.stack || this.state.error.message || this.state.error))}
          </pre>
          <button className="cr-btn primary" onClick={() => window.location.reload()}>Reload</button>
        </div>
      </div>
    )
  }
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
