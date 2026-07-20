import { useEffect, useState } from 'react'
import { clearStoredSession } from '../shared.js'
import AdminAcademicPanel from './AdminAcademicPanel.jsx'
import AdminAttendancePanel from './AdminAttendancePanel.jsx'
import AdminBulkEnrollmentPanel from './AdminBulkEnrollmentPanel.jsx'
import AdminCertificateTemplatePanel from './AdminCertificateTemplatePanel.jsx'
import AdminCourseCutsPanel from './AdminCourseCutsPanel.jsx'
import AdminCourseStudentsPanel from './AdminCourseStudentsPanel.jsx'
import AdminEnrolledStudentsPanel from './AdminEnrolledStudentsPanel.jsx'
import AdminStudentUpdatesPanel from './AdminStudentUpdatesPanel.jsx'
import AdminGradeTransferPanel from './AdminGradeTransferPanel.jsx'
import AdminPaymentsPanel from './AdminPaymentsPanel.jsx'
import AdminPaymentOperationsPanel from './AdminPaymentOperationsPanel.jsx'
import AdminSchedulePanel from './AdminSchedulePanel.jsx'
import AdminTeacherEntryPanel from './AdminTeacherEntryPanel.jsx'
import AdminTeacherEnrollmentPanel from './AdminTeacherEnrollmentPanel.jsx'
import AdminTeamsEnrollmentPanel from './AdminTeamsEnrollmentPanel.jsx'
import DashboardHome from './DashboardHome.jsx'
import StudentGradesPanel from './StudentGradesPanel.jsx'
import StudentSchedulePanel from './StudentSchedulePanel.jsx'
import TeacherAcademicPanel from './TeacherAcademicPanel.jsx'
import TeacherSchedulePanel from './TeacherSchedulePanel.jsx'
import NotificationCenter from './NotificationCenter.jsx'
import {
  canAccessDashboardRoute,
  isTeacherAdminRoute,
  routeById,
  routeFromHash,
  visibleRoutesForUser,
} from './navigation.js'

function getCurrentRouteId() {
  return routeFromHash(window.location.hash)
}

