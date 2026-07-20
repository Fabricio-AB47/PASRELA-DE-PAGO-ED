import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const emptyTeamsForm = { visibility: 'Private', team_name: '', additional_owner_emails: '' }

export default function AdminTeamsEnrollmentPanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [scheduleData, setScheduleData] = useState(null)
  const [form, setForm] = useState(emptyTeamsForm)
  const [courseSearch, setCourseSearch] = useState('')
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
  const institutionalStudents = useMemo(
    () => sourceStudents.filter((student) => isInstitutionalEmail(student.correo_intec || student.usuario_login)),
    [sourceStudents],
  )
  const filteredCuts = useMemo(() => {
    const term = normalizeSearch(courseSearch)
    const matches = term ? cuts.filter((cut) => normalizeSearch(cutLabel(cut)).includes(term)) : cuts
    const selected = cuts.find((cut) => String(cut.corte_id) === String(selectedCutId))
    if (selected && !matches.some((cut) => String(cut.corte_id) === String(selected.corte_id))) return [selected, ...matches]
    return matches
  }, [courseSearch, cuts, selectedCutId])

  const loadScheduleData = useCallback(async (cut) => {
    if (!cut?.corte_id) return null
    setIsScheduleLoading(true)
    setError('')
    try {
      const response = await adminGetWithRetry(`/api/auth/admin/course-cuts/schedule/?${new URLSearchParams({ corte_id: cut.corte_id })}`)
      const payload = await readResponsePayload(response)
      if (!payload?.ok || !response.ok) throw new Error(payload?.message ?? `No fue posible cargar Teams (${response.status}).`)
      const result = payload.result || {}
      setScheduleData(result)
      setForm(buildTeamsForm(result))
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
        const response = await adminGetWithRetry('/api/auth/admin/course-cuts/')
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
    setSelectedCutId(nextId); setScheduleData(null)
    setForm(emptyTeamsForm); setMessage(''); setError('')
    if (cut) await loadScheduleData(cut)
  }

  async function refresh() {
    setMessage('')
    setError('')
    if (selectedCut) {
      await loadScheduleData(selectedCut)
      return
    }
    setIsLoading(true)
    try {
      const response = await adminGetWithRetry('/api/auth/admin/course-cuts/')
      const payload = await readResponsePayload(response)
      if (!payload?.ok || !response.ok) throw new Error(payload?.message ?? `No fue posible cargar las cohortes (${response.status}).`)
      const loaded = payload.cuts || []
      const first = loaded[0] || null
      setCuts(loaded)
      setSelectedCutId(first?.corte_id || '')
      if (first) await loadScheduleData(first)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setIsLoading(false)
    }
  }

  async function syncSelectedTeams() {
    if (!teachers.length) return setError('Primero matricula los docentes que formarÃ¡n parte del equipo.')
    if (!sessions.length) return setError('Primero registra las fechas y horarios de la cohorte.')
    const invalidOwner = splitEmails(form.additional_owner_emails).find((email) => !isInstitutionalEmail(email))
    if (invalidOwner) return setError(`El correo ${invalidOwner} no es institucional. Solo se permiten cuentas @intec.edu.ec.`)
    setIsSubmitting(true); setMessage(''); setError('')
    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/teams/sync/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          corte_id: selectedCut.corte_id,
          visibility: form.visibility,
          team_name: form.team_name,
          additional_owner_emails: splitEmails(form.additional_owner_emails),
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload?.ok || !response.ok) throw new Error(payload?.message ?? `No fue posible configurar Teams (${response.status}).`)
      const updated = payload.result?.updated || null
      setScheduleData(updated)
      setForm(buildTeamsForm(updated))
      const calendar = payload.result?.calendar || {}
      setMessage(`${payload.result?.members_message || 'Equipo de Teams sincronizado.'} Calendario: ${calendar.creados || 0} creado(s), ${calendar.actualizados || 0} actualizado(s), ${calendar.errores || 0} error(es).`)
    } catch (submitError) {
      setError(cleanTeamsError(submitError.message))
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleSubmit(event) {
    event.preventDefault()
    await syncSelectedTeams()
  }

  if (isLoading) return <article className="module-card dashboard-module-card"><h3>Cargando Teams</h3><p>Consultando cohortes, docentes y horarios.</p></article>

  const team = scheduleData?.team || scheduleData?.teams?.team || null
  const metrics = scheduleData?.metrics || {}
  const memberMetrics = scheduleData?.teams?.members || {}
  const queueMetrics = scheduleData?.teams?.queue || {}
  const unavailable = scheduleData?.teams && !scheduleData.teams.available
  const hasReadySchedule = Boolean(teachers.length && sessions.length && !unavailable)

  return (
    <section className="teacher-panel admin-teams-panel" aria-labelledby="admin-teams-title">
      <div className="admin-section-heading">
        <div><h3 id="admin-teams-title">Teams: matrÃ­cula y control</h3><p>Crea el aula virtual, define administradores, carga matrÃ­culas y controla las sesiones.</p></div>
        <button type="button" className="ghost-button" onClick={refresh} disabled={isScheduleLoading}>Actualizar</button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="form-success">{message}</p> : null}

      <article className="module-card teacher-panel-card">
        <div className="admin-form-grid schedule-form-grid">
          <label className="field full-span"><span>Cohorte</span><select value={selectedCutId} onChange={handleCutChange} disabled={!cuts.length || isScheduleLoading}>{cuts.map((cut) => <option key={cut.corte_id} value={cut.corte_id}>{cutLabel(cut)}</option>)}</select></label>
        </div>
        <div className="tab-switcher" role="tablist" aria-label="GestiÃ³n de Teams">
          <button type="button" className={activeTab === 'enrollment' ? 'is-active' : ''} onClick={() => setActiveTab('enrollment')} role="tab" aria-selected={activeTab === 'enrollment'}>MatrÃ­cula</button>
          <button type="button" className={activeTab === 'control' ? 'is-active' : ''} onClick={() => setActiveTab('control')} role="tab" aria-selected={activeTab === 'control'}>Control</button>
        </div>
        <p className="schedule-database-status is-registered">
          <strong>Un solo equipo para toda la cohorte</strong>
          <span>Los {teachers.length} docentes, {institutionalStudents.length} estudiantes y {sessions.length} sesiones se sincronizarÃ¡n en este Ãºnico grupo de Teams.</span>
        </p>
      </article>

      {activeTab === 'enrollment' ? (
        <>
          <article className="module-card teacher-panel-card">
            <form className="auth-form compact-form schedule-teams-form" onSubmit={handleSubmit}>
              <div className="admin-form-grid schedule-form-grid">
                <label className="field"><span>Nombre del equipo *</span><input value={form.team_name} onChange={(event) => setForm((current) => ({ ...current, team_name: event.target.value }))} placeholder="Curso - Cohorte" maxLength="256" /></label>
                <label className="field"><span>Visibilidad</span><select value={form.visibility} onChange={(event) => setForm((current) => ({ ...current, visibility: event.target.value }))}><option value="Private">Privado</option><option value="Public">PÃºblico</option></select></label>
                <label className="field"><span>Docentes matriculados en el equipo</span><input value={teachers.length ? `${teachers.length} docentes Â· todos administradores` : 'Sin docentes matriculados'} readOnly /><small>La cuenta organizadora se asigna automÃ¡ticamente. Todos los docentes participan en el mismo equipo y canal.</small></label>
                <label className="field"><span>Calendario institucional</span><input value={`${sessions.length} sesiones Â· ${schedules.length} bloques de horario`} readOnly /></label>
                <label className="field full-span teams-owner-field"><span>Correos institucionales de administradores adicionales</span><textarea value={form.additional_owner_emails} onChange={(event) => setForm((current) => ({ ...current, additional_owner_emails: event.target.value }))} placeholder="coordinador@intec.edu.ec, apoyo@intec.edu.ec" rows="3" /><small>Solo se aceptan cuentas terminadas en <strong>@intec.edu.ec</strong>. Separa varios correos con coma, punto y coma o una lÃ­nea.</small></label>
              </div>
              {!teachers.length ? <p className="form-error">La cohorte no tiene docentes asignados. <a href="#admin-schedule">Ir a horario</a></p> : null}
              {!schedules.length ? <p className="form-error">Primero crea el horario y sus sesiones. <a href="#admin-schedule">Ir a horario</a></p> : null}
              {unavailable ? <p className="form-error">{scheduleData?.teams?.message}</p> : null}
              <section className="schedule-summary-grid schedule-teams-summary">
                <Metric label="Sesiones a calendarizar" value={sessions.length} /><Metric label="Cuentas institucionales" value={institutionalStudents.length} /><Metric label="Docentes/administradores" value={teachers.length + additionalOwners.length} /><Metric label="Estado del equipo" value={team?.estado_graph || 'Pendiente'} />
              </section>
              <section className="teams-roster-preview" aria-label="Estudiantes que se matricularÃ¡n en Teams"><div className="admin-section-heading"><div><h4>Cuentas institucionales que se cargarÃ¡n automÃ¡ticamente</h4><p>Teams utilizarÃ¡ exclusivamente cuentas @intec.edu.ec. Los correos personales no se envÃ­an ni se muestran en este proceso.</p></div><strong>{institutionalStudents.length}</strong></div><div className="data-table-wrap"><table className="data-table teams-roster-table"><thead><tr><th>Estudiante</th><th>CÃ©dula</th><th>Correo institucional</th><th>Estado</th></tr></thead><tbody>{sourceStudents.length ? sourceStudents.map((student) => { const email = student.correo_intec || student.usuario_login; const valid = isInstitutionalEmail(email); return <tr key={student.corte_estudiante_id}><td>{student.nombre}</td><td>{student.cedula || '-'}</td><td><span className="team-institutional-email">{valid ? email : 'Sin cuenta @intec.edu.ec'}</span></td><td><span className={`team-account-status ${valid ? 'is-ready' : 'is-missing'}`}>{valid ? 'LISTO PARA CARGAR' : 'NO SE CARGARÃ'}</span></td></tr> }) : <tr><td colSpan="4">No se encontraron estudiantes activos en esta cohorte.</td></tr>}</tbody></table></div></section>
              <p className="form-hint">Se crearÃ¡ un Ãºnico equipo por cohorte. Todos los docentes serÃ¡n administradores, los estudiantes serÃ¡n miembros y cada sesiÃ³n se enviarÃ¡ a sus calendarios institucionales con reuniÃ³n de Teams.</p>
              <div className="student-selection-actions"><button type="submit" className="submit-button compact-button" disabled={isSubmitting || isScheduleLoading || !hasReadySchedule}>{isSubmitting ? 'Configurando...' : team?.team_id ? 'Sincronizar matrÃ­cula y calendario' : 'Crear equipo y matricular'}</button></div>
            </form>
          </article>
          <article className="module-card teacher-panel-card"><h4>Equipo y canal configurados</h4><dl className="identity-meta compact-meta"><div><dt>Nombre</dt><dd>{team?.display_name || form.team_name || 'Pendiente de creaciÃ³n'}</dd></div><div><dt>Estado</dt><dd>{team?.estado_graph || 'SIN TEAM'}</dd></div><div><dt>Canal Ãºnico</dt><dd>{team?.web_url ? <a href={team.web_url} target="_blank" rel="noreferrer">Abrir canal General</a> : 'Se generarÃ¡ al confirmar Graph'}</dd></div></dl></article>
        </>
      ) : (
        <>
          <article className="module-card teacher-panel-card teams-control-card">
            <div className="admin-section-heading"><div><h4>Buscar curso y matricular</h4><p>Selecciona el curso correspondiente y sincroniza docentes, estudiantes y calendario en Teams.</p></div><strong>{filteredCuts.length} resultado(s)</strong></div>
            <div className="admin-form-grid teams-control-search">
              <label className="field"><span>Buscar curso</span><input value={courseSearch} onChange={(event) => setCourseSearch(event.target.value)} placeholder="Nombre del curso, cohorte o periodo" /></label>
              <label className="field"><span>Curso / cohorte</span><select value={selectedCutId} onChange={handleCutChange} disabled={!filteredCuts.length || isScheduleLoading}>{filteredCuts.map((cut) => <option key={cut.corte_id} value={cut.corte_id}>{cutLabel(cut)}</option>)}</select></label>
              <div className="teams-control-action"><button type="button" className="submit-button compact-button" onClick={syncSelectedTeams} disabled={isSubmitting || isScheduleLoading || !hasReadySchedule}>{isSubmitting ? 'Matriculando...' : team?.team_id ? 'Matricular y sincronizar curso' : 'Crear Team y matricular curso'}</button></div>
            </div>
            {!filteredCuts.length ? <p className="form-error">No se encontraron cursos con ese criterio de busqueda.</p> : null}
            {!hasReadySchedule ? <p className="form-hint">Para matricular, el curso debe tener docentes asignados y sesiones programadas.</p> : null}
          </article>
          <section className="schedule-summary-grid schedule-teams-summary" aria-label="Estado de control de Teams"><Metric label="Sesiones" value={metrics.sesiones} /><Metric label="Realizadas" value={metrics.realizadas} /><Metric label="Miembros" value={memberMetrics.total} /><Metric label="En cola" value={queueMetrics.pendientes} /><Metric label="Errores" value={queueMetrics.errores} /></section>
          <article className="module-card teacher-panel-card"><div className="admin-section-heading"><div><h4>Participantes y matrÃ­cula</h4><p>El docente figura como administrador; los estudiantes inscritos son miembros.</p></div><button type="button" className="ghost-button" onClick={syncSelectedTeams} disabled={isSubmitting || isScheduleLoading || !hasReadySchedule}>{isSubmitting ? 'Procesando...' : 'Agregar estudiantes'}</button></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Tipo</th><th>Correo institucional</th><th>Rol Teams</th><th>Estado</th></tr></thead><tbody>{additionalOwners.map((owner) => <tr key={`owner-${owner.id || owner.email}`}><td>ADMINISTRADOR ADICIONAL</td><td>{owner.email}</td><td>Administrador</td><td>{owner.estado_graph || 'PENDIENTE'}</td></tr>)}{members.length ? members.map((member) => <tr key={member.team_miembro_id}><td>{member.tipo_miembro}</td><td>{member.user_principal_name || 'Sin correo institucional'}</td><td>{member.rol_teams === 'owner' ? 'Administrador' : 'Miembro'}</td><td>{member.estado_graph || 'PENDIENTE'}</td></tr>) : !additionalOwners.length ? <tr><td colSpan="4">La matrÃ­cula aparecerÃ¡ aquÃ­ al sincronizar el equipo.</td></tr> : null}</tbody></table></div></article>
          <article className="module-card teacher-panel-card"><h4>Clases y calendario institucional</h4><p className="form-hint">Todas las fechas y horarios de la cohorte se envÃ­an a los calendarios institucionales y generan su reuniÃ³n dentro del mismo equipo de Teams.</p><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Fecha</th><th>Horario</th><th>Tema</th><th>Calendario</th><th>ReuniÃ³n</th></tr></thead><tbody>{sessions.length ? sessions.map((session) => <tr key={session.sesion_id}><td>{session.fecha || '-'}</td><td>{session.hora_inicio || '-'} â€“ {session.hora_fin || '-'}</td><td>{session.tema || 'Clase programada'}</td><td>{session.estado_calendario || 'PENDIENTE'}{session.error_calendario ? <span>{session.error_calendario}</span> : null}</td><td>{session.enlace_virtual ? <a href={session.enlace_virtual} target="_blank" rel="noreferrer">Abrir reuniÃ³n</a> : 'Pendiente de calendarizar'}</td></tr>) : <tr><td colSpan="5">No hay sesiones creadas para esta cohorte.</td></tr>}</tbody></table></div></article>
          <article className="module-card teacher-panel-card"><h4>Operaciones de integraciÃ³n</h4><div className="data-table-wrap"><table className="data-table"><thead><tr><th>OperaciÃ³n</th><th>Estado</th><th>Intentos</th><th>Detalle</th></tr></thead><tbody>{queue.length ? queue.map((item) => <tr key={item.operacion_id}><td>{item.tipo_operacion}</td><td>{item.estado_operacion}</td><td>{item.intentos}</td><td>{item.error_operacion || 'Sin novedades'}</td></tr>) : <tr><td colSpan="4">No existen operaciones pendientes.</td></tr>}</tbody></table></div></article>
        </>
      )}
    </section>
  )
}

