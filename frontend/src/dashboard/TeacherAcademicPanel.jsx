import { useEffect, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const decimalFormatter = new Intl.NumberFormat('es-EC', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const PANEL_CONFIG = {
  attendance: {
    endpoint: '/api/auth/teacher/attendance/',
    title: 'Asistencia',
    description: 'Consulta el resumen de asistencia asociado a tus materias.',
    empty: 'No hay cursos asignados para mostrar asistencia.',
    metrics: [
      { key: 'cursos_asignados', label: 'Cursos asignados' },
      { key: 'estudiantes', label: 'Estudiantes' },
      { key: 'clases_registradas', label: 'Clases registradas' },
      { key: 'registros_asistencia', label: 'Registros' },
    ],
  },
  grades: {
    endpoint: '/api/auth/teacher/grades/',
    title: 'Calificaciones',
    description: 'Consulta el avance de calificaciones por materia y periodo.',
    empty: 'No hay cursos asignados para mostrar calificaciones.',
    metrics: [
      { key: 'cursos_asignados', label: 'Cursos asignados' },
      { key: 'estudiantes', label: 'Estudiantes' },
      { key: 'registros_calificados', label: 'Registros con nota' },
      { key: 'promedio_final', label: 'Promedio final', decimal: true },
    ],
  },
}

export default function TeacherAcademicPanel({ mode }) {
  const config = PANEL_CONFIG[mode] || PANEL_CONFIG.attendance
  const [dashboard, setDashboard] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  async function refreshDashboard() {
    const response = await adminFetch(config.endpoint)
    const payload = await readResponsePayload(response)

    if (!payload || !response.ok || !payload.ok || !payload.dashboard) {
      throw new Error(payload?.message ?? `No fue posible actualizar ${config.title.toLowerCase()} (${response.status}).`)
    }

    setDashboard(payload.dashboard)
  }

  useEffect(() => {
    let isMounted = true

    async function loadDashboard() {
      setIsLoading(true)
      setError('')

      try {
        const response = await adminFetch(config.endpoint)
        const payload = await readResponsePayload(response)

        if (!payload || !response.ok || !payload.ok || !payload.dashboard) {
          throw new Error(payload?.message ?? `No fue posible cargar ${config.title.toLowerCase()} (${response.status}).`)
        }

        if (isMounted) {
          setDashboard(payload.dashboard)
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

    loadDashboard()

    return () => {
      isMounted = false
    }
  }, [config])

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando {config.title.toLowerCase()}</h3>
          <p>Estamos consultando la información académica del docente.</p>
        </div>
      </article>
    )
  }

  if (error) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>No se pudo cargar {config.title.toLowerCase()}</h3>
          <p>{error}</p>
        </div>
      </article>
    )
  }

  const metrics = dashboard?.metrics || {}
  const courses = dashboard?.courses || []

  return (
    <section className="teacher-panel" aria-labelledby={`teacher-${mode}-title`}>
      <div className="admin-section-heading">
        <div>
          <span className="eyebrow">Docente</span>
          <h3 id={`teacher-${mode}-title`}>{config.title}</h3>
          <p>{config.description}</p>
        </div>
      </div>

      <section className="summary-grid teacher-summary-grid" aria-label={`Indicadores de ${config.title.toLowerCase()}`}>
        {config.metrics.map((metric) => (
          <article className="summary-card" key={metric.key}>
            <span>{metric.label}</span>
            <strong>{formatMetric(metrics[metric.key], metric.decimal)}</strong>
          </article>
        ))}
      </section>

      <article className="module-card teacher-panel-card">
        {mode === 'attendance' ? (
          <AttendanceManager courses={courses} onDashboardRefresh={refreshDashboard} />
        ) : courses.length ? (
          <div className="admin-table-wrap">
            <table className="admin-table teacher-panel-table">
              <GradesTableHead />
              <tbody>
                {courses.map((course) => (
                  <GradesRow course={course} key={courseKey(course)} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="teacher-panel-empty">{config.empty}</p>
        )}
      </article>
    </section>
  )
}

function AttendanceManager({ courses, onDashboardRefresh }) {
  const [selectedSubjectKey, setSelectedSubjectKey] = useState('')
  const [selectedCourseKey, setSelectedCourseKey] = useState('')
  const [attendanceDate, setAttendanceDate] = useState(todayInputValue)
  const [attendanceTime, setAttendanceTime] = useState(currentTimeInputValue)
  const [isAttendanceModalOpen, setIsAttendanceModalOpen] = useState(false)
  const [roster, setRoster] = useState(null)
  const [isLoadingRoster, setIsLoadingRoster] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const subjectOptions = subjectCourseOptions(courses)
  const selectedSubjectCourses = courses.filter((course) => subjectKey(course) === selectedSubjectKey)
  const selectedCourse = courses.find((course) => courseKey(course) === selectedCourseKey) || null

  if (!courses.length) {
    return <p className="teacher-panel-empty">No hay materias asignadas para registrar asistencia.</p>
  }

  async function loadRoster(course = selectedCourse) {
    if (!course) {
      setError('Selecciona una materia antes de cargar estudiantes.')
      return
    }

    setIsLoadingRoster(true)
    setError('')
    setMessage('')

    try {
      const params = new URLSearchParams({
        cod_anio_basica: course.cod_anio_basica,
        codigo_materia: course.codigo_materia,
        codigo_periodo: course.codigo_periodo,
        paralelo: course.paralelo,
        cod_jornada: course.cod_jornada,
        fecha: attendanceDate,
      })
      if (course.corte_id) {
        params.set('corte_id', course.corte_id)
      }
      if (course.source) {
        params.set('source', course.source)
      }
      const response = await adminFetch(`/api/auth/teacher/attendance/roster/?${params.toString()}`)
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar estudiantes (${response.status}).`)
      }

      setRoster(payload.result)
      setMessage(`${payload.result.students?.length || 0} estudiante(s) cargado(s).`)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setIsLoadingRoster(false)
    }
  }

  function handleCourseChange(event) {
    const nextKey = event.target.value
    const nextCourse = courses.find((course) => courseKey(course) === nextKey) || null
    setSelectedCourseKey(nextKey)
    setRoster(null)
    setMessage('')
    setError('')

    if (nextCourse) {
      setIsAttendanceModalOpen(true)
      loadRoster(nextCourse)
    } else {
      setIsAttendanceModalOpen(false)
    }
  }

  function openAttendanceCourse(course) {
    setSelectedCourseKey(courseKey(course))
    setRoster(null)
    setMessage('')
    setError('')
    setIsAttendanceModalOpen(true)
    loadRoster(course)
  }

  function handleSubjectChange(event) {
    const nextSubjectKey = event.target.value
    setSelectedSubjectKey(nextSubjectKey)
    setSelectedCourseKey('')
    setIsAttendanceModalOpen(false)
    setRoster(null)
    setMessage('')
    setError('')
  }

  function closeAttendanceModal() {
    setIsAttendanceModalOpen(false)
    setRoster(null)
    setMessage('')
    setError('')
  }

  function updateStudentAttendance(codigoEstud, presente) {
    setRoster((current) => {
      if (!current) {
        return current
      }

      return {
        ...current,
        students: current.students.map((student) => (
          student.codigo_estud === codigoEstud
            ? { ...student, presente, asistencia: presente ? 1 : 0 }
            : student
        )),
      }
    })
  }

  function markAll(presente) {
    setRoster((current) => {
      if (!current) {
        return current
      }

      return {
        ...current,
        students: current.students.map((student) => ({
          ...student,
          presente,
          asistencia: presente ? 1 : 0,
        })),
      }
    })
  }

  async function handleSubmit(event) {
    event.preventDefault()

    if (!selectedCourse) {
      setError('Selecciona una materia antes de guardar asistencia.')
      return
    }
    if (!roster?.students?.length) {
      setError('Carga estudiantes antes de guardar asistencia.')
      return
    }

    setIsSaving(true)
    setError('')
    setMessage('')

    try {
      const response = await adminFetch('/api/auth/teacher/attendance/save/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...coursePayload(selectedCourse),
          fecha: attendanceDate,
          hora: attendanceTime,
          records: roster.students.map((student) => ({
            codigo_estud: student.codigo_estud,
            corte_estudiante_id: student.corte_estudiante_id,
            asistencia: student.presente ? 1 : 0,
          })),
        }),
      })
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible guardar asistencia (${response.status}).`)
      }

      setRoster(payload.result)
      setMessage(`${payload.result.saved} asistencia(s) guardada(s).`)
      await onDashboardRefresh()
    } catch (saveError) {
      setError(saveError.message)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="attendance-entry-form">
      <div className="attendance-picker-grid">
        <label className="field">
          <span>Materia</span>
          <select value={selectedSubjectKey} onChange={handleSubjectChange} required>
            <option value="">Selecciona una materia</option>
            {subjectOptions.map((course) => (
              <option key={subjectKey(course)} value={subjectKey(course)}>
                {course.materia}
              </option>
            ))}
          </select>
        </label>

        {selectedSubjectKey ? (
          <label className="field">
            <span>Período</span>
            <select value={selectedCourseKey} onChange={handleCourseChange} required>
              <option value="">Selecciona un periodo</option>
              {selectedSubjectCourses.map((course) => (
                <option key={courseKey(course)} value={courseKey(course)}>
                  {coursePeriodOptionLabel(course)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {selectedSubjectKey ? (
        <section className="attendance-period-list" aria-label="Períodos correspondientes">
          <div className="attendance-period-header">
            <div>
              <strong>{subjectLabel(selectedSubjectCourses[0])}</strong>
              <span>{selectedSubjectCourses.length} período(s) correspondiente(s).</span>
            </div>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table teacher-panel-table">
              <thead>
                <tr>
                  <th>Período</th>
                  <th>Carrera</th>
                  <th>Paralelo</th>
                  <th>Jornada</th>
                  <th>Estudiantes</th>
                  <th>Registros</th>
                  <th>Accion</th>
                </tr>
              </thead>
              <tbody>
                {selectedSubjectCourses.map((course) => (
                  <tr key={courseKey(course)}>
                    <td>
                      <strong>{course.periodo}</strong>
                      <span>{course.estado_periodo === 'A' ? 'Activo' : 'Histórico'}</span>
                    </td>
                    <td>{course.carrera}</td>
                    <td>{course.paralelo}</td>
                    <td>{course.jornada}</td>
                    <td>{formatNumber(course.estudiantes)}</td>
                    <td>{formatNumber(course.registros_asistencia)}</td>
                    <td>
                      <button
                        type="button"
                        className="ghost-button compact-button table-action-button"
                        onClick={() => openAttendanceCourse(course)}
                      >
                        Registrar
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {selectedCourse && !isAttendanceModalOpen ? (
        <div className="attendance-selected-course">
          <strong>{courseTitle(selectedCourse)}</strong>
          <span>
            {selectedCourse.carrera} - {selectedCourse.periodo} - Paralelo {selectedCourse.paralelo} - {selectedCourse.jornada}
          </span>
          <button type="button" className="ghost-button compact-button" onClick={() => setIsAttendanceModalOpen(true)}>
            Abrir asistencia
          </button>
        </div>
      ) : null}

      {isAttendanceModalOpen && selectedCourse ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="career-modal attendance-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="attendance-modal-title"
          >
            <div className="career-modal-header">
              <div>
                <h4 id="attendance-modal-title">Registrar asistencia</h4>
                <p>{courseTitle(selectedCourse)} - {selectedCourse.periodo}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={closeAttendanceModal}>
                Cerrar
              </button>
            </div>

            <div className="career-modal-body">
              <form className="attendance-entry-form" onSubmit={handleSubmit}>
                <div className="attendance-selected-course">
                  <strong>{courseTitle(selectedCourse)}</strong>
                  <span>
                    {selectedCourse.carrera} - {selectedCourse.periodo} - Paralelo {selectedCourse.paralelo} - {selectedCourse.jornada}
                  </span>
                </div>

                <div className="attendance-modal-controls">
                  <label className="field">
                    <span>Fecha</span>
                    <input
                      type="date"
                      value={attendanceDate}
                      onChange={(event) => {
                        setAttendanceDate(event.target.value)
                        setRoster(null)
                      }}
                      required
                    />
                  </label>

                  <label className="field">
                    <span>Hora</span>
                    <input
                      type="time"
                      value={attendanceTime}
                      onChange={(event) => setAttendanceTime(event.target.value)}
                      required
                    />
                  </label>

                  <button
                    type="button"
                    className="ghost-button compact-button"
                    onClick={() => loadRoster()}
                    disabled={isLoadingRoster}
                  >
                    {isLoadingRoster ? 'Cargando...' : 'Cargar estudiantes'}
                  </button>
                </div>

                {error ? <p className="status-message error">{error}</p> : null}
                {message ? <p className="status-message success">{message}</p> : null}

                {roster?.students?.length ? (
                  <>
                    <div className="student-selection-actions">
                      <button type="button" className="ghost-button compact-button" onClick={() => markAll(true)}>
                        Marcar presentes
                      </button>
                      <button type="button" className="ghost-button compact-button" onClick={() => markAll(false)}>
                        Marcar ausentes
                      </button>
                    </div>

                    <div className="admin-table-wrap attendance-roster-table">
                      <table className="admin-table teacher-panel-table">
                        <thead>
                          <tr>
                            <th>Estudiante</th>
                            <th>Cédula</th>
                            <th>Asistencia</th>
                          </tr>
                        </thead>
                        <tbody>
                          {roster.students.map((student) => (
                            <tr key={student.codigo_estud}>
                              <td>
                                <strong>{student.nombre}</strong>
                                <span>Código {student.codigo_estud}</span>
                              </td>
                              <td>{student.cedula || 'N/D'}</td>
                              <td>
                                <label className="attendance-toggle">
                                  <input
                                    type="checkbox"
                                    checked={Boolean(student.presente)}
                                    onChange={(event) => updateStudentAttendance(student.codigo_estud, event.target.checked)}
                                  />
                                  <span>{student.presente ? 'Presente' : 'Ausente'}</span>
                                </label>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <button type="submit" className="submit-button" disabled={isSaving}>
                      {isSaving ? 'Guardando asistencia...' : 'Guardar asistencia'}
                    </button>
                  </>
                ) : null}

                {roster && !roster.students?.length ? (
                  <p className="teacher-panel-empty">No hay estudiantes matriculados en la materia seleccionada.</p>
                ) : null}
              </form>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}

function GradesTableHead() {
  return (
    <thead>
      <tr>
        <th>Materia</th>
        <th>Carrera</th>
        <th>Período</th>
        <th>Paralelo</th>
        <th>Estudiantes</th>
        <th>Calificados</th>
        <th>P1</th>
        <th>P2</th>
        <th>P3</th>
        <th>Final</th>
      </tr>
    </thead>
  )
}

function GradesRow({ course }) {
  return (
    <tr>
      <CourseCell course={course} />
      <td>{course.carrera}</td>
      <PeriodCell course={course} />
      <td>{course.paralelo}</td>
      <td>{formatNumber(course.estudiantes)}</td>
      <td>{formatNumber(course.registros_calificados)}</td>
      <td>{formatDecimal(course.promedio_p1)}</td>
      <td>{formatDecimal(course.promedio_p2)}</td>
      <td>{formatDecimal(course.promedio_p3)}</td>
      <td>{formatDecimal(course.promedio_final)}</td>
    </tr>
  )
}

function CourseCell({ course }) {
  return (
    <td>
      <strong>{course.materia}</strong>
      <span>Código {courseDisplayCode(course)}</span>
    </td>
  )
}

function PeriodCell({ course }) {
  return (
    <td>
      <strong>{course.periodo}</strong>
      <span>{course.estado_periodo === 'A' ? 'Activo' : 'Histórico'}</span>
    </td>
  )
}

function formatMetric(value, decimal = false) {
  return decimal ? formatDecimal(value) : formatNumber(value)
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function formatDecimal(value) {
  if (value === null || value === undefined || value === '') {
    return 'N/D'
  }
  return decimalFormatter.format(Number(value || 0))
}

function courseKey(course) {
  if (course.corte_id) {
    return `corte-${course.corte_id}`
  }

  return [
    course.cod_anio_basica,
    course.codigo_materia,
    course.codigo_periodo,
    course.paralelo,
    course.cod_jornada,
  ].join('-')
}

function subjectCourseOptions(courses) {
  const seen = new Set()
  const options = []

  courses.forEach((course) => {
    const key = subjectKey(course)
    if (!key || seen.has(key)) {
      return
    }
    seen.add(key)
    options.push(course)
  })

  return options
}

function subjectKey(course) {
  return course.cod_materia || course.codigo_materia || course.materia || ''
}

function subjectLabel(course) {
  return course?.materia || 'Materia seleccionada'
}

function courseDisplayCode(course) {
  return course.cod_materia || course.codigo_materia || 'N/D'
}

function courseTitle(course) {
  return `${course.materia} (${courseDisplayCode(course)})`
}

function coursePeriodOptionLabel(course) {
  const parallelLabel = course.paralelo ? ` - Paralelo ${course.paralelo}` : ''
  const journeyLabel = course.jornada && course.jornada !== 'N/D' ? ` - ${course.jornada}` : ''
  return `${course.periodo}${parallelLabel}${journeyLabel}`
}

function coursePayload(course) {
  return {
    source: course.source,
    corte_id: course.corte_id,
    cod_anio_basica: course.cod_anio_basica,
    codigo_materia: course.codigo_materia,
    codigo_periodo: course.codigo_periodo,
    paralelo: course.paralelo,
    cod_jornada: course.cod_jornada,
  }
}

function todayInputValue() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function currentTimeInputValue() {
  const now = new Date()
  const hours = String(now.getHours()).padStart(2, '0')
  const minutes = String(now.getMinutes()).padStart(2, '0')
  return `${hours}:${minutes}`
}