export default function DashboardLayout({ session, onSessionChange }) {
  const [activeRouteId, setActiveRouteId] = useState(getCurrentRouteId)
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false)
  const { user } = session
  const routeIsAllowed = canAccessDashboardRoute(user, activeRouteId)
  const effectiveRouteId = routeIsAllowed ? activeRouteId : 'home'
  const activeRoute = routeById(effectiveRouteId)
  const dashboardTitle = user.category === 'teacher'
    ? 'Dashboard docente'
    : user.category === 'student'
      ? 'Dashboard estudiantil'
      : 'Dashboard general'
  const visibleRoutes = visibleRoutesForUser(user)
  const primaryRoutes = visibleRoutes.filter((route) => !isTeacherAdminRoute(route.id) && !route.parentId)
  const teacherRoutes = user.category === 'staff'
    ? visibleRoutes.filter((route) => isTeacherAdminRoute(route.id))
    : []
  const isTeacherRouteActive = isTeacherAdminRoute(activeRoute.id)

  useEffect(() => {
    function handleHashChange() {
      const requestedRouteId = getCurrentRouteId()
      if (!canAccessDashboardRoute(user, requestedRouteId)) {
        window.history.replaceState(null, '', '#dashboard')
        setActiveRouteId('home')
        setIsMobileMenuOpen(false)
        return
      }
      setActiveRouteId(requestedRouteId)
      setIsMobileMenuOpen(false)
    }

    window.addEventListener('hashchange', handleHashChange)
    handleHashChange()

    return () => {
      window.removeEventListener('hashchange', handleHashChange)
    }
  }, [user])

  useEffect(() => {
    function handleResponsiveMenu(event) {
      if (event.type === 'keydown' && event.key === 'Escape') {
        setIsMobileMenuOpen(false)
      }
      if (event.type === 'resize' && window.innerWidth > 1120) {
        setIsMobileMenuOpen(false)
      }
    }

    window.addEventListener('keydown', handleResponsiveMenu)
    window.addEventListener('resize', handleResponsiveMenu)
    return () => {
      window.removeEventListener('keydown', handleResponsiveMenu)
      window.removeEventListener('resize', handleResponsiveMenu)
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
        <div className="dashboard-topbar-actions">
          <NotificationCenter />
          <button type="button" className="ghost-button" onClick={handleLogout}>Cerrar sesión</button>
        </div>
      </header>

      <div className="dashboard-mobile-navigation">
        <button
          type="button"
          className={`dashboard-menu-toggle ${isMobileMenuOpen ? 'is-open' : ''}`}
          aria-expanded={isMobileMenuOpen}
          aria-controls="dashboard-navigation"
          onClick={() => setIsMobileMenuOpen((current) => !current)}
        >
          <span className="dashboard-menu-icon" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>
            <small>Menú</small>
            <strong>{activeRoute.label}</strong>
          </span>
        </button>
      </div>

      <nav
        id="dashboard-navigation"
        className={`dashboard-nav ${isMobileMenuOpen ? 'is-open' : ''}`}
        aria-label="Navegación del dashboard"
      >
        {primaryRoutes.map((route) => (
          <a
            key={route.id}
            href={route.hash}
            className={route.id === activeRoute.id || activeRoute.parentId === route.id ? 'is-active' : ''}
            aria-current={route.id === activeRoute.id || activeRoute.parentId === route.id ? 'page' : undefined}
            onClick={() => setIsMobileMenuOpen(false)}
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
                  onClick={() => setIsMobileMenuOpen(false)}
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
          {effectiveRouteId === 'home' ? <DashboardHome user={user} /> : null}
          {effectiveRouteId === 'academic' && user.category === 'staff' ? <AdminAcademicPanel /> : null}
          {effectiveRouteId === 'course-cuts' && user.category === 'staff' ? <AdminCourseCutsPanel /> : null}
          {effectiveRouteId === 'course-students' && user.category === 'staff' ? <AdminCourseStudentsPanel /> : null}
          {effectiveRouteId === 'enrolled-students' && user.category === 'staff' ? <AdminEnrolledStudentsPanel /> : null}
          {effectiveRouteId === 'student-updates' && user.category === 'staff' ? <AdminStudentUpdatesPanel /> : null}
          {effectiveRouteId === 'attendance' && user.category === 'staff' ? <AdminAttendancePanel /> : null}
          {effectiveRouteId === 'admin-schedule' && user.category === 'staff' ? <AdminSchedulePanel /> : null}
          {effectiveRouteId === 'admin-teams' && user.category === 'staff' ? <AdminTeamsEnrollmentPanel /> : null}
          {effectiveRouteId === 'grade-transfer' && user.category === 'staff' ? <AdminGradeTransferPanel /> : null}
          {effectiveRouteId === 'certificate-template' && user.category === 'staff' ? <AdminCertificateTemplatePanel /> : null}
          {effectiveRouteId === 'payments' && user.category === 'staff' ? <AdminPaymentsPanel /> : null}
          {effectiveRouteId === 'payment-operations' && user.category === 'staff' ? <AdminPaymentOperationsPanel /> : null}
          {effectiveRouteId === 'bulk-enrollment' && user.category === 'staff' ? <AdminBulkEnrollmentPanel /> : null}
          {effectiveRouteId === 'teacher-entry' && user.category === 'staff' ? <AdminTeacherEntryPanel /> : null}
          {effectiveRouteId === 'teacher-enrollment' && user.category === 'staff' ? <AdminTeacherEnrollmentPanel /> : null}
          {effectiveRouteId === 'teacher-attendance' && user.category === 'teacher' ? <TeacherAcademicPanel mode="attendance" /> : null}
          {effectiveRouteId === 'teacher-grades' && user.category === 'teacher' ? <TeacherAcademicPanel mode="grades" /> : null}
          {effectiveRouteId === 'teacher-schedule' && user.category === 'teacher' ? <TeacherSchedulePanel /> : null}
          {effectiveRouteId === 'student-schedule' && user.category === 'student' ? <StudentSchedulePanel /> : null}
          {effectiveRouteId === 'student-grades' && user.category === 'student' ? <StudentGradesPanel /> : null}
        </section>
      </section>
    </main>
  )
}
