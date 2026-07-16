import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const emptyTeamsForm = {
  visibility: 'Private',
  team_id: '',
  group_id: '',
  web_url: '',
}

export default function AdminTeamsEnrollmentPanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [selectedTeacherId, setSelectedTeacherId] = useState('')
  const [selectedScheduleId, setSelectedScheduleId] = useState('')
  const [scheduleData, setScheduleData] = useState(null)
  const [form, setForm] = useState(emptyTeamsForm)
  const [isLoading, setIsLoading] = useState(true)
  const [isScheduleLoading, setIsScheduleLoading] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const selectedCut = useMemo(
    () => cuts.find((cut) => String(cut.corte_id) === String(selectedCutId)) || null,
    [cuts, selectedCutId],
  )
  const teachers = useMemo(() => scheduleData?.teachers || [], [scheduleData?.teachers])
  const schedules = useMemo(() => scheduleData?.schedules || [], [scheduleData?.schedules])
  const selectedTeacher = useMemo(
    () => teachers.find((teacher) => String(teacher.docente_corte_id) === String(selectedTeacherId)) || null,
    [teachers, selectedTeacherId],
  )
  const selectedSchedule = useMemo(
    () => schedules.find((schedule) => String(schedule.horario_id) === String(selectedScheduleId)) || null,
    [schedules, selectedScheduleId],
  )

  const loadScheduleData = useCallback(async (cut, preferredScheduleId = '') => {
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
        throw new Error(payload?.message ?? `No fue posible cargar horario y Teams (${response.status}).`)
      }

      const result = payload.result || {}
      setScheduleData(result)
      setSelectedTeacherId((current) => resolveSelectedTeacherId(result, current))
      const nextScheduleId = resolveSelectedScheduleId(result, preferredScheduleId)
      setSelectedScheduleId(nextScheduleId)
      setForm(buildTeamsForm(result, nextScheduleId))
      return result
    } catch (loadError) {
      setError(loadError.message)
      return null
    } finally {
      setIsScheduleLoading(false)
    }
  }, [])

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
  }, [loadScheduleData])

  async function handleCutChange(event) {
    const nextCutId = event.target.value
    const nextCut = cuts.find((cut) => String(cut.corte_id) === String(nextCutId)) || null
    setSelectedCutId(nextCutId)
    setSelectedTeacherId('')
    setSelectedScheduleId('')
    setScheduleData(null)
    setForm(emptyTeamsForm)
    setMessage('')
    setError('')
    if (nextCut) {
      await loadScheduleData(nextCut)
    }
  }

  function handleScheduleChange(event) {
    const scheduleId = event.target.value
    const schedule = schedules.find((item) => String(item.horario_id) === String(scheduleId)) || null
    setSelectedScheduleId(scheduleId)
    if (schedule?.enlace_virtual) {
      setForm((current) => ({
        ...current,
        web_url: schedule.enlace_virtual,
      }))
    }
  }

  function handleChange(event) {
    const { name, value } = event.target
    setForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (!selectedCut?.corte_id) {
      setError('Selecciona una cohorte para matricular por Teams.')
      return
    }
    if (!selectedTeacherId) {
      setError('Selecciona el docente correspondiente.')
      return
    }
    if (!selectedScheduleId) {
      setError('Selecciona el horario base guardado.')
      return
    }
    if (!selectedSchedule || Number(selectedSchedule.total_sesiones || 0) <= 0) {
      setError('El horario seleccionado debe tener sesiones programadas.')
      return
    }

    setIsSubmitting(true)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/teams/sync/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          corte_id: selectedCut.corte_id,
          horario_id: selectedScheduleId,
          docente_corte_id: selectedTeacherId,
          codigo_docente: selectedTeacher?.codigo_docente || '',
          visibility: form.visibility,
          team_id: form.team_id,
          group_id: form.group_id,
          web_url: form.web_url || selectedSchedule?.enlace_virtual || '',
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible matricular por Teams (${response.status}).`)
      }

      const updated = payload.result?.updated || null
      setScheduleData(updated)
      setSelectedTeacherId((current) => resolveSelectedTeacherId(updated, current))
      setSelectedScheduleId((current) => resolveSelectedScheduleId(updated, current))
      setForm(buildTeamsForm(updated, selectedScheduleId))
      setMessage(payload.result?.members_message || payload.message || 'Matrícula por Teams procesada.')
    } catch (submitError) {
      setError(submitError.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando Teams</h3>
          <p>Estamos consultando las cohortes disponibles.</p>
        </div>
      </article>
    )
  }

  const metrics = scheduleData?.metrics || {}
  const teamInfo = scheduleData?.team || scheduleData?.teams?.team || null
  const teamMemberMetrics = scheduleData?.teams?.members || {}
  const graphQueueMetrics = scheduleData?.teams?.queue || {}
  const teamsUnavailable = scheduleData?.teams && !scheduleData.teams.available
  const noTeacher = scheduleData && !isScheduleLoading && !teachers.length
  const noSchedule = scheduleData && !isScheduleLoading && !schedules.length
  const canSubmit = Boolean(
    selectedTeacherId &&
    selectedScheduleId &&
    selectedSchedule &&
    Number(selectedSchedule.total_sesiones || 0) > 0 &&
    !teamsUnavailable,
  )

  return (
    <section className="teacher-panel admin-teams-panel" aria-labelledby="admin-teams-title">
      <div className="admin-section-heading">
        <div>
          <h3 id="admin-teams-title">Matrícula por Teams</h3>
          <p>Matricula docentes y estudiantes en Teams usando el horario ya creado.</p>
        </div>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="form-success">{message}</p> : null}

      <article className="module-card teacher-panel-card">
        <div className="admin-form-grid schedule-form-grid">
          <label className="field full-span">
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
            <span>Docente *</span>
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
          </label>

          <label className="field">
            <span>Horario base *</span>
            <select value={selectedScheduleId} onChange={handleScheduleChange} disabled={!schedules.length || isScheduleLoading}>
              {schedules.length ? (
                schedules.map((schedule) => (
                  <option key={schedule.horario_id} value={schedule.horario_id}>
                    {scheduleLabel(schedule)}
                  </option>
                ))
              ) : (
                <option value="">Sin horario guardado</option>
              )}
            </select>
          </label>

          <label className="field">
            <span>Visibilidad</span>
            <select name="visibility" value={form.visibility} onChange={handleChange}>
              <option value="Private">Private</option>
              <option value="Public">Public</option>
            </select>
          </label>
        </div>

        {noTeacher ? <p className="form-error">Primero matricula el docente correspondiente para esta cohorte.</p> : null}
        {noSchedule ? <p className="form-error">Primero crea el horario y sus sesiones antes de matricular por Teams.</p> : null}
        {teamsUnavailable ? <p className="form-error">{scheduleData?.teams?.message}</p> : null}
      </article>

      <article className="module-card teacher-panel-card">
        <form className="auth-form compact-form schedule-teams-form" onSubmit={handleSubmit}>
          <div className="admin-form-grid schedule-form-grid">
            <label className="field">
              <span>Team ID</span>
              <input
                name="team_id"
                type="text"
                value={form.team_id}
                onChange={handleChange}
                placeholder="GUID confirmado por Graph"
              />
            </label>

            <label className="field">
              <span>Group ID</span>
              <input name="group_id" type="text" value={form.group_id} onChange={handleChange} placeholder="Opcional" />
            </label>

            <label className="field">
              <span>URL del Team</span>
              <input
                name="web_url"
                type="url"
                value={form.web_url}
                onChange={handleChange}
                placeholder="https://teams.microsoft.com/..."
              />
            </label>
          </div>

          <section className="schedule-summary-grid schedule-teams-summary" aria-label="Resumen de matrícula por Teams">
            <div>
              <span>Horarios</span>
              <strong>{formatNumber(metrics.horarios)}</strong>
            </div>
            <div>
              <span>Sesiones</span>
              <strong>{formatNumber(metrics.sesiones)}</strong>
            </div>
            <div>
              <span>Team</span>
              <strong>{teamInfo?.estado_graph || 'Sin Team'}</strong>
            </div>
            <div>
              <span>Miembros</span>
              <strong>{formatNumber(teamMemberMetrics.total)}</strong>
            </div>
            <div>
              <span>Docentes</span>
              <strong>{formatNumber(teamMemberMetrics.docentes)}</strong>
            </div>
            <div>
              <span>Estudiantes</span>
              <strong>{formatNumber(teamMemberMetrics.estudiantes)}</strong>
            </div>
            <div>
              <span>En cola</span>
              <strong>{formatNumber(graphQueueMetrics.pendientes)}</strong>
            </div>
            <div>
              <span>Errores</span>
              <strong>{formatNumber(graphQueueMetrics.errores)}</strong>
            </div>
          </section>

          <div className="student-selection-actions">
            <button
              type="submit"
              className="submit-button compact-button"
              disabled={isSubmitting || isScheduleLoading || !canSubmit}
            >
              {isSubmitting ? 'Procesando...' : 'Matricular en Teams'}
            </button>
          </div>
        </form>
      </article>
    </section>
  )
}

function buildTeamsForm(scheduleData, selectedScheduleId = '') {
  const team = scheduleData?.team || scheduleData?.teams?.team || null
  const schedule = (scheduleData?.schedules || []).find(
    (item) => String(item.horario_id) === String(selectedScheduleId),
  )
  const teamsLink = team?.web_url || schedule?.enlace_virtual || ''
  return {
    visibility: team?.visibility || emptyTeamsForm.visibility,
    team_id: team?.team_id || '',
    group_id: team?.group_id || '',
    web_url: teamsLink,
  }
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

function resolveSelectedScheduleId(scheduleData, currentScheduleId = '') {
  const schedules = scheduleData?.schedules || []
  if (!schedules.length) {
    return ''
  }
  if (currentScheduleId && schedules.some((schedule) => String(schedule.horario_id) === String(currentScheduleId))) {
    return currentScheduleId
  }
  const withSessions = schedules.find((schedule) => Number(schedule.total_sesiones || 0) > 0)
  return String((withSessions || schedules[0]).horario_id || '')
}

function cutLabel(cut) {
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || cut.cod_curso || 'Sin materia'
  const period = cut.periodo || cut.codigo_periodo || cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const status = cut.estado_inscripcion || cut.estado_corte || ''
  return `${subject} - ${period}${status ? ` - ${status}` : ''}`
}

function scheduleLabel(schedule) {
  return `${schedule.dia_semana_label || 'Día'} ${schedule.hora_inicio || '-'} - ${schedule.hora_fin || '-'} · ${formatNumber(schedule.total_sesiones)} sesiones`
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}
