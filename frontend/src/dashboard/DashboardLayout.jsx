import { useEffect, useState } from 'react'
import { clearStoredSession } from '../shared.js'
import AdminAcademicPanel from './AdminAcademicPanel.jsx'
import AdminPaymentsPanel from './AdminPaymentsPanel.jsx'
import DashboardHome from './DashboardHome.jsx'
import { DASHBOARD_ROUTES, routeById, routeFromHash } from './navigation.js'

function getCurrentRouteId() {
  return routeFromHash(window.location.hash)
}

export default function DashboardLayout({ session, onSessionChange }) {
  const [activeRouteId, setActiveRouteId] = useState(getCurrentRouteId)
  const { user } = session
  const activeRoute = routeById(activeRouteId)
  const visibleRoutes = user.category === 'staff' ? DASHBOARD_ROUTES : DASHBOARD_ROUTES.filter((route) => route.id === 'home')

  useEffect(() => {
    function handleHashChange() {
      setActiveRouteId(getCurrentRouteId())
    }

    window.addEventListener('hashchange', handleHashChange)
    handleHashChange()

    return () => {
      window.removeEventListener('hashchange', handleHashChange)
    }
  }, [])

  function handleLogout() {
    clearStoredSession()
    onSessionChange(null)
    window.location.replace('/login/')
  }

  return (
    <main className="dashboard-layout">
      <header className="dashboard-topbar">
        <div>
          <span className="eyebrow">Acceso concedido</span>
          <h1 className="dashboard-title">Dashboard de pagos ED</h1>
        </div>
        <button type="button" className="ghost-button" onClick={handleLogout}>
          Cerrar sesion
        </button>
      </header>

      <nav className="dashboard-nav" aria-label="Navegacion del dashboard">
        {visibleRoutes.map((route) => (
          <a
            key={route.id}
            href={route.hash}
            className={route.id === activeRoute.id ? 'is-active' : ''}
            aria-current={route.id === activeRoute.id ? 'page' : undefined}
          >
            {route.label}
          </a>
        ))}
      </nav>

      <section className="dashboard-shell">
        <aside className="dashboard-sidebar">
          <div className="identity-card">
            <span className="identity-badge">{user.category_label}</span>
            <h2>{user.display_name}</h2>
            <p>{user.email ?? user.login}</p>
          </div>

          <dl className="identity-meta">
            <div>
              <dt>Rol</dt>
              <dd>{user.role.name}</dd>
            </div>
            <div>
              <dt>Estado</dt>
              <dd>{user.status}</dd>
            </div>
            <div>
              <dt>Vista</dt>
              <dd>{activeRoute.label}</dd>
            </div>
          </dl>
        </aside>

        <section className="dashboard-main">
          {activeRouteId === 'home' ? <DashboardHome user={user} /> : null}
          {activeRouteId === 'academic' && user.category === 'staff' ? <AdminAcademicPanel /> : null}
          {activeRouteId === 'payments' && user.category === 'staff' ? <AdminPaymentsPanel /> : null}
        </section>
      </section>
    </main>
  )
}
