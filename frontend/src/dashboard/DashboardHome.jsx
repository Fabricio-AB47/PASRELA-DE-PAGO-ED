import { useEffect, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'
import { DASHBOARD_ROUTES } from './navigation.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const TEACHER_PANEL_ROUTE_IDS = new Set(['teacher-attendance', 'teacher-grades', 'teacher-schedule'])
const STUDENT_PANEL_ROUTE_IDS = new Set(['student-schedule', 'student-grades'])

export default function DashboardHome({ user }) {
  const isTeacher = user.category === 'teacher'
  const isStudent = user.category === 'student'
  const visibleRoutes = user.category === 'staff'
    ? DASHBOARD_ROUTES.filter(
      (route) => route.id !== 'home' && !TEACHER_PANEL_ROUTE_IDS.has(route.id) && !STUDENT_PANEL_ROUTE_IDS.has(route.id),
    )
    : isStudent
      ? DASHBOARD_ROUTES.filter((route) => STUDENT_PANEL_ROUTE_IDS.has(route.id))
      : []

  return (
    <section className="dashboard-home" aria-labelledby="dashboard-home-title">
      <article className="hero-card">
        <span className="eyebrow">Panel general</span>
        <h2 id="dashboard-home-title">
          {isTeacher ? 'Dashboard docente' : isStudent ? 'Dashboard estudiantil' : 'Dashboard general'}
        </h2>
        <p>
          {isTeacher
            ? 'Consulta el resumen de cursos vinculados a tu usuario docente.'
            : isStudent
              ? 'Consulta la información académica disponible para tus materias matriculadas.'
              : 'Accede a cada módulo desde una vista independiente para mantener el sistema ordenado mientras crece.'}
        </p>
      </article>

      {isTeacher ? <TeacherCourseOverview /> : null}

      {!isTeacher ? (
        <section className="dashboard-module-grid" aria-label="Módulos disponibles">
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
                <h3>Sin módulos disponibles</h3>
                <p>Tu perfil está autenticado, pero no tiene opciones administrativas asignadas.</p>
              </div>
            </article>
          ) : null}
        </section>
      ) : null}
    </section>
  )
}

function TeacherCourseOverview() {
  const [dashboard, setDashboard] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let isMounted = true

    async function loadTeacherCourses() {
      try {
        const response = await adminFetch('/api/auth/teacher/courses/')
        const payload = await readResponsePayload(response)

        if (!payload || !response.ok || !payload.ok || !payload.dashboard) {
          throw new Error(payload?.message ?? `No fue posible cargar la información docente (${response.status}).`)
        }

        if (isMounted) {
          setDashboard(payload.dashboard)
          setError('')
        }
      } catch (loadError) {
        if (isMounted) {
          setError(loadError.message)
        }
      } finally {
        if (isMounted) {
          setIsLoading(false)
        }
      }
    }

    loadTeacherCourses()

    return () => {
      isMounted = false
    }
  }, [])

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando cursos</h3>
          <p>Estamos consultando la información académica del docente.</p>
        </div>
      </article>
    )
  }

  if (error) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>No se pudo cargar la información docente</h3>
          <p>{error}</p>
        </div>
      </article>
    )
  }

  const metrics = dashboard?.metrics || {}

  return (
    <section className="teacher-overview" aria-label="Resumen de cursos docentes">
      <section className="summary-grid teacher-summary-grid" aria-label="Indicadores docentes">
        <article className="summary-card">
          <span>Total cursos</span>
          <strong>{formatNumber(metrics.total_cursos)}</strong>
        </article>
        <article className="summary-card">
          <span>Materias distintas</span>
          <strong>{formatNumber(metrics.materias_distintas)}</strong>
        </article>
        <article className="summary-card">
          <span>Períodos</span>
          <strong>{formatNumber(metrics.periodos_distintos)}</strong>
        </article>
        <article className="summary-card">
          <span>Período activo</span>
          <strong>{formatNumber(metrics.cursos_periodo_activo)}</strong>
        </article>
      </section>
    </section>
  )
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}
