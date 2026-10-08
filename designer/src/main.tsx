import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { DevErrorBoundary } from './components/DevErrorBoundary'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <DevErrorBoundary>
      <App />
    </DevErrorBoundary>
  </React.StrictMode>,
)
