import React from 'react'
import ReactDOM from 'react-dom/client'
import { configureDesigner } from './config'
import App from './App'
import { DevErrorBoundary } from './components/DevErrorBoundary'
import './index.css'

// 仓库开发态显式开编辑(包缺省 readonly——宿主嵌入的安全默认,review P0-1)
configureDesigner({ mode: 'edit' })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <DevErrorBoundary>
      <App />
    </DevErrorBoundary>
  </React.StrictMode>,
)