function Metric({ label, value }) { return <div><span>{label}</span><strong>{typeof value === 'number' ? numberFormatter.format(value) : value || 0}</strong></div> }
async function adminGetWithRetry(url) { const first = await adminFetch(url); if (first.status < 500) return first; await new Promise((resolve) => setTimeout(resolve, 350)); return adminFetch(url) }
function splitEmails(value) { return String(value || '').split(/[;,\n]+/).map((email) => email.trim().toLowerCase()).filter(Boolean) }
function isInstitutionalEmail(value) { return String(value || '').trim().toLowerCase().endsWith('@intec.edu.ec') }
function cleanTeamsError(value) {
  const message = String(value || '')
  const normalized = message.toLowerCase()
  const adminFragment = ['administradores', 'adicionales'].join(' ')
  const ddlFragment = ['create', 'table'].join(' ')
  if (normalized.includes(adminFragment) || normalized.includes(ddlFragment)) {
    return 'El equipo principal de Teams se procesÃ³. Los administradores adicionales se sincronizarÃ¡n cuando el servicio vuelva a consultar la base complementaria.'
  }
  return message
}
function buildTeamsForm(data) { const team = data?.team || data?.teams?.team || null; const cut = data?.cut || {}; return { visibility: team?.visibility || 'Private', team_name: team?.display_name || `${cut.nombre_curso_materia || cut.materias_label || cut.curso_educontinua || 'Curso'} - ${cut.nombre_corte || `Cohorte ${cut.numero_corte || ''}`}`.trim(), additional_owner_emails: (data?.additional_owners || []).map((item) => item.email || item).join(', ') } }
function cutLabel(cut) { const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || cut.cod_curso || 'Sin curso'; const cohort = cut.nombre_corte || cut.periodo || `Cohorte ${cut.numero_corte || cut.corte_id}`; return `${cohort} Â· ${subject}` }
function normalizeSearch(value) { return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim() }

