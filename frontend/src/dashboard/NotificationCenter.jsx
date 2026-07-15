import { useCallback, useEffect, useRef, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

export default function NotificationCenter() {
  const [result, setResult] = useState({ items: [], unread_count: 0 })
  const [isOpen, setIsOpen] = useState(false)
  const [error, setError] = useState('')
  const containerRef = useRef(null)

  const loadNotifications = useCallback(async () => {
    try {
      const response = await adminFetch('/api/auth/notifications/?limit=40')
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) throw new Error(payload?.message || 'No fue posible cargar las notificaciones.')
      setResult(payload.result || { items: [], unread_count: 0 })
      setError('')
    } catch (loadError) {
      setError(loadError.message)
    }
  }, [])

  useEffect(() => {
    const initialLoadId = window.setTimeout(loadNotifications, 0)
    const intervalId = window.setInterval(loadNotifications, 60000)
    function closeOutside(event) {
      if (containerRef.current && !containerRef.current.contains(event.target)) setIsOpen(false)
    }
    document.addEventListener('mousedown', closeOutside)
    return () => {
      window.clearTimeout(initialLoadId)
      window.clearInterval(intervalId)
      document.removeEventListener('mousedown', closeOutside)
    }
  }, [loadNotifications])

  async function markRead(notificationIds) {
    try {
      const response = await adminFetch('/api/auth/notifications/read/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(notificationIds ? { notification_ids: notificationIds } : {}),
      })
      if (response.ok) await loadNotifications()
    } catch {
      // La próxima actualización reintentará cargar el estado real.
    }
  }

  return (
    <div className="notification-center" ref={containerRef}>
      <button
        type="button"
        className="notification-trigger"
        aria-label={`Notificaciones: ${result.unread_count || 0} sin leer`}
        aria-expanded={isOpen}
        onClick={() => setIsOpen((current) => !current)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></svg>
        {result.unread_count ? <span className="notification-count">{result.unread_count > 99 ? '99+' : result.unread_count}</span> : null}
      </button>
      {isOpen ? (
        <section className="notification-popover" aria-label="Bandeja de notificaciones">
          <header>
            <div><strong>Notificaciones</strong><span>{result.unread_count || 0} sin leer</span></div>
            {result.unread_count ? <button type="button" onClick={() => markRead(null)}>Marcar todas</button> : null}
          </header>
          <div className="notification-list">
            {error ? <p className="notification-empty">{error}</p> : result.items?.length ? result.items.map((item) => (
              <a
                key={item.id}
                href={item.route || '#dashboard'}
                className={`notification-item ${item.is_read ? 'is-read' : 'is-unread'}`}
                onClick={() => markRead([item.id])}
              >
                <span className="notification-dot" aria-hidden="true" />
                <span><strong>{item.title}</strong><small>{item.message}</small><time>{item.created_at}</time></span>
              </a>
            )) : <p className="notification-empty">No tienes notificaciones.</p>}
          </div>
        </section>
      ) : null}
    </div>
  )
}
