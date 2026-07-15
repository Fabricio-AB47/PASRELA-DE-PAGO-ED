import { useEffect, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const DEFAULT_START_DATE = '2026-07-20'
const FIXED_COD_ANIO_BASICA = '13'
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

const emptyForm = {
  tipo_oferta: 'CARRERA',
  cod_anio_basica: '',
  codigo_materias: [],
  codigo_periodo: '',
  numero_corte: '',
  nombre_corte: '',
  fecha_inicio: DEFAULT_START_DATE,
  fecha_fin: '',
  cupo_esperado: '',
  horas: '',
  observacion: '',
}

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
  visibility: 'Private',
  team_id: '',
  group_id: '',
  web_url: '',
}

const removeNumbersFromLabel = (value) =>
  String(value || '')
    .replace(/[0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

const normalizeHours = (value) => {
  const text = String(value ?? '').trim()
  if (!text) {
    return ''
  }
  const numeric = Number.parseInt(text, 10)
  return Number.isFinite(numeric) ? String(numeric) : ''
}

function resolveSelectedHours(courses, subjectCodes) {
  const selectedCodes = new Set((subjectCodes || []).map((item) => String(item)))
  const selectedCourses = (courses || []).filter((course) => selectedCodes.has(String(course.codigo_materia)))
  const selectedHours = [...new Set(selectedCourses.map((course) => normalizeHours(course.horas)).filter(Boolean))]

  return selectedHours.length === 1 ? selectedHours[0] : ''
}

function cutTargetLabel(cut) {
  if (cut.tipo_oferta === 'EDUCONTINUA') {
    return cut.curso_educontinua || `Curso ${cut.cod_curso || '-'}`
  }

  const career = cut.carrera || `Carrera ${cut.cod_anio_basica || '-'}`
  const period = cut.periodo || `Período ${cut.codigo_periodo || '-'}`
  return `${removeNumbersFromLabel(career)} - ${removeNumbersFromLabel(period)}`
}

function cutSubjectLabel(cut) {
  if (cut.materias_label) {
    return removeNumbersFromLabel(cut.materias_label)
  }
  if (cut.codigo_materias?.length) {
    return cut.codigo_materias.join(', ')
  }
  return 'Todas las materias'
}

function cutStatusClass(cut) {
  if (cut.ingresos_disponibles) {
    return 'is-open'
  }
  if (cut.estado_corte === 'ABIERTO') {
    return 'is-unavailable'
  }
  return 'is-closed'
}

function buildScheduleForm(cut, scheduleData) {
  const firstSchedule = scheduleData?.schedules?.[0] || null
  const team = scheduleData?.team || scheduleData?.teams?.team || null
  const teamsLink = team?.web_url || firstSchedule?.enlace_virtual || ''

  return {
    ...emptyScheduleForm,
    horario_id: firstSchedule?.horario_id || '',
    dia_semana: firstSchedule?.dia_semana ? String(firstSchedule.dia_semana) : emptyScheduleForm.dia_semana,
    hora_inicio: firstSchedule?.hora_inicio || emptyScheduleForm.hora_inicio,
    hora_fin: firstSchedule?.hora_fin || emptyScheduleForm.hora_fin,
    modalidad: firstSchedule?.modalidad || emptyScheduleForm.modalidad,
    aula: firstSchedule?.aula || '',
    enlace_virtual: firstSchedule?.enlace_virtual || teamsLink,
    fecha_desde: cut?.fecha_inicio_iso || '',
    fecha_hasta: cut?.fecha_fin_iso || '',
    visibility: team?.visibility || emptyScheduleForm.visibility,
    team_id: team?.team_id || '',
    group_id: team?.group_id || '',
    web_url: teamsLink,
  }
}

function scheduleFormFromRow(current, schedule) {
  return {
    ...current,
    horario_id: schedule?.horario_id || '',
    dia_semana: schedule?.dia_semana ? String(schedule.dia_semana) : current.dia_semana,
    hora_inicio: schedule?.hora_inicio || current.hora_inicio,
    hora_fin: schedule?.hora_fin || current.hora_fin,
    modalidad: schedule?.modalidad || current.modalidad,
    aula: schedule?.aula || '',
    enlace_virtual: schedule?.enlace_virtual || current.enlace_virtual,
  }
}

function selectedCutLabel(cut) {
  if (!cut) {
    return ''
  }
  return `${cutSubjectLabel(cut)} - ${cut.nombre_corte || `Corte ${cut.numero_corte || cut.corte_id}`}`
}

export default function AdminCourseCutsPanel() {
  const [catalogs, setCatalogs] = useState({
    carreras: [],
    periodos: [],
    cursos_por_carrera: {},
  })
  const [cuts, setCuts] = useState([])
  const [form, setForm] = useState(emptyForm)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [closingCutId, setClosingCutId] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [scheduleModal, setScheduleModal] = useState({ isOpen: false, cut: null })
  const [scheduleData, setScheduleData] = useState(null)
  const [scheduleForm, setScheduleForm] = useState(emptyScheduleForm)
  const [isScheduleLoading, setIsScheduleLoading] = useState(false)
  const [isScheduleSaving, setIsScheduleSaving] = useState(false)
  const [isTeamsSaving, setIsTeamsSaving] = useState(false)
  const [scheduleMessage, setScheduleMessage] = useState('')
  const [scheduleError, setScheduleError] = useState('')

  useEffect(() => {
    let isMounted = true

    async function loadInitialData() {
      try {
        const [catalogResponse, cutResponse] = await Promise.all([
          adminFetch('/api/auth/inscription/catalogs/'),
          adminFetch('/api/auth/admin/course-cuts/'),
        ])
        const [catalogPayload, cutPayload] = await Promise.all([
          readResponsePayload(catalogResponse),
          readResponsePayload(cutResponse),
        ])

        if (!catalogPayload || !catalogResponse.ok || !catalogPayload.ok || !catalogPayload.catalogs) {
          throw new Error(catalogPayload?.message ?? `No fue posible cargar catálogos (${catalogResponse.status}).`)
        }
        if (!cutPayload || !cutResponse.ok || !cutPayload.ok) {
          throw new Error(cutPayload?.message ?? `No fue posible cargar cortes (${cutResponse.status}).`)
        }

        if (!isMounted) {
          return
        }

        const loadedCatalogs = catalogPayload.catalogs
        const fixedCareer = (loadedCatalogs.carreras || []).find(
          (item) => String(item.cod_anio_basica) === FIXED_COD_ANIO_BASICA,
        )
        const activeCareer = fixedCareer || (loadedCatalogs.carreras || []).find((item) => item.es_activo) || null
        const activePeriod = (loadedCatalogs.periodos || []).find((item) => item.es_activo) || null
        const activeCourses = activeCareer
          ? loadedCatalogs.cursos_por_carrera?.[String(activeCareer.cod_anio_basica)] || []
          : []
        const defaultCourse = activeCourses[0] || null

        setCatalogs({
          carreras: loadedCatalogs.carreras || [],
          periodos: loadedCatalogs.periodos || [],
          cursos_por_carrera: loadedCatalogs.cursos_por_carrera || {},
        })
        setCuts(cutPayload.cuts || [])
        setForm((current) => ({
          ...current,
          cod_anio_basica: activeCareer?.cod_anio_basica ? String(activeCareer.cod_anio_basica) : '',
          codigo_materias: defaultCourse?.codigo_materia ? [String(defaultCourse.codigo_materia)] : [],
          codigo_periodo: activePeriod?.cod_periodo ? String(activePeriod.cod_periodo) : '',
          horas: normalizeHours(defaultCourse?.horas),
        }))
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

    loadInitialData()

    return () => {
      isMounted = false
    }
  }, [])

  function handleChange(event) {
    const { name, value } = event.target
    if (name === 'cod_anio_basica') {
      const nextCourses = catalogs.cursos_por_carrera?.[String(value)] || []
      const defaultCourse = nextCourses[0] || null
      const nextSubjectCodes = defaultCourse?.codigo_materia ? [String(defaultCourse.codigo_materia)] : []
      setForm((current) => ({
        ...current,
        cod_anio_basica: value,
        codigo_materias: nextSubjectCodes,
        horas: resolveSelectedHours(nextCourses, nextSubjectCodes),
      }))
      return
    }

    setForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  function handleSubjectToggle(subjectCode) {
    setForm((current) => {
      const code = String(subjectCode)
      const selected = current.codigo_materias || []
      const exists = selected.includes(code)
      const nextSelected = exists
        ? selected.filter((item) => item !== code)
        : [...selected, code]
      const careerCourses = catalogs.cursos_por_carrera?.[String(current.cod_anio_basica)] || []
      return {
        ...current,
        codigo_materias: nextSelected,
        horas: resolveSelectedHours(careerCourses, nextSelected),
      }
    })
  }

  async function reloadCuts() {
    const response = await adminFetch('/api/auth/admin/course-cuts/')
    const payload = await readResponsePayload(response)
    if (!payload || !response.ok || !payload.ok) {
      throw new Error(payload?.message ?? `No fue posible cargar cortes (${response.status}).`)
    }
    setCuts(payload.cuts || [])
  }

  async function handleReloadCuts() {
    setMessage('')
    setError('')

    try {
      await reloadCuts()
    } catch (reloadError) {
      setError(reloadError.message)
    }
  }

  async function handleCreate(event) {
    event.preventDefault()
    setIsSaving(true)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/create/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(form),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible crear la corte (${response.status}).`)
      }

      await reloadCuts()
      setMessage(payload.message || 'Corte creada.')
      setForm((current) => ({
        ...emptyForm,
        cod_anio_basica: current.cod_anio_basica,
        codigo_materias: current.codigo_materias,
        codigo_periodo: current.codigo_periodo,
        horas: current.horas,
      }))
    } catch (createError) {
      setError(createError.message)
    } finally {
      setIsSaving(false)
    }
  }

  async function handleClose(cut) {
    if (!cut?.corte_id) {
      return
    }

    setClosingCutId(cut.corte_id)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/close/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ corte_id: cut.corte_id }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible cerrar la corte (${response.status}).`)
      }

      await reloadCuts()
      setMessage(payload.message || 'Corte cerrada.')
    } catch (closeError) {
      setError(closeError.message)
    } finally {
      setClosingCutId('')
    }
  }

  async function loadScheduleData(cut, { resetForm = true } = {}) {
    if (!cut?.corte_id) {
      return null
    }

    setIsScheduleLoading(true)
    setScheduleError('')

    try {
      const params = new URLSearchParams({ corte_id: cut.corte_id })
      const response = await adminFetch(`/api/auth/admin/course-cuts/schedule/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible cargar horario y Teams (${response.status}).`)
      }
      const result = payload.result || {}
      setScheduleData(result)
      if (resetForm) {
        setScheduleForm(buildScheduleForm(cut, result))
      }
      return result
    } catch (loadError) {
      setScheduleError(loadError.message)
      return null
    } finally {
      setIsScheduleLoading(false)
    }
  }

  async function openScheduleModal(cut) {
    setScheduleModal({ isOpen: true, cut })
    setScheduleData(null)
    setScheduleForm(buildScheduleForm(cut, null))
    setScheduleMessage('')
    setScheduleError('')
    await loadScheduleData(cut)
  }

  function closeScheduleModal() {
    setScheduleModal({ isOpen: false, cut: null })
    setScheduleData(null)
    setScheduleForm(emptyScheduleForm)
    setScheduleMessage('')
    setScheduleError('')
  }

  function handleScheduleChange(event) {
    const { name, value, checked, type } = event.target
    setScheduleForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  function handleEditSchedule(schedule) {
    setScheduleForm((current) => scheduleFormFromRow(current, schedule))
    setScheduleMessage(`Editando horario ${schedule?.dia_semana_label || ''} ${schedule?.hora_inicio || ''}.`)
    setScheduleError('')
  }

  async function handleScheduleSubmit(event) {
    event.preventDefault()
    const cut = scheduleModal.cut
    if (!cut?.corte_id) {
      return
    }

    setIsScheduleSaving(true)
    setScheduleMessage('')
    setScheduleError('')

    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/schedule/save/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...scheduleForm,
          corte_id: cut.corte_id,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible guardar horario (${response.status}).`)
      }
      const updated = payload.result?.updated || null
      setScheduleData(updated)
      setScheduleForm(buildScheduleForm(cut, updated))
      setScheduleMessage(payload.message || 'Horario guardado.')
      await reloadCuts()
    } catch (saveError) {
      setScheduleError(saveError.message)
    } finally {
      setIsScheduleSaving(false)
    }
  }

  async function handleTeamsSubmit(event) {
    event.preventDefault()
    const cut = scheduleModal.cut
    if (!cut?.corte_id) {
      return
    }

    setIsTeamsSaving(true)
    setScheduleMessage('')
    setScheduleError('')

    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/teams/sync/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          corte_id: cut.corte_id,
          visibility: scheduleForm.visibility,
          team_id: scheduleForm.team_id,
          group_id: scheduleForm.group_id,
          web_url: scheduleForm.web_url || scheduleForm.enlace_virtual,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible matricular por Teams (${response.status}).`)
      }
      const updated = payload.result?.updated || null
      setScheduleData(updated)
      setScheduleForm(buildScheduleForm(cut, updated))
      setScheduleMessage(payload.result?.members_message || payload.message || 'Proceso de Teams encolado.')
    } catch (teamsError) {
      setScheduleError(teamsError.message)
    } finally {
      setIsTeamsSaving(false)
    }
  }

  const activeCareers = catalogs.carreras.filter((career) => career.es_activo !== false)
  const activePeriods = catalogs.periodos.filter((period) => period.es_activo !== false)
  const selectedCareerCourses = catalogs.cursos_por_carrera?.[String(form.cod_anio_basica)] || []
  const selectedSubjectCodes = (form.codigo_materias || []).map((item) => String(item))
  const selectedOpenCut = cuts.find(
    (cut) =>
      cut.estado_corte === 'ABIERTO' &&
      cut.tipo_oferta === 'CARRERA' &&
      String(cut.cod_anio_basica) === String(form.cod_anio_basica) &&
      String(cut.codigo_periodo) === String(form.codigo_periodo) &&
      (
        !selectedSubjectCodes.length ||
        !cut.codigo_materias?.length ||
        cut.codigo_materias.some((subjectCode) => selectedSubjectCodes.includes(String(subjectCode)))
      ),
  )
  const selectedOpenCutStatus = selectedOpenCut
    ? [
        selectedOpenCut.ingresos_disponibles ? 'Ingresos disponibles' : 'Ingresos cerrados',
        `Inicio: ${selectedOpenCut.fecha_inicio || '-'}`,
        `Fin inscripción: ${selectedOpenCut.fecha_fin || 'Sin fecha final'}`,
      ].join(' - ')
    : ''
  const activeScheduleCut = scheduleModal.cut
  const scheduleMetrics = scheduleData?.metrics || {}
  const teamInfo = scheduleData?.team || scheduleData?.teams?.team || null
  const teamMemberMetrics = scheduleData?.teams?.members || {}
  const graphQueueMetrics = scheduleData?.teams?.queue || {}
  const scheduleUnavailable = scheduleData?.continuing_education && !scheduleData.continuing_education.available
  const teamsUnavailable = scheduleData?.teams && !scheduleData.teams.available

  return (
    <section id="admin-course-cuts" className="admin-course-cuts">
      <div className="admin-section-heading">
        <div>
          <h3>Cortes de inscripción</h3>
          <p>Abre y cierra las cortes que reciben matrículas desde el formulario público y la carga Excel.</p>
        </div>
      </div>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Nueva corte</h4>
            <p>La corte abierta recibe inscripciones hasta la fecha final configurada.</p>
          </div>
        </div>

        <form className="auth-form compact-form" onSubmit={handleCreate}>
          <div className="admin-form-grid course-cut-form-grid">
            <label className="field">
              <span>Carrera *</span>
              <select
                name="cod_anio_basica"
                value={form.cod_anio_basica}
                onChange={handleChange}
                disabled={isLoading}
                required
              >
                <option value="">Selecciona una carrera</option>
                {activeCareers.map((career) => (
                  <option key={career.cod_anio_basica} value={career.cod_anio_basica}>
                    {removeNumbersFromLabel(career.nombre_basica)}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Período *</span>
              <select
                name="codigo_periodo"
                value={form.codigo_periodo}
                onChange={handleChange}
                disabled={isLoading}
                required
              >
                <option value="">Selecciona un período</option>
                {activePeriods.map((period) => (
                  <option key={period.cod_periodo} value={period.cod_periodo}>
                    {removeNumbersFromLabel(period.detalle_periodo)}
                  </option>
                ))}
              </select>
            </label>

            <fieldset className="course-subject-picker full-span">
              <legend>Materia(s) *</legend>
              <div className="course-subject-list">
                {selectedCareerCourses.length ? (
                  selectedCareerCourses.map((course) => {
                    const subjectCode = String(course.codigo_materia)
                    const checked = selectedSubjectCodes.includes(subjectCode)
                    return (
                      <label key={subjectCode} className={`course-subject-option ${checked ? 'is-selected' : ''}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => handleSubjectToggle(subjectCode)}
                        />
                        <span>{removeNumbersFromLabel(course.nombre_materia)}</span>
                      </label>
                    )
                  })
                ) : (
                  <p>No hay materias activas para esta carrera.</p>
                )}
              </div>
            </fieldset>

            <div className="bulk-status-note">
              <strong>{selectedOpenCut ? `Corte activa: ${selectedOpenCut.nombre_corte}` : 'Sin corte activa'}</strong>
              <span>
                {selectedOpenCut
                  ? selectedOpenCutStatus
                  : 'Crea una corte para habilitar inscripción pública y Excel.'}
              </span>
            </div>

            <label className="field">
              <span>Número de corte</span>
              <input
                name="numero_corte"
                type="number"
                min="1"
                value={form.numero_corte}
                onChange={handleChange}
                placeholder="Automático"
              />
            </label>

            <label className="field">
              <span>Nombre de corte</span>
              <input
                name="nombre_corte"
                type="text"
                value={form.nombre_corte}
                onChange={handleChange}
                placeholder="Corte 1"
              />
            </label>

            <label className="field">
              <span>Fecha de inicio *</span>
              <input
                name="fecha_inicio"
                type="date"
                value={form.fecha_inicio}
                onChange={handleChange}
                required
              />
            </label>

            <label className="field">
              <span>Fecha final de inscripción</span>
              <input
                name="fecha_fin"
                type="date"
                value={form.fecha_fin}
                onChange={handleChange}
              />
            </label>

            <label className="field">
              <span>Cupo esperado</span>
              <input
                name="cupo_esperado"
                type="number"
                min="0"
                value={form.cupo_esperado}
                onChange={handleChange}
                placeholder="120"
              />
            </label>

            <label className="field">
              <span>Horas</span>
              <input
                name="horas"
                type="number"
                min="0"
                value={form.horas}
                readOnly
                placeholder="Automático"
              />
            </label>

            <label className="field">
              <span>Observación</span>
              <input
                name="observacion"
                type="text"
                value={form.observacion}
                onChange={handleChange}
                placeholder="Apertura inicial"
              />
            </label>

            <div className="bulk-status-note">
              <strong>Materias seleccionadas</strong>
              <span>{selectedSubjectCodes.length ? `${selectedSubjectCodes.length} materia(s) para esta corte.` : 'Selecciona al menos una materia.'}</span>
            </div>
          </div>

          {error ? <p className="form-error">{error}</p> : null}
          {message ? <p className="form-success">{message}</p> : null}

          <button
            type="submit"
            className="submit-button"
            disabled={isSaving || isLoading || Boolean(selectedOpenCut) || !selectedSubjectCodes.length}
          >
            {isSaving ? 'Creando corte...' : 'Crear corte'}
          </button>
        </form>
      </article>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Cortes registradas</h4>
            <p>Cierra una corte para impedir nuevas matrículas dentro de ese grupo.</p>
          </div>
          <button type="button" className="ghost-button compact-button" onClick={handleReloadCuts} disabled={isLoading}>
            Actualizar
          </button>
        </div>

        <div className="admin-table-wrap">
          <table className="admin-table course-cut-table">
            <thead>
              <tr>
                <th>Corte</th>
                <th>Oferta</th>
                <th>Materias</th>
                <th>Inicio</th>
                <th>Fin inscripción</th>
                <th>Estado</th>
                <th>Estudiantes</th>
                <th>Acción</th>
              </tr>
            </thead>
            <tbody>
              {cuts.length ? (
                cuts.map((cut) => (
                  <tr key={cut.corte_id}>
                    <td>
                      <strong>{cut.nombre_corte || `Corte ${cut.numero_corte || '-'}`}</strong>
                      <span>{cut.numero_corte ? `No. ${cut.numero_corte}` : '-'}</span>
                    </td>
                    <td>{cutTargetLabel(cut)}</td>
                    <td>{cutSubjectLabel(cut)}</td>
                    <td>{cut.fecha_inicio || '-'}</td>
                    <td>{cut.fecha_fin || 'Sin fecha final'}</td>
                    <td>
                      <span className={`cut-status-badge ${cutStatusClass(cut)}`}>
                        {cut.estado_inscripcion || cut.estado_corte || '-'}
                      </span>
                    </td>
                    <td>{cut.total_estudiantes ?? 0}</td>
                    <td>
                      <div className="table-actions-stack">
                        <button
                          type="button"
                          className="ghost-button compact-button table-action-button"
                          onClick={() => openScheduleModal(cut)}
                        >
                          Horario y Teams
                        </button>
                        {cut.estado_corte === 'ABIERTO' ? (
                          <button
                            type="button"
                            className="ghost-button compact-button table-action-button"
                            onClick={() => handleClose(cut)}
                            disabled={closingCutId === cut.corte_id}
                          >
                            {closingCutId === cut.corte_id ? 'Cerrando...' : 'Cerrar'}
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="8">No hay cortes registradas.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>

      {scheduleModal.isOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="career-modal schedule-teams-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="schedule-teams-modal-title"
          >
            <div className="career-modal-header">
              <div>
                <h4 id="schedule-teams-modal-title">Horario y Teams</h4>
                <p>{selectedCutLabel(activeScheduleCut)}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={closeScheduleModal}>
                Cerrar
              </button>
            </div>

            <div className="career-modal-body schedule-teams-body">
              {isScheduleLoading ? <p className="form-success">Cargando horario...</p> : null}
              {scheduleError ? <p className="form-error">{scheduleError}</p> : null}
              {scheduleMessage ? <p className="form-success">{scheduleMessage}</p> : null}
              {scheduleUnavailable ? (
                <p className="form-error">{scheduleData?.continuing_education?.message}</p>
              ) : null}

              <section className="schedule-summary-grid" aria-label="Resumen de horario y Teams">
                <div>
                  <span>Horarios</span>
                  <strong>{scheduleMetrics.horarios ?? 0}</strong>
                </div>
                <div>
                  <span>Sesiones</span>
                  <strong>{scheduleMetrics.sesiones ?? 0}</strong>
                </div>
                <div>
                  <span>Team</span>
                  <strong>{teamInfo?.estado_graph || 'Sin Team'}</strong>
                </div>
                <div>
                  <span>Miembros</span>
                  <strong>{teamMemberMetrics.total ?? 0}</strong>
                </div>
              </section>

              <form className="auth-form compact-form schedule-form" onSubmit={handleScheduleSubmit}>
                <div className="admin-form-grid schedule-form-grid">
                  <label className="field">
                    <span>Día *</span>
                    <select
                      name="dia_semana"
                      value={scheduleForm.dia_semana}
                      onChange={handleScheduleChange}
                      required
                    >
                      {WEEKDAY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="field">
                    <span>Modalidad *</span>
                    <select
                      name="modalidad"
                      value={scheduleForm.modalidad}
                      onChange={handleScheduleChange}
                      required
                    >
                      {MODALITY_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="field">
                    <span>Hora inicio *</span>
                    <input
                      name="hora_inicio"
                      type="time"
                      value={scheduleForm.hora_inicio}
                      onChange={handleScheduleChange}
                      required
                    />
                  </label>

                  <label className="field">
                    <span>Hora fin *</span>
                    <input
                      name="hora_fin"
                      type="time"
                      value={scheduleForm.hora_fin}
                      onChange={handleScheduleChange}
                      required
                    />
                  </label>

                  <label className="field">
                    <span>Fecha desde</span>
                    <input
                      name="fecha_desde"
                      type="date"
                      value={scheduleForm.fecha_desde}
                      onChange={handleScheduleChange}
                    />
                  </label>

                  <label className="field">
                    <span>Fecha hasta</span>
                    <input
                      name="fecha_hasta"
                      type="date"
                      value={scheduleForm.fecha_hasta}
                      onChange={handleScheduleChange}
                    />
                  </label>

                  <label className="field">
                    <span>Aula</span>
                    <input
                      name="aula"
                      type="text"
                      value={scheduleForm.aula}
                      onChange={handleScheduleChange}
                      placeholder="Laboratorio, aula o sala"
                    />
                  </label>

                  <label className="field">
                    <span>Enlace Teams</span>
                    <input
                      name="enlace_virtual"
                      type="url"
                      value={scheduleForm.enlace_virtual}
                      onChange={handleScheduleChange}
                      placeholder="https://teams.microsoft.com/..."
                    />
                  </label>

                  <label className="schedule-checkbox full-span">
                    <input
                      name="generar_sesiones"
                      type="checkbox"
                      checked={scheduleForm.generar_sesiones}
                      onChange={handleScheduleChange}
                    />
                    <span>Generar sesiones automáticamente con este horario</span>
                  </label>
                </div>

                <div className="student-selection-actions">
                  <button
                    type="button"
                    className="ghost-button compact-button"
                    onClick={() => setScheduleForm(buildScheduleForm(activeScheduleCut, scheduleData))}
                    disabled={isScheduleSaving || isScheduleLoading}
                  >
                    Limpiar
                  </button>
                  <button
                    type="submit"
                    className="submit-button compact-button"
                    disabled={isScheduleSaving || isScheduleLoading || Boolean(scheduleUnavailable)}
                  >
                    {isScheduleSaving ? 'Guardando...' : 'Guardar horario'}
                  </button>
                </div>
              </form>

              <section className="schedule-list-panel">
                <div className="admin-subsection-header">
                  <div>
                    <h4>Horarios creados</h4>
                    <p>{scheduleData?.schedules?.length || 0} registro(s) activos para esta corte.</p>
                  </div>
                  <button
                    type="button"
                    className="ghost-button compact-button"
                    onClick={() => loadScheduleData(activeScheduleCut)}
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
                        <th>Modalidad</th>
                        <th>Sesiones</th>
                        <th>Enlace</th>
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
                            <td>{schedule.modalidad || '-'}</td>
                            <td>{schedule.total_sesiones ?? 0}</td>
                            <td>{schedule.enlace_virtual ? 'Registrado' : '-'}</td>
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
                          <td colSpan="6">No hay horarios creados para esta corte.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <form className="auth-form compact-form schedule-teams-form" onSubmit={handleTeamsSubmit}>
                <div className="module-card-header">
                  <div>
                    <h4>Matrícula por Teams</h4>
                    <p>
                      {teamInfo?.team_id
                        ? `Team confirmado: ${teamInfo.team_id}`
                        : 'El Team puede quedar encolado hasta que Graph confirme el identificador.'}
                    </p>
                  </div>
                </div>

                {teamsUnavailable ? <p className="form-error">{scheduleData?.teams?.message}</p> : null}

                <div className="admin-form-grid schedule-form-grid">
                  <label className="field">
                    <span>Visibilidad</span>
                    <select name="visibility" value={scheduleForm.visibility} onChange={handleScheduleChange}>
                      <option value="Private">Private</option>
                      <option value="Public">Public</option>
                    </select>
                  </label>

                  <label className="field">
                    <span>Team ID</span>
                    <input
                      name="team_id"
                      type="text"
                      value={scheduleForm.team_id}
                      onChange={handleScheduleChange}
                      placeholder="GUID confirmado por Graph"
                    />
                  </label>

                  <label className="field">
                    <span>Group ID</span>
                    <input
                      name="group_id"
                      type="text"
                      value={scheduleForm.group_id}
                      onChange={handleScheduleChange}
                      placeholder="Opcional"
                    />
                  </label>

                  <label className="field">
                    <span>URL del Team</span>
                    <input
                      name="web_url"
                      type="url"
                      value={scheduleForm.web_url}
                      onChange={handleScheduleChange}
                      placeholder="https://teams.microsoft.com/..."
                    />
                  </label>
                </div>

                <section className="schedule-summary-grid schedule-teams-summary" aria-label="Resumen de Teams">
                  <div>
                    <span>Docentes</span>
                    <strong>{teamMemberMetrics.docentes ?? 0}</strong>
                  </div>
                  <div>
                    <span>Estudiantes</span>
                    <strong>{teamMemberMetrics.estudiantes ?? 0}</strong>
                  </div>
                  <div>
                    <span>En cola</span>
                    <strong>{graphQueueMetrics.pendientes ?? 0}</strong>
                  </div>
                  <div>
                    <span>Errores</span>
                    <strong>{graphQueueMetrics.errores ?? 0}</strong>
                  </div>
                </section>

                <div className="student-selection-actions">
                  <button
                    type="submit"
                    className="submit-button compact-button"
                    disabled={isTeamsSaving || isScheduleLoading || Boolean(teamsUnavailable)}
                  >
                    {isTeamsSaving ? 'Procesando...' : 'Encolar Teams'}
                  </button>
                </div>
              </form>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}
