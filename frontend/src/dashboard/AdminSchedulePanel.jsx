import { useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
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
  fechas_clase: [],
}
const WEEKDAY_SHORT_LABELS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const monthFormatter = new Intl.DateTimeFormat('es-EC', { month: 'long', year: 'numeric' })

export default function AdminSchedulePanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [selectedTeacherId, setSelectedTeacherId] = useState('')
  const [scheduleData, setScheduleData] = useState(null)
  const [form, setForm] = useState(emptyScheduleForm)
  const [calendarMonth, setCalendarMonth] = useState(() => toMonthKey(new Date()))
  const [isLoading, setIsLoading] = useState(true)
  const [isScheduleLoading, setIsScheduleLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const selectedCut = useMemo(
    () => cuts.find((cut) => String(cut.corte_id) === String(selectedCutId)) || null,
    [cuts, selectedCutId],
  )
  const teachers = useMemo(() => scheduleData?.teachers || [], [scheduleData?.teachers])
  const selectedTeacher = useMemo(
    () => teachers.find((teacher) => String(teacher.docente_corte_id) === String(selectedTeacherId)) || null,
    [teachers, selectedTeacherId],
  )
  const selectedDateSet = useMemo(() => new Set(form.fechas_clase || []), [form.fechas_clase])
  const calendarDays = useMemo(() => buildCalendarDays(calendarMonth), [calendarMonth])

  useEffect(() => {
    let isMounted = true

    async function loadCuts() {
      setIsLoading(true)
      setError('')

      try {
        const response = await adminFetch('/api/auth/admin/course-cuts/')
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok) {
          throw new Error(payload?.message ?? `No fue posible cargar cohortes (${response.status}).`)
        }

        if (!isMounted) {
          return
        }

        const loadedCuts = payload.cuts || []
        const firstCut = loadedCuts[0] || null
        setCuts(loadedCuts)
        setSelectedCutId(firstCut?.corte_id || '')
        setForm(buildScheduleForm(firstCut, null))
        setCalendarMonth(resolveCalendarMonth(firstCut, []))
        if (firstCut) {
          await loadScheduleData(firstCut)
        } else {
          setScheduleData(null)
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

    loadCuts()

    return () => {
      isMounted = false
    }
  }, [])

  async function loadScheduleData(cut, { resetForm = true } = {}) {
    if (!cut?.corte_id) {
      return null
    }

    setIsScheduleLoading(true)
    setError('')

    try {
      const params = new URLSearchParams({ corte_id: cut.corte_id })
      const response = await adminFetch(`/api/auth/admin/course-cuts/schedule/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible cargar horario (${response.status}).`)
      }

      const result = payload.result || {}
      setScheduleData(result)
      setSelectedTeacherId((current) => resolveSelectedTeacherId(result, current))
      if (resetForm) {
        const nextForm = buildScheduleForm(cut, result)
        setForm(nextForm)
        setCalendarMonth(resolveCalendarMonth(cut, nextForm.fechas_clase))
      }
      return result
    } catch (loadError) {
      setError(loadError.message)
      return null
    } finally {
      setIsScheduleLoading(false)
    }
  }

  async function handleCutChange(event) {
    const nextCutId = event.target.value
    const nextCut = cuts.find((cut) => String(cut.corte_id) === String(nextCutId)) || null
    setSelectedCutId(nextCutId)
    setSelectedTeacherId('')
    setScheduleData(null)
    setForm(buildScheduleForm(nextCut, null))
    setCalendarMonth(resolveCalendarMonth(nextCut, []))
    setMessage('')
    setError('')
    if (nextCut) {
      await loadScheduleData(nextCut)
    }
  }

  function handleChange(event) {
    const { name, value, checked, type } = event.target
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  function handleEditSchedule(schedule) {
    const scheduleDates = sessionDatesFromData(scheduleData, schedule?.horario_id)
    setForm((current) => ({
      ...current,
      horario_id: schedule?.horario_id || '',
      dia_semana: schedule?.dia_semana ? String(schedule.dia_semana) : current.dia_semana,
      hora_inicio: schedule?.hora_inicio || current.hora_inicio,
      hora_fin: schedule?.hora_fin || current.hora_fin,
      modalidad: schedule?.modalidad || current.modalidad,
      aula: schedule?.aula || '',
      enlace_virtual: schedule?.enlace_virtual || current.enlace_virtual,
      fechas_clase: scheduleDates,
    }))
    setCalendarMonth(resolveCalendarMonth(selectedCut, scheduleDates))
    setMessage(`Editando horario ${schedule?.dia_semana_label || ''} ${schedule?.hora_inicio || ''}.`)
  }

  function toggleClassDate(dateIso) {
    if (!dateIso || isCalendarDateDisabled(dateIso, selectedCut)) {
      return
    }
    setForm((current) => {
      const currentDates = current.fechas_clase || []
      const exists = currentDates.includes(dateIso)
      const nextDates = exists
        ? currentDates.filter((item) => item !== dateIso)
        : [...currentDates, dateIso].sort()
      return {
        ...current,
        fechas_clase: nextDates,
        dia_semana: nextDates[0] ? String(weekdayFromIsoDate(nextDates[0])) : current.dia_semana,
      }
    })
  }

  function clearSelectedDates() {
    setForm((current) => ({
      ...current,
      fechas_clase: [],
    }))
  }

  async function handleScheduleSubmit(event) {
    event.preventDefault()
    if (!selectedCut?.corte_id) {
      setError('Selecciona una cohorte para guardar el horario.')
      return
    }
    if (!form.fechas_clase?.length) {
      setError('Selecciona al menos un día de clase en el calendario.')
      return
    }
    if (!selectedTeacherId) {
      setError('Selecciona el docente correspondiente antes de guardar el horario.')
      return
    }

    setIsSaving(true)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/schedule/save/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...form,
          corte_id: selectedCut.corte_id,
          docente_corte_id: selectedTeacherId,
          codigo_docente: selectedTeacher?.codigo_docente || '',
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible guardar horario (${response.status}).`)
      }

      const updated = payload.result?.updated || null
      setScheduleData(updated)
      setSelectedTeacherId((current) => resolveSelectedTeacherId(updated, current))
      const nextForm = buildScheduleForm(selectedCut, updated)
      setForm(nextForm)
      setCalendarMonth(resolveCalendarMonth(selectedCut, nextForm.fechas_clase))
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
          <h3>Cargando horarios</h3>
          <p>Estamos consultando las cohortes disponibles.</p>
        </div>
      </article>
    )
  }

  const metrics = scheduleData?.metrics || {}
  const scheduleUnavailable = scheduleData?.continuing_education && !scheduleData.continuing_education.available
  const teacherUnavailable = selectedCut && scheduleData && !isScheduleLoading && !teachers.length
  const selectedDates = form.fechas_clase || []
  const visibleSelectedDates = selectedDates.slice(0, 8)
  const hiddenSelectedDatesCount = Math.max(selectedDates.length - visibleSelectedDates.length, 0)

  return (
    <section className="teacher-panel admin-schedule-panel" aria-labelledby="admin-schedule-title">
      <div className="admin-section-heading">
        <div>
          <h3 id="admin-schedule-title">Horario</h3>
          <p>Crea horarios por cohorte y genera las sesiones vinculadas.</p>
        </div>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="form-success">{message}</p> : null}

      <article className="module-card teacher-panel-card admin-schedule-selector-card">
        <label className="field">
          <span>Cohorte</span>
          <select value={selectedCutId} onChange={handleCutChange} disabled={!cuts.length || isScheduleLoading}>
            {cuts.length ? (
              cuts.map((cut) => (
                <option key={cut.corte_id} value={cut.corte_id}>
                  {cutLabel(cut)}
                </option>
              ))
            ) : (
              <option value="">No hay cohortes registradas</option>
            )}
          </select>
        </label>

        <label className="field">
          <span>Docente encargado del grupo</span>
          <select
            value={selectedTeacherId}
            onChange={(event) => setSelectedTeacherId(event.target.value)}
            disabled={!teachers.length || isScheduleLoading}
          >
            {teachers.length ? (
              teachers.map((teacher) => (
                <option key={teacher.docente_corte_id} value={teacher.docente_corte_id}>
                  {teacher.nombre} - {teacher.rol_docente || 'DOCENTE'}
                </option>
              ))
            ) : (
              <option value="">Sin docente matriculado</option>
            )}
          </select>
          {selectedTeacher ? (
            <small className="admin-schedule-teacher-meta">
              Cédula {selectedTeacher.cedula || '-'} · Código {selectedTeacher.codigo_docente || '-'} ·{' '}
              {selectedTeacher.correo_intec || selectedTeacher.correo_personal || 'sin correo'}
            </small>
          ) : null}
        </label>

        {selectedCut ? (
          <section className="schedule-summary-grid" aria-label="Resumen de horario administrativo">
            <div>
              <span>Horarios</span>
              <strong>{formatNumber(metrics.horarios)}</strong>
            </div>
            <div>
              <span>Sesiones</span>
              <strong>{formatNumber(metrics.sesiones)}</strong>
            </div>
            <div>
              <span>Docentes</span>
              <strong>{formatNumber(teachers.length)}</strong>
            </div>
            <div>
              <span>Fechas</span>
              <strong>{formatNumber(scheduleData?.sessions?.length)}</strong>
            </div>
          </section>
        ) : null}
        {teacherUnavailable ? (
          <p className="teacher-panel-empty admin-schedule-teacher-warning">
            Primero matricula el docente correspondiente para esta cohorte.
          </p>
        ) : null}
      </article>

      {selectedCut ? (
        <>
          <article className="module-card teacher-panel-card admin-schedule-editor-card">
            {scheduleUnavailable ? <p className="form-error">{scheduleData?.continuing_education?.message}</p> : null}
            {isScheduleLoading ? <p className="form-success">Cargando horario...</p> : null}

            <form className="auth-form compact-form schedule-form" onSubmit={handleScheduleSubmit}>
              <div className="schedule-calendar-panel">
                <div className="schedule-calendar-header">
                  <div>
                    <span className="eyebrow">Calendario</span>
                    <h4>{capitalize(monthFormatter.format(monthDateFromKey(calendarMonth)))}</h4>
                  </div>
                  <div className="schedule-calendar-actions">
                    <button
                      type="button"
                      className="ghost-button compact-button"
                      onClick={() => setCalendarMonth(shiftMonth(calendarMonth, -1))}
                    >
                      Anterior
                    </button>
                    <button
                      type="button"
                      className="ghost-button compact-button"
                      onClick={() => setCalendarMonth(resolveCalendarMonth(selectedCut, form.fechas_clase))}
                    >
                      Actual
                    </button>
                    <button
                      type="button"
                      className="ghost-button compact-button"
                      onClick={() => setCalendarMonth(shiftMonth(calendarMonth, 1))}
                    >
                      Siguiente
                    </button>
                  </div>
                </div>

                <div className="schedule-calendar-grid" aria-label="Calendario de días de clase">
                  {WEEKDAY_SHORT_LABELS.map((label) => (
                    <span key={label} className="schedule-calendar-weekday">
                      {label}
                    </span>
                  ))}
                  {calendarDays.map((day) => {
                    const selected = selectedDateSet.has(day.iso)
                    const disabled = isCalendarDateDisabled(day.iso, selectedCut)
                    return (
                      <button
                        key={day.iso}
                        type="button"
                        className={[
                          'schedule-calendar-day',
                          day.isCurrentMonth ? '' : 'is-muted',
                          selected ? 'is-selected' : '',
                        ].filter(Boolean).join(' ')}
                        onClick={() => toggleClassDate(day.iso)}
                        disabled={disabled}
                        aria-pressed={selected}
                      >
                        <span>{day.day}</span>
                      </button>
                    )
                  })}
                </div>

                <div className="schedule-selected-dates">
                  <div>
                    <strong>{formatNumber(form.fechas_clase?.length)} día(s) seleccionados</strong>
                    <span>{selectedCut ? cutLabel(selectedCut) : 'Selecciona una cohorte'}</span>
                  </div>
                  {selectedDates.length ? (
                    <button type="button" className="ghost-button compact-button" onClick={clearSelectedDates}>
                      Limpiar días
                    </button>
                  ) : null}
                </div>

                {selectedDates.length ? (
                  <div className="schedule-date-chip-list" aria-label="Días de clase seleccionados">
                    {visibleSelectedDates.map((dateIso) => (
                      <button
                        key={dateIso}
                        type="button"
                        className="schedule-date-chip"
                        onClick={() => toggleClassDate(dateIso)}
                      >
                        {dateIso}
                      </button>
                    ))}
                    {hiddenSelectedDatesCount ? (
                      <span className="schedule-date-chip is-count">+{hiddenSelectedDatesCount}</span>
                    ) : null}
                  </div>
                ) : (
                  <p className="teacher-panel-empty">Selecciona en el calendario los días reales de clase.</p>
                )}
              </div>

              <div className="admin-form-grid schedule-form-grid">
                <label className="field schedule-teacher-field">
                  <span>Docente responsable *</span>
                  <select
                    value={selectedTeacherId}
                    onChange={(event) => setSelectedTeacherId(event.target.value)}
                    disabled={!teachers.length || isScheduleLoading}
                    required
                  >
                    {teachers.length ? (
                      teachers.map((teacher) => (
                        <option key={teacher.docente_corte_id} value={teacher.docente_corte_id}>
                          {teacher.nombre} - {teacher.rol_docente || 'DOCENTE'}
                        </option>
                      ))
                    ) : (
                      <option value="">Sin docente matriculado</option>
                    )}
                  </select>
                  {selectedTeacher ? (
                    <small className="admin-schedule-teacher-meta">
                      Cédula {selectedTeacher.cedula || '-'} · Código {selectedTeacher.codigo_docente || '-'}
                    </small>
                  ) : null}
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

              </div>

              <div className="student-selection-actions">
                <button
                  type="button"
                  className="ghost-button compact-button"
                  onClick={() => {
                    const nextForm = buildScheduleForm(selectedCut, scheduleData)
                    setForm(nextForm)
                    setCalendarMonth(resolveCalendarMonth(selectedCut, nextForm.fechas_clase))
                  }}
                  disabled={isSaving || isScheduleLoading}
                >
                  Limpiar
                </button>
                <button
                  type="submit"
                  className="submit-button compact-button"
                  disabled={isSaving || isScheduleLoading || Boolean(scheduleUnavailable) || !selectedTeacherId}
                >
                  {isSaving ? 'Guardando...' : 'Guardar horario'}
                </button>
              </div>
            </form>
          </article>

          <article className="module-card teacher-panel-card admin-schedule-table-card">
            <div className="admin-subsection-header">
              <div>
                <h4>Horarios creados</h4>
                <p>{scheduleData?.schedules?.length || 0} registro(s) activos para la cohorte seleccionada.</p>
              </div>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => loadScheduleData(selectedCut, { resetForm: false })}
                disabled={isScheduleLoading}
              >
                Actualizar
              </button>
            </div>

            <div className="admin-table-wrap">
              <table className="admin-table schedule-table">
                <thead>
                  <tr>
                    <th>Día</th>
                    <th>Hora</th>
                    <th>Docente</th>
                    <th>Modalidad</th>
                    <th>Sesiones</th>
                    <th>Acción</th>
                  </tr>
                </thead>
                <tbody>
                  {scheduleData?.schedules?.length ? (
                    scheduleData.schedules.map((schedule) => (
                      <tr key={schedule.horario_id}>
                        <td>{schedule.dia_semana_label || '-'}</td>
                        <td>
                          <strong>{schedule.hora_inicio || '-'}</strong>
                          <span>{schedule.hora_fin || '-'}</span>
                        </td>
                        <td>
                          <strong>{schedule.docente_responsable?.nombre || selectedTeacher?.nombre || '-'}</strong>
                          <span>
                            {schedule.docente_responsable?.codigo_docente || selectedTeacher?.codigo_docente || ''}
                          </span>
                        </td>
                        <td>{schedule.modalidad || '-'}</td>
                        <td>{formatNumber(schedule.total_sesiones)}</td>
                        <td>
                          <button
                            type="button"
                            className="ghost-button compact-button table-action-button"
                            onClick={() => handleEditSchedule(schedule)}
                          >
                            Editar
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="6">No hay horarios creados para esta cohorte.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </article>

          <article className="module-card teacher-panel-card admin-schedule-table-card">
            <div className="admin-subsection-header">
              <div>
                <h4>Fechas de clase</h4>
                <p>{scheduleData?.sessions?.length || 0} sesión(es) programadas para la materia seleccionada.</p>
              </div>
            </div>

            <div className="admin-table-wrap">
              <table className="admin-table schedule-table">
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Hora</th>
                    <th>Modalidad</th>
                    <th>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {scheduleData?.sessions?.length ? (
                    scheduleData.sessions.map((session) => (
                      <tr key={session.sesion_id}>
                        <td>{session.fecha || '-'}</td>
                        <td>
                          <strong>{session.hora_inicio || '-'}</strong>
                          <span>{session.hora_fin || '-'}</span>
                        </td>
                        <td>{session.modalidad || '-'}</td>
                        <td>{session.estado || '-'}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="4">No hay fechas de clase programadas.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </article>

        </>
      ) : (
        <article className="module-card dashboard-module-card">
          <div>
            <h3>Sin cohortes disponibles</h3>
            <p>Crea una cohorte antes de cargar horarios.</p>
          </div>
        </article>
      )}
    </section>
  )
}

function buildScheduleForm(cut, scheduleData) {
  const firstSchedule = scheduleData?.schedules?.[0] || null
  const fechasClase = sessionDatesFromData(scheduleData)

  return {
    ...emptyScheduleForm,
    horario_id: firstSchedule?.horario_id || '',
    dia_semana: firstSchedule?.dia_semana ? String(firstSchedule.dia_semana) : emptyScheduleForm.dia_semana,
    hora_inicio: firstSchedule?.hora_inicio || emptyScheduleForm.hora_inicio,
    hora_fin: firstSchedule?.hora_fin || emptyScheduleForm.hora_fin,
    modalidad: firstSchedule?.modalidad || emptyScheduleForm.modalidad,
    aula: firstSchedule?.aula || '',
    enlace_virtual: firstSchedule?.enlace_virtual || '',
    fecha_desde: cut?.fecha_inicio_iso || '',
    fecha_hasta: cut?.fecha_fin_iso || '',
    fechas_clase: fechasClase,
  }
}

function cutLabel(cut) {
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || cut.cod_curso || 'Sin materia'
  const period = cut.periodo || cut.codigo_periodo || cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const status = cut.estado_inscripcion || cut.estado_corte || ''
  return `${subject} - ${period}${status ? ` - ${status}` : ''}`
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function resolveSelectedTeacherId(scheduleData, currentTeacherId = '') {
  const teachers = scheduleData?.teachers || []
  if (!teachers.length) {
    return ''
  }
  if (currentTeacherId && teachers.some((teacher) => String(teacher.docente_corte_id) === String(currentTeacherId))) {
    return currentTeacherId
  }
  const titular = teachers.find((teacher) => String(teacher.rol_docente || '').toUpperCase() === 'TITULAR')
  return String((titular || teachers[0]).docente_corte_id || '')
}

function sessionDatesFromData(scheduleData, horarioId = '') {
  const normalizedHorarioId = String(horarioId || '')
  const dates = (scheduleData?.sessions || [])
    .filter((session) => !normalizedHorarioId || String(session.horario_id) === normalizedHorarioId)
    .map((session) => session.fecha)
    .filter(Boolean)
  return [...new Set(dates)].sort()
}

function buildCalendarDays(monthKey) {
  const [year, month] = monthKey.split('-').map(Number)
  const firstDay = new Date(year, month - 1, 1)
  const gridStart = new Date(firstDay)
  const mondayOffset = (firstDay.getDay() + 6) % 7
  gridStart.setDate(firstDay.getDate() - mondayOffset)

  return Array.from({ length: 42 }, (_, index) => {
    const current = new Date(gridStart)
    current.setDate(gridStart.getDate() + index)
    return {
      iso: toDateIso(current),
      day: current.getDate(),
      isCurrentMonth: current.getMonth() === month - 1,
    }
  })
}

function toDateIso(value) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function toMonthKey(value) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}`
}

function monthDateFromKey(monthKey) {
  const [year, month] = monthKey.split('-').map(Number)
  return new Date(year, month - 1, 1)
}

function shiftMonth(monthKey, offset) {
  const current = monthDateFromKey(monthKey)
  current.setMonth(current.getMonth() + offset)
  return toMonthKey(current)
}

function resolveCalendarMonth(cut, selectedDates = []) {
  const firstDate = selectedDates?.[0] || cut?.fecha_inicio_iso || ''
  if (firstDate) {
    const parsed = new Date(`${firstDate}T00:00:00`)
    if (!Number.isNaN(parsed.getTime())) {
      return toMonthKey(parsed)
    }
  }
  return toMonthKey(new Date())
}

function isCalendarDateDisabled(dateIso, cut) {
  if (!cut) {
    return true
  }
  if (cut.fecha_inicio_iso && dateIso < cut.fecha_inicio_iso) {
    return true
  }
  return false
}

function weekdayFromIsoDate(dateIso) {
  const parsed = new Date(`${dateIso}T00:00:00`)
  const day = parsed.getDay()
  return day === 0 ? 7 : day
}

function capitalize(value) {
  const text = String(value || '')
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''
}
