import { useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const WEEKDAY_OPTIONS = [
  { value: '1', label: 'Lunes' },
  { value: '2', label: 'Martes' },
  { value: '3', label: 'Miércoles' },
  { value: '4', label: 'Jueves' },
  { value: '5', label: 'Viernes' },
  { value: '6', label: 'Sábado' },
  { value: '7', label: 'Domingo' },
]
const MODALITY_OPTIONS = ['EN LÍNEA', 'PRESENCIAL']
const emptyScheduleForm = {
  horario_id: '',
  dia_semana: '1',
  hora_inicio: '18:00',
  hora_fin: '20:00',
  modalidad: 'EN LÍNEA',
  aula: '',
  enlace_virtual: '',
  fecha_desde: '',
  fecha_hasta: '',
  generar_sesiones: true,
}

export default function TeacherSchedulePanel() {
  const [dashboard, setDashboard] = useState(null)
  const [selectedCourseId, setSelectedCourseId] = useState('')
  const [form, setForm] = useState(emptyScheduleForm)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState('')
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
        const response = await adminFetch('/api/auth/teacher/schedule/')
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok || !payload.dashboard) {
          throw new Error(payload?.message ?? `No fue posible cargar horario (${response.status}).`)
        }

        if (!isMounted) {
          return
        }

        const loadedCourses = payload.dashboard.courses || []
        const firstCourse = loadedCourses[0] || null
        setDashboard(payload.dashboard)
        setSelectedCourseId(firstCourse?.corte_id || '')
        setForm(buildFormFromCourse(firstCourse))
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

  function handleCourseChange(event) {
    const nextCourse = courses.find((course) => String(course.corte_id) === String(event.target.value)) || null
    setSelectedCourseId(event.target.value)
    setForm(buildFormFromCourse(nextCourse))
    setMessage('')
    setError('')
  }

  function handleChange(event) {
    const { name, value, checked, type } = event.target
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  function handleEdit(schedule) {
    setForm((current) => ({
      ...current,
      horario_id: schedule.horario_id || '',
      dia_semana: schedule.dia_semana ? String(schedule.dia_semana) : current.dia_semana,
      hora_inicio: schedule.hora_inicio || current.hora_inicio,
      hora_fin: schedule.hora_fin || current.hora_fin,
      modalidad: schedule.modalidad || current.modalidad,
      aula: schedule.aula || '',
      enlace_virtual: schedule.enlace_virtual || '',
    }))
    setMessage('Horario seleccionado para edición.')
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (!selectedCourse) {
      setError('Selecciona una materia antes de guardar el horario.')
      return
    }

    setIsSaving(true)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/teacher/schedule/save/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...form,
          corte_id: selectedCourse.corte_id,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result?.dashboard) {
        throw new Error(payload?.message ?? `No fue posible guardar horario (${response.status}).`)
      }

      const updatedDashboard = payload.result.dashboard
      const updatedCourse = (updatedDashboard.courses || []).find(
        (course) => String(course.corte_id) === String(selectedCourse.corte_id),
      )
      setDashboard(updatedDashboard)
      setSelectedCourseId(selectedCourse.corte_id)
      setForm(buildFormFromCourse(updatedCourse))
      setMessage(payload.message || 'Horario guardado.')
    } catch (saveError) {
      setError(saveError.message)
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando horario</h3>
          <p>Estamos consultando tus materias asignadas.</p>
        </div>
      </article>
    )
  }

  const metrics = dashboard?.metrics || {}

  return (
    <section className="teacher-panel" aria-labelledby="teacher-schedule-title">
      <div className="admin-section-heading">
        <div>
          <span className="eyebrow">Docente</span>
          <h3 id="teacher-schedule-title">Horario</h3>
        </div>
      </div>

      <section className="summary-grid teacher-summary-grid" aria-label="Indicadores de horario docente">
        <article className="summary-card">
          <span>Cursos asignados</span>
          <strong>{formatNumber(metrics.cursos_asignados)}</strong>
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
        {error ? <p className="status-message error">{error}</p> : null}
        {message ? <p className="status-message success">{message}</p> : null}

        {courses.length ? (
          <form className="auth-form compact-form schedule-form" onSubmit={handleSubmit}>
            <div className="admin-form-grid schedule-form-grid">
              <label className="field full-span">
                <span>Materia</span>
                <select value={selectedCourseId} onChange={handleCourseChange} required>
                  {courses.map((course) => (
                    <option key={course.corte_id} value={course.corte_id}>
                      {course.materia} - {course.periodo}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Día *</span>
                <select name="dia_semana" value={form.dia_semana} onChange={handleChange} required>
                  {WEEKDAY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Modalidad *</span>
                <select name="modalidad" value={form.modalidad} onChange={handleChange} required>
                  {MODALITY_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Hora inicio *</span>
                <input name="hora_inicio" type="time" value={form.hora_inicio} onChange={handleChange} required />
              </label>

              <label className="field">
                <span>Hora fin *</span>
                <input name="hora_fin" type="time" value={form.hora_fin} onChange={handleChange} required />
              </label>

              <label className="field">
                <span>Fecha desde</span>
                <input name="fecha_desde" type="date" value={form.fecha_desde} onChange={handleChange} />
              </label>

              <label className="field">
                <span>Fecha hasta</span>
                <input name="fecha_hasta" type="date" value={form.fecha_hasta} onChange={handleChange} />
              </label>

              <label className="field">
                <span>Aula</span>
                <input name="aula" type="text" value={form.aula} onChange={handleChange} placeholder="Aula o sala" />
              </label>

              <label className="field">
                <span>Enlace virtual</span>
                <input
                  name="enlace_virtual"
                  type="url"
                  value={form.enlace_virtual}
                  onChange={handleChange}
                  placeholder="https://teams.microsoft.com/..."
                />
              </label>

              <label className="schedule-checkbox full-span">
                <input
                  name="generar_sesiones"
                  type="checkbox"
                  checked={form.generar_sesiones}
                  onChange={handleChange}
                />
                <span>Generar sesiones para el rango seleccionado</span>
              </label>
            </div>

            <div className="student-selection-actions">
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => setForm(buildFormFromCourse(selectedCourse))}
                disabled={isSaving}
              >
                Limpiar
              </button>
              <button type="submit" className="submit-button compact-button" disabled={isSaving}>
                {isSaving ? 'Guardando...' : 'Guardar horario'}
              </button>
            </div>
          </form>
        ) : (
          <p className="teacher-panel-empty">No hay cortes asignados para cargar horario.</p>
        )}
      </article>

      {selectedCourse ? (
        <article className="module-card teacher-panel-card">
          <div className="module-card-header">
            <div>
              <h4>Horarios cargados</h4>
              <p>{selectedCourse.schedules?.length || 0} horario(s) para {selectedCourse.materia}.</p>
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
                  <th>Acción</th>
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
                      <td>{formatNumber(schedule.total_sesiones)}</td>
                      <td>
                        <button
                          type="button"
                          className="ghost-button compact-button table-action-button"
                          onClick={() => handleEdit(schedule)}
                        >
                          Editar
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="6">No hay horarios cargados para esta materia.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}
    </section>
  )
}

function buildFormFromCourse(course) {
  const firstSchedule = course?.schedules?.[0] || null
  return {
    ...emptyScheduleForm,
    horario_id: firstSchedule?.horario_id || '',
    dia_semana: firstSchedule?.dia_semana ? String(firstSchedule.dia_semana) : emptyScheduleForm.dia_semana,
    hora_inicio: firstSchedule?.hora_inicio || emptyScheduleForm.hora_inicio,
    hora_fin: firstSchedule?.hora_fin || emptyScheduleForm.hora_fin,
    modalidad: firstSchedule?.modalidad || emptyScheduleForm.modalidad,
    aula: firstSchedule?.aula || '',
    enlace_virtual: firstSchedule?.enlace_virtual || '',
    fecha_desde: course?.fecha_inicio || '',
    fecha_hasta: course?.fecha_fin || '',
  }
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}
