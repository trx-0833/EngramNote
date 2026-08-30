import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles/base.css'
import './styles/components.css'
import './styles/markdown.css'
import './styles/diff.css'
import './styles/cleaning.css'
import './styles/auth.css'
import './styles/layout.css'
import './styles/dashboard.css'
import './styles/learning.css'
import './styles/graph.css'
import './styles/assessment.css'
import './styles/responsive.css'
import './styles/markdown-extras.css'
import './styles/refinements.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
