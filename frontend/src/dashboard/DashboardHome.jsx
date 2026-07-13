import { DASHBOARD_ROUTES } from './navigation.js'

export default function DashboardHome({ user }) {
  const visibleRoutes = user.category === 'staff'
    ? DASHBOARD_ROUTES.filter((route) => route.id !== 'home')
    : []

  return (
    <section className="dashboard-home" aria-labelledby="dashboard-home-title">
      <article className="hero-card">
        <span className="eyebrow">Panel general</span>
        <h2 id="dashboard-home-title">Dashboard general</h2>
        <p>Accede a cada modulo desde una vista independiente para mantener el sistema ordenado mientras crece.</p>
      </article>

      <section className="dashboard-module-grid" aria-label="Modulos disponibles">
        {visibleRoutes.map((route) => (
          <article key={route.id} className="module-card dashboard-module-card">
            <div>
              <h3>{route.title}</h3>
              <p>{route.description}</p>
            </div>
            <a className="ghost-button compact-button" href={route.hash}>
              Abrir
            </a>
          </article>
        ))}
        {!visibleRoutes.length ? (
          <article className="module-card dashboard-module-card">
            <div>
              <h3>Sin modulos disponibles</h3>
              <p>Tu perfil esta autenticado, pero no tiene opciones administrativas asignadas.</p>
            </div>
          </article>
        ) : null}
      </section>
    </section>
  )
}
