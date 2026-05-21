/* eslint-disable react-refresh/only-export-components */
import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './App.css'
import DashboardLayout from './dashboard/DashboardLayout.jsx'
import { clearStoredSession, getStoredSession } from './shared.js'

function DashboardPage() {
  const [session, setSession] = useState(() => getStoredSession())

  useEffect(() => {
    if (!session?.user || !session?.session_token) {
      clearStoredSession()
      window.location.replace('/login/')
    }
  }, [session])

  if (!session?.user || !session?.session_token) {
    return null
  }

  return (
    <DashboardLayout session={session} onSessionChange={setSession} />
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <DashboardPage />
  </StrictMode>,
)