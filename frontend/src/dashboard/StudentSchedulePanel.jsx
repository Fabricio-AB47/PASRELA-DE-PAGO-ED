import { useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')

export default function StudentSchedulePanel() {
  const [dashboard, setDashboard] = useState(null)
  const [selectedCourseId, setSelectedCourseId] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const courses = useMemo(() => dashboard?.courses || [], [dashboard?.courses])
  const selectedCourse = useMemo(
    () => courses.find((course) => String(course.corte_id) === String(selectedCourseId)) || null,
    [courses, selectedCourseId],
  )

  useEffect(() => {
    let isMounted = true

    async function loadSchedule() {
      setIsLoading(true)
      setError('')

      try {
        const response = await adminFetch('/api/auth/student/schedule/')
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok || !payload.dashboard) {
          throw new Error(payload?.message ?? `No fue posible cargar el horario (${response.status}).`)
        }

        if (!isMounted) {
          return
        }

        const loadedCourses = payload.dashboard.courses || []
        setDashboard(payload.dashboard)
        setSelectedCourseId(loadedCourses[0]?.corte_id || '')
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

    loadSchedule()

    return () => {
      isMounted = false
    }
  }, [])

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando horario</h3>
          <p>Estamos consultando tus materias matriculadas.</p>
        </div>
      </article>
    )
  }

  if (error) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>No se pudo cargar el horario</h3>
          <p>{error}</p>
        </div>
      </article>
    )
  }

  const metrics = dashboard?.metrics || {}

  return (
    <section className="teacher-panel" aria-labelledby="student-schedule-title">
      <div className="admin-section-heading">
        <div>
          <span className="eyebrow">Estudiante</span>
          <h3 id="student-schedule-title">Horario</h3>
        </div>
      </div>

      <section className="summary-grid teacher-summary-grid" aria-label="Indicadores de horario estudiantil">
        <article className="summary-card">
          <span>Cursos</span>
          <strong>{formatNumber(metrics.cursos)}</strong>
        </article>
        <article className="summary-card">
          <span>Horarios</span>
          <strong>{formatNumber(metrics.horarios)}</strong>
        </article>
        <article className="summary-card">
          <span>Sesiones</span>
          <strong>{formatNumber(metrics.sesiones)}</strong>
        </article>
        <article className="summary-card">
          <span>Virtuales</span>
          <strong>{formatNumber(metrics.virtuales)}</strong>
        </article>
      </section>

      <article className="module-card teacher-panel-card">
        {courses.length ? (
          <>
            <label className="field">
              <span>Materia</span>
              <select value={selectedCourseId} onChange={(event) => setSelectedCourseId(event.target.value)}>
                {courses.map((course) => (
                  <option key={course.corte_id} value={course.corte_id}>
                    {course.materia} - {course.nombre_corte || course.codigo_periodo || 'Sin período'}
                  </option>
                ))}
              </select>
            </label>

            {selectedCourse ? (
              <div className="schedule-list-panel">
                <div className="module-card-header">
                  <div>
                    <h4>{selectedCourse.materia}</h4>
                    <p>
                      {selectedCourse.nombre_corte || selectedCourse.codigo_periodo || 'Sin período'} · Docente:{' '}
                      {selectedCourse.docente || 'Sin docente asignado'}
                    </p>
                  </div>
                </div>

                <div className="schedule-summary-grid" aria-label="Datos de la materia">
                  <div>
                    <span>Estado matrícula</span>
                    <strong>{selectedCourse.estado_matricula || '-'}</strong>
                  </div>
                  <div>
                    <span>Inicio</span>
                    <strong>{selectedCourse.fecha_inicio || '-'}</strong>
                  </div>
                  <div>
                    <span>Fin</span>
                    <strong>{selectedCourse.fecha_fin || '-'}</strong>
                  </div>
                  <div>
                    <span>Horarios</span>
                    <strong>{formatNumber(selectedCourse.schedules?.length)}</strong>
                  </div>
                </div>

                <div className="admin-table-wrap">
                  <table className="admin-table schedule-table">
                    <thead>
                      <tr>
                        <th>Día</th>
                        <th>Hora</th>
                        <th>Modalidad</th>
                        <th>Aula</th>
                        <th>Sesiones</th>
                        <th>Enlace</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedCourse.schedules?.length ? (
                        selectedCourse.schedules.map((schedule) => (
                          <tr key={schedule.horario_id}>
                            <td>{schedule.dia_semana_label || '-'}</td>
                            <td>
                              <strong>{schedule.hora_inicio || '-'}</strong>
                              <span>{schedule.hora_fin || '-'}</span>
                            </td>
                            <td>{schedule.modalidad || '-'}</td>
                            <td>{schedule.aula || '-'}</td>
                            <td>
                              <strong>{formatNumber(schedule.total_sesiones)}</strong>
                              <span>
                                {schedule.primera_sesion || '-'} / {schedule.ultima_sesion || '-'}
                              </span>
                            </td>
                            <td>
                              {schedule.enlace_virtual ? (
                                <a
                                  className="ghost-button compact-button table-action-button"
                                  href={schedule.enlace_virtual}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  Abrir
                                </a>
                              ) : (
                                '-'
                              )}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan="6">El docente aún no ha cargado horario para esta materia.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <p className="teacher-panel-empty">No tienes materias matriculadas con horario disponible.</p>
        )}
      </article>
    </section>
  )
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}
