import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const emptyTeamsForm = { visibility: 'Private', team_name: '', additional_owner_emails: '' }

export default function AdminTeamsEnrollmentPanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [selectedTeacherId, setSelectedTeacherId] = useState('')
  const [selectedScheduleId, setSelectedScheduleId] = useState('')
  const [scheduleData, setScheduleData] = useState(null)
  const [form, setForm] = useState(emptyTeamsForm)
  const [activeTab, setActiveTab] = useState('enrollment')
  const [isLoading, setIsLoading] = useState(true)
  const [isScheduleLoading, setIsScheduleLoading] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const selectedCut = useMemo(
    () => cuts.find((cut) => String(cut.corte_id) === String(selectedCutId)) || null,
    [cuts, selectedCutId],
  )
  const teachers = useMemo(() => scheduleData?.teachers || [], [scheduleData])
  const schedules = useMemo(() => scheduleData?.schedules || [], [scheduleData])
  const sessions = useMemo(() => scheduleData?.sessions || [], [scheduleData])
  const members = useMemo(() => scheduleData?.team_members || [], [scheduleData])
  const queue = useMemo(() => scheduleData?.graph_queue || [], [scheduleData])
  const additionalOwners = useMemo(() => scheduleData?.additional_owners || [], [scheduleData])
  const sourceStudents = useMemo(() => scheduleData?.source_students || [], [scheduleData])
  const selectedTeacher = useMemo(
    () => teachers.find((teacher) => String(teacher.docente_corte_id) === String(selectedTeacherId)) || null,
    [teachers, selectedTeacherId],
  )
  const selectedSchedule = useMemo(
    () => schedules.find((schedule) => String(schedule.horario_id) === String(selectedScheduleId)) || null,
    [schedules, selectedScheduleId],
  )

  const loadScheduleData = useCallback(async (cut, preferredScheduleId = '') => {
    if (!cut?.corte_id) return null
    setIsScheduleLoading(true)
    setError('')
    try {
      const response = await adminFetch(`/api/auth/admin/course-cuts/schedule/?${new URLSearchParams({ corte_id: cut.corte_id })}`)
      const payload = await readResponsePayload(response)
      if (!payload?.ok || !response.ok) throw new Error(payload?.message ?? `No fue posible cargar Teams (${response.status}).`)
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
    let mounted = true
    async function loadCuts() {
      setIsLoading(true)
      try {
        const response = await adminFetch('/api/auth/admin/course-cuts/')
        const payload = await readResponsePayload(response)
        if (!payload?.ok || !response.ok) throw new Error(payload?.message ?? 'No fue posible cargar las cohortes.')
        if (!mounted) return
        const loaded = payload.cuts || []
        const first = loaded[0] || null
        setCuts(loaded)
        setSelectedCutId(first?.corte_id || '')
        if (first) await loadScheduleData(first)
      } catch (loadError) {
        if (mounted) setError(loadError.message)
      } finally {
        if (mounted) setIsLoading(false)
      }
    }
    loadCuts()
    return () => { mounted = false }
  }, [loadScheduleData])

  async function handleCutChange(event) {
    const nextId = event.target.value
    const cut = cuts.find((item) => String(item.corte_id) === String(nextId)) || null
    setSelectedCutId(nextId); setSelectedTeacherId(''); setSelectedScheduleId(''); setScheduleData(null)
    setForm(emptyTeamsForm); setMessage(''); setError('')
    if (cut) await loadScheduleData(cut)
  }

  async function refresh() {
    if (selectedCut) await loadScheduleData(selectedCut, selectedScheduleId)
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (!selectedTeacherId) return setError('Selecciona un docente ya matriculado en esta cohorte.')
    if (!selectedSchedule || Number(selectedSchedule.total_sesiones || 0) <= 0) return setError('Selecciona un horario que ya tenga sesiones programadas.')
    setIsSubmitting(true); setMessage(''); setError('')
    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/teams/sync/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          corte_id: selectedCut.corte_id,
          horario_id: selectedScheduleId,
          docente_corte_id: selectedTeacherId,
          codigo_docente: selectedTeacher?.codigo_docente || '',
          visibility: form.visibility,
          team_name: form.team_name,
          additional_owner_emails: splitEmails(form.additional_owner_emails),
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload?.ok || !response.ok) throw new Error(payload?.message ?? `No fue posible configurar Teams (${response.status}).`)
      const updated = payload.result?.updated || null
      setScheduleData(updated)
      setSelectedTeacherId((current) => resolveSelectedTeacherId(updated, current))
      setSelectedScheduleId((current) => resolveSelectedScheduleId(updated, current))
      setForm(buildTeamsForm(updated, selectedScheduleId))
      setMessage(payload.result?.members_message || payload.message || 'Equipo y matrícula de Teams procesados.')
    } catch (submitError) {
      setError(submitError.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  if (isLoading) return <article className="module-card dashboard-module-card"><h3>Cargando Teams</h3><p>Consultando cohortes, docentes y horarios.</p></article>

  const team = scheduleData?.team || scheduleData?.teams?.team || null
  const metrics = scheduleData?.metrics || {}
  const memberMetrics = scheduleData?.teams?.members || {}
  const queueMetrics = scheduleData?.teams?.queue || {}
  const unavailable = scheduleData?.teams && !scheduleData.teams.available
  const hasReadySchedule = Boolean(selectedTeacherId && selectedSchedule && Number(selectedSchedule.total_sesiones || 0) > 0 && !unavailable)

  return (
    <section className="teacher-panel admin-teams-panel" aria-labelledby="admin-teams-title">
      <div className="admin-section-heading">
        <div><h3 id="admin-teams-title">Teams: matrícula y control</h3><p>Crea el aula virtual, define administradores, carga matrículas y controla las sesiones.</p></div>
        <button type="button" className="ghost-button" onClick={refresh} disabled={isScheduleLoading}>Actualizar</button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="form-success">{message}</p> : null}

      <article className="module-card teacher-panel-card">
        <div className="admin-form-grid schedule-form-grid">
          <label className="field full-span"><span>Cohorte</span><select value={selectedCutId} onChange={handleCutChange} disabled={!cuts.length || isScheduleLoading}>{cuts.map((cut) => <option key={cut.corte_id} value={cut.corte_id}>{cutLabel(cut)}</option>)}</select></label>
        </div>
        <div className="tab-switcher" role="tablist" aria-label="Gestión de Teams">
          <button type="button" className={activeTab === 'enrollment' ? 'is-active' : ''} onClick={() => setActiveTab('enrollment')} role="tab" aria-selected={activeTab === 'enrollment'}>Matrícula</button>
          <button type="button" className={activeTab === 'control' ? 'is-active' : ''} onClick={() => setActiveTab('control')} role="tab" aria-selected={activeTab === 'control'}>Control</button>
        </div>
      </article>

      {activeTab === 'enrollment' ? (
        <>
          <article className="module-card teacher-panel-card">
            <form className="auth-form compact-form schedule-teams-form" onSubmit={handleSubmit}>
              <div className="admin-form-grid schedule-form-grid">
                <label className="field"><span>Nombre del equipo *</span><input value={form.team_name} onChange={(event) => setForm((current) => ({ ...current, team_name: event.target.value }))} placeholder="Curso - Cohorte" maxLength="256" /></label>
                <label className="field"><span>Visibilidad</span><select value={form.visibility} onChange={(event) => setForm((current) => ({ ...current, visibility: event.target.value }))}><option value="Private">Privado</option><option value="Public">Público</option></select></label>
                <label className="field"><span>Docente administrador *</span><select value={selectedTeacherId} onChange={(event) => setSelectedTeacherId(event.target.value)} disabled={!teachers.length || isScheduleLoading}>{teachers.length ? teachers.map((teacher) => <option key={teacher.docente_corte_id} value={teacher.docente_corte_id}>{teacher.nombre} · {teacher.rol_docente || 'DOCENTE'}</option>) : <option value="">Sin docente matriculado</option>}</select></label>
                <label className="field"><span>Horario a calendarizar *</span><select value={selectedScheduleId} onChange={(event) => setSelectedScheduleId(event.target.value)} disabled={!schedules.length || isScheduleLoading}>{schedules.length ? schedules.map((schedule) => <option key={schedule.horario_id} value={schedule.horario_id}>{scheduleLabel(schedule)}</option>) : <option value="">Sin horario guardado</option>}</select></label>
                <label className="field full-span"><span>Correos administradores adicionales</span><textarea value={form.additional_owner_emails} onChange={(event) => setForm((current) => ({ ...current, additional_owner_emails: event.target.value }))} placeholder="coordinador@intec.edu.ec, apoyo@intec.edu.ec" rows="2" /><small>Separa uno o varios correos con coma, punto y coma o una línea. El docente seleccionado siempre se agrega como administrador.</small></label>
              </div>
              {!teachers.length ? <p className="form-error">Primero matrícula al docente en la cohorte. <a href="#teacher-enrollment">Ir a matrícula docente</a></p> : null}
              {!schedules.length ? <p className="form-error">Primero crea el horario y sus sesiones. <a href="#admin-schedule">Ir a horario</a></p> : null}
              {unavailable ? <p className="form-error">{scheduleData?.teams?.message}</p> : null}
              <section className="schedule-summary-grid schedule-teams-summary">
                <Metric label="Sesiones a calendarizar" value={metrics.sesiones} /><Metric label="Estudiantes del curso" value={sourceStudents.length} /><Metric label="Docentes/administradores" value={teachers.length + additionalOwners.length} /><Metric label="Estado del equipo" value={team?.estado_graph || 'Pendiente'} />
              </section>
              <section className="teams-roster-preview" aria-label="Estudiantes que se matricularán en Teams"><div className="admin-section-heading"><div><h4>Estudiantes que se cargarán automáticamente</h4><p>Se toman de los estudiantes activos de la cohorte seleccionada. Al crear el equipo se sincronizan con INTECEDUCONTINUA y se encolan como miembros.</p></div><strong>{sourceStudents.length}</strong></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Estudiante</th><th>Cédula</th><th>Correo</th><th>Estado</th></tr></thead><tbody>{sourceStudents.length ? sourceStudents.map((student) => <tr key={student.corte_estudiante_id}><td>{student.nombre}</td><td>{student.cedula || '-'}</td><td>{student.correo_personal || 'Sin correo personal'}</td><td>{student.continuing_education?.synced ? 'SINCRONIZADO' : 'LISTO PARA CARGAR'}</td></tr>) : <tr><td colSpan="4">No se encontraron estudiantes activos en esta cohorte.</td></tr>}</tbody></table></div></section>
              <p className="form-hint">Al crear el equipo, el docente queda como administrador y los estudiantes inscritos se cargan como miembros. Las sesiones del horario son la fuente de la calendarización.</p>
              <div className="student-selection-actions"><button type="submit" className="submit-button compact-button" disabled={isSubmitting || isScheduleLoading || !hasReadySchedule}>{isSubmitting ? 'Configurando...' : team?.team_id ? 'Sincronizar matrícula y calendario' : 'Crear equipo y matricular'}</button></div>
            </form>
          </article>
          <article className="module-card teacher-panel-card"><h4>Equipo configurado</h4><dl className="identity-meta compact-meta"><div><dt>Nombre</dt><dd>{team?.display_name || form.team_name || 'Pendiente de creación'}</dd></div><div><dt>Estado</dt><dd>{team?.estado_graph || 'SIN TEAM'}</dd></div><div><dt>Enlace</dt><dd>{team?.web_url ? <a href={team.web_url} target="_blank" rel="noreferrer">Abrir Teams</a> : 'Se generará al confirmar Graph'}</dd></div></dl></article>
        </>
      ) : (
        <>
          <section className="schedule-summary-grid schedule-teams-summary" aria-label="Estado de control de Teams"><Metric label="Sesiones" value={metrics.sesiones} /><Metric label="Realizadas" value={metrics.realizadas} /><Metric label="Miembros" value={memberMetrics.total} /><Metric label="En cola" value={queueMetrics.pendientes} /><Metric label="Errores" value={queueMetrics.errores} /></section>
          <article className="module-card teacher-panel-card"><div className="admin-section-heading"><div><h4>Participantes y matrícula</h4><p>El docente figura como administrador; los estudiantes inscritos son miembros.</p></div><a className="ghost-button" href="#course-students">Agregar estudiantes</a></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Tipo</th><th>Correo institucional</th><th>Rol Teams</th><th>Estado</th></tr></thead><tbody>{additionalOwners.map((owner) => <tr key={`owner-${owner.id || owner.email}`}><td>ADMINISTRADOR ADICIONAL</td><td>{owner.email}</td><td>Administrador</td><td>{owner.estado_graph || 'PENDIENTE'}</td></tr>)}{members.length ? members.map((member) => <tr key={member.team_miembro_id}><td>{member.tipo_miembro}</td><td>{member.user_principal_name || 'Sin correo institucional'}</td><td>{member.rol_teams === 'owner' ? 'Administrador' : 'Miembro'}</td><td>{member.estado_graph || 'PENDIENTE'}</td></tr>) : !additionalOwners.length ? <tr><td colSpan="4">La matrícula aparecerá aquí al sincronizar el equipo.</td></tr> : null}</tbody></table></div></article>
          <article className="module-card teacher-panel-card"><h4>Clases, chat y grabaciones</h4><p className="form-hint">Cada sesión del horario se muestra aquí. Al confirmarse la integración de Microsoft Graph, el enlace de reunión, chat, participantes y grabaciones se consultan desde el equipo.</p><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Fecha</th><th>Horario</th><th>Tema</th><th>Estado</th><th>Reunión</th></tr></thead><tbody>{sessions.length ? sessions.map((session) => <tr key={session.sesion_id}><td>{session.fecha || '-'}</td><td>{session.hora_inicio || '-'} – {session.hora_fin || '-'}</td><td>{session.tema || 'Clase programada'}</td><td>{session.estado || 'PROGRAMADA'}</td><td>{session.enlace_virtual ? <a href={session.enlace_virtual} target="_blank" rel="noreferrer">Abrir reunión</a> : 'Pendiente de calendarizar'}</td></tr>) : <tr><td colSpan="5">No hay sesiones creadas para esta cohorte.</td></tr>}</tbody></table></div></article>
          <article className="module-card teacher-panel-card"><h4>Operaciones de integración</h4><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Operación</th><th>Estado</th><th>Intentos</th><th>Detalle</th></tr></thead><tbody>{queue.length ? queue.map((item) => <tr key={item.operacion_id}><td>{item.tipo_operacion}</td><td>{item.estado_operacion}</td><td>{item.intentos}</td><td>{item.error_operacion || 'Sin novedades'}</td></tr>) : <tr><td colSpan="4">No existen operaciones pendientes.</td></tr>}</tbody></table></div></article>
        </>
      )}
    </section>
  )
}

