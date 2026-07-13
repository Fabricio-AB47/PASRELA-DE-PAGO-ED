import { useEffect, useState } from 'react'
import { clearStoredSession } from '../shared.js'
import AdminAcademicPanel from './AdminAcademicPanel.jsx'
import AdminAttendancePanel from './AdminAttendancePanel.jsx'
import AdminBulkEnrollmentPanel from './AdminBulkEnrollmentPanel.jsx'
import AdminCertificateTemplatePanel from './AdminCertificateTemplatePanel.jsx'
import AdminCourseCutsPanel from './AdminCourseCutsPanel.jsx'
import AdminCourseStudentsPanel from './AdminCourseStudentsPanel.jsx'
import AdminEnrolledStudentsPanel from './AdminEnrolledStudentsPanel.jsx'
import AdminGradeTransferPanel from './AdminGradeTransferPanel.jsx'
import AdminPaymentsPanel from './AdminPaymentsPanel.jsx'
import AdminSchedulePanel from './AdminSchedulePanel.jsx'
import AdminTeacherEntryPanel from './AdminTeacherEntryPanel.jsx'
import AdminTeacherEnrollmentPanel from './AdminTeacherEnrollmentPanel.jsx'
import AdminTeamsEnrollmentPanel from './AdminTeamsEnrollmentPanel.jsx'
import DashboardHome from './DashboardHome.jsx'
import StudentGradesPanel from './StudentGradesPanel.jsx'
import StudentSchedulePanel from './StudentSchedulePanel.jsx'
import TeacherAcademicPanel from './TeacherAcademicPanel.jsx'
import TeacherSchedulePanel from './TeacherSchedulePanel.jsx'
import { DASHBOARD_ROUTES, routeById, routeFromHash } from './navigation.js'

function getCurrentRouteId() {
  return routeFromHash(window.location.hash)
}

const TEACHER_ADMIN_ROUTE_IDS = new Set(['teacher-entry', 'teacher-enrollment'])
const TEACHER_PANEL_ROUTE_IDS = new Set(['teacher-attendance', 'teacher-grades', 'teacher-schedule'])
const STUDENT_PANEL_ROUTE_IDS = new Set(['student-schedule', 'student-grades'])

export default function DashboardLayout({ session, onSessionChange }) {
  const [activeRouteId, setActiveRouteId] = useState(getCurrentRouteId)
  const { user } = session
  const activeRoute = routeById(activeRouteId)
  const dashboardTitle = user.category === 'teacher'
    ? 'Dashboard docente'
    : user.category === 'student'
      ? 'Dashboard estudiantil'
      : 'Dashboard administrativo'
  const visibleRoutes = user.category === 'staff'
    ? DASHBOARD_ROUTES.filter((route) => !TEACHER_PANEL_ROUTE_IDS.has(route.id) && !STUDENT_PANEL_ROUTE_IDS.has(route.id))
    : user.category === 'teacher'
      ? DASHBOARD_ROUTES.filter((route) => route.id === 'home' || TEACHER_PANEL_ROUTE_IDS.has(route.id))
      : user.category === 'student'
        ? DASHBOARD_ROUTES.filter((route) => route.id === 'home' || STUDENT_PANEL_ROUTE_IDS.has(route.id))
        : DASHBOARD_ROUTES.filter((route) => route.id === 'home')
  const primaryRoutes = visibleRoutes.filter((route) => !TEACHER_ADMIN_ROUTE_IDS.has(route.id))
  const teacherRoutes = user.category === 'staff'
    ? DASHBOARD_ROUTES.filter((route) => TEACHER_ADMIN_ROUTE_IDS.has(route.id))
    : []
  const isTeacherRouteActive = TEACHER_ADMIN_ROUTE_IDS.has(activeRoute.id)

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
        <div className="dashboard-brand-heading">
          <img
            className="dashboard-logo"
            src="/Intec-Logowithslogangray.svg"
            alt="INTEC"
          />
          <div className="dashboard-brand-copy">
            <span className="eyebrow">Acceso concedido</span>
            <h1 className="dashboard-title">{dashboardTitle}</h1>
          </div>
        </div>
        <button type="button" className="ghost-button" onClick={handleLogout}>
          Cerrar sesión
        </button>
      </header>

      <nav className="dashboard-nav" aria-label="Navegación del dashboard">
        {primaryRoutes.map((route) => (
          <a
            key={route.id}
            href={route.hash}
            className={route.id === activeRoute.id ? 'is-active' : ''}
            aria-current={route.id === activeRoute.id ? 'page' : undefined}
          >
            {route.label}
          </a>
        ))}
        {teacherRoutes.length ? (
          <details className={`dashboard-nav-menu ${isTeacherRouteActive ? 'is-active' : ''}`}>
            <summary aria-current={isTeacherRouteActive ? 'page' : undefined}>
              Docente
            </summary>
            <div className="dashboard-nav-dropdown">
              {teacherRoutes.map((route) => (
                <a
                  key={route.id}
                  href={route.hash}
                  className={route.id === activeRoute.id ? 'is-active' : ''}
                  aria-current={route.id === activeRoute.id ? 'page' : undefined}
                >
                  {route.label}
                </a>
              ))}
            </div>
          </details>
        ) : null}
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
          {activeRouteId === 'course-cuts' && user.category === 'staff' ? <AdminCourseCutsPanel /> : null}
          {activeRouteId === 'course-students' && user.category === 'staff' ? <AdminCourseStudentsPanel /> : null}
          {activeRouteId === 'enrolled-students' && user.category === 'staff' ? <AdminEnrolledStudentsPanel /> : null}
          {activeRouteId === 'attendance' && user.category === 'staff' ? <AdminAttendancePanel /> : null}
          {activeRouteId === 'admin-schedule' && user.category === 'staff' ? <AdminSchedulePanel /> : null}
          {activeRouteId === 'admin-teams' && user.category === 'staff' ? <AdminTeamsEnrollmentPanel /> : null}
          {activeRouteId === 'grade-transfer' && user.category === 'staff' ? <AdminGradeTransferPanel /> : null}
          {activeRouteId === 'certificate-template' && user.category === 'staff' ? <AdminCertificateTemplatePanel /> : null}
          {activeRouteId === 'payments' && user.category === 'staff' ? <AdminPaymentsPanel /> : null}
          {activeRouteId === 'bulk-enrollment' && user.category === 'staff' ? <AdminBulkEnrollmentPanel /> : null}
          {activeRouteId === 'teacher-entry' && user.category === 'staff' ? <AdminTeacherEntryPanel /> : null}
          {activeRouteId === 'teacher-enrollment' && user.category === 'staff' ? <AdminTeacherEnrollmentPanel /> : null}
          {activeRouteId === 'teacher-attendance' && user.category === 'teacher' ? <TeacherAcademicPanel mode="attendance" /> : null}
          {activeRouteId === 'teacher-grades' && user.category === 'teacher' ? <TeacherAcademicPanel mode="grades" /> : null}
          {activeRouteId === 'teacher-schedule' && user.category === 'teacher' ? <TeacherSchedulePanel /> : null}
          {activeRouteId === 'student-schedule' && user.category === 'student' ? <StudentSchedulePanel /> : null}
          {activeRouteId === 'student-grades' && user.category === 'student' ? <StudentGradesPanel /> : null}
        </section>
      </section>
    </main>
  )
}