function Metric({ label, value }) { return <div><span>{label}</span><strong>{typeof value === 'number' ? numberFormatter.format(value) : value || 0}</strong></div> }
function splitEmails(value) { return String(value || '').split(/[;,\n]+/).map((email) => email.trim().toLowerCase()).filter(Boolean) }
function buildTeamsForm(data) { const team = data?.team || data?.teams?.team || null; const cut = data?.cut || {}; return { visibility: team?.visibility || 'Private', team_name: team?.display_name || `${cut.nombre_curso_materia || cut.materias_label || cut.curso_educontinua || 'Curso'} - ${cut.nombre_corte || `Cohorte ${cut.numero_corte || ''}`}`.trim(), additional_owner_emails: (data?.additional_owners || []).map((item) => item.email || item).join(', ') } }
function resolveSelectedTeacherId(data, current = '') { const teachers = data?.teachers || []; if (!teachers.length) return ''; if (current && teachers.some((teacher) => String(teacher.docente_corte_id) === String(current))) return current; return String((teachers.find((teacher) => String(teacher.rol_docente || '').toUpperCase() === 'TITULAR') || teachers[0]).docente_corte_id || '') }
function resolveSelectedScheduleId(data, current = '') { const schedules = data?.schedules || []; if (!schedules.length) return ''; if (current && schedules.some((schedule) => String(schedule.horario_id) === String(current))) return current; return String((schedules.find((schedule) => Number(schedule.total_sesiones || 0) > 0) || schedules[0]).horario_id || '') }
function cutLabel(cut) { const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || cut.cod_curso || 'Sin curso'; const cohort = cut.nombre_corte || cut.periodo || `Cohorte ${cut.numero_corte || cut.corte_id}`; return `${cohort} · ${subject}` }
function scheduleLabel(schedule) { return `${schedule.dia_semana_label || 'Día'} ${schedule.hora_inicio || '-'} - ${schedule.hora_fin || '-'} · ${numberFormatter.format(Number(schedule.total_sesiones || 0))} sesiones` }
