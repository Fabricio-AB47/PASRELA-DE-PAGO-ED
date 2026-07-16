import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const ATTENDANCE_OPTIONS = [
  { value: 'PRESENTE', label: 'Presente' },
  { value: 'AUSENTE', label: 'Ausente' },
  { value: 'TARDANZA', label: 'Tardanza' },
  { value: 'JUSTIFICADO', label: 'Justificado' },
]

function cutLabel(cut) {
  const name = cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || ''
  return subject ? `${name} - ${subject}` : name
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function formatPercent(value) {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  return `${numberFormatter.format(Number(value))}%`
}

export default function AdminAttendancePanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [result, setResult] = useState(null)
  const [attendanceValues, setAttendanceValues] = useState({})
  const [isLoadingCuts, setIsLoadingCuts] = useState(true)
  const [isLoadingStudents, setIsLoadingStudents] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const applyLoadedResult = useCallback((nextResult) => {
    setResult(nextResult)
    setAttendanceValues(
      Object.fromEntries(
        (nextResult?.students || []).map((student) => [
          student.corte_estudiante_id,
          student.estado_asistencia || 'AUSENTE',
        ]),
      ),
    )
  }, [])

  const loadAttendance = useCallback(async (corteId) => {
    if (!corteId) {
      return
    }

    setIsLoadingStudents(true)
    setMessage('')
    setError('')

    try {
      const params = new URLSearchParams({
        corte_id: corteId,
      })
      const response = await adminFetch(`/api/auth/admin/attendance/?${params.toString()}`)
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar asistencia (${response.status}).`)
      }

      applyLoadedResult(payload.result)
    } catch (loadError) {
      setResult(null)
      setAttendanceValues({})
      setError(loadError.message)
    } finally {
      setIsLoadingStudents(false)
    }
  }, [applyLoadedResult])

  useEffect(() => {
    let isMounted = true

    async function loadCuts() {
      setIsLoadingCuts(true)
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
        const initialCutId = loadedCuts[0]?.corte_id || ''
        setCuts(loadedCuts)
        setSelectedCutId(initialCutId)
        if (initialCutId) {
          await loadAttendance(initialCutId)
        }
      } catch (loadError) {
        if (isMounted) {
          setError(loadError.message)
        }
      } finally {
        if (isMounted) {
          setIsLoadingCuts(false)
        }
      }
    }

    loadCuts()

    return () => {
      isMounted = false
    }
  }, [loadAttendance])

  const students = useMemo(() => result?.students || [], [result])
  const metrics = result?.metrics || {}
  const complement = result?.continuing_education
  const session = result?.session

  function handleCutChange(event) {
    const nextCutId = event.target.value
    setSelectedCutId(nextCutId)
    if (nextCutId) {
      loadAttendance(nextCutId)
    } else {
      setResult(null)
      setAttendanceValues({})
    }
  }

  function handleFilterSubmit(event) {
    event.preventDefault()
    loadAttendance(selectedCutId)
  }

  function updateAttendance(studentId, value) {
    setAttendanceValues((current) => ({
      ...current,
      [studentId]: value,
    }))
  }

  function markAllAs(status) {
    setAttendanceValues(
      Object.fromEntries(
        students.map((student) => [student.corte_estudiante_id, status]),
      ),
    )
  }

  async function saveAttendance() {
    if (!selectedCutId) {
      setError('Selecciona una cohorte antes de guardar asistencia.')
      return
    }
    if (!students.length) {
      setError('No hay estudiantes matriculados para guardar asistencia.')
      return
    }

    setIsSaving(true)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/admin/attendance/save/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          corte_id: selectedCutId,
          fecha: result?.fecha,
          hora: result?.hora,
          records: students.map((student) => ({
            corte_estudiante_id: student.corte_estudiante_id,
            estado_asistencia: attendanceValues[student.corte_estudiante_id] || 'AUSENTE',
          })),
        }),
      })
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible guardar asistencia (${response.status}).`)
      }

      applyLoadedResult(payload.result.updated)
      const summary = payload.result.summary || {}
      setMessage(
        `Procesados: ${formatNumber(summary.procesados)}. Guardados: ${formatNumber(summary.guardados)}. Errores: ${formatNumber(summary.errores)}.`,
      )
    } catch (saveError) {
      setError(saveError.message)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section id="admin-attendance" className="admin-course-cuts">
      <div className="admin-section-heading">
        <div>
          <h3>Asistencia</h3>
          <p>Registra la asistencia de estudiantes matriculados en la base complementaria por cohorte.</p>
        </div>
      </div>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Filtro de sesión</h4>
            <p>La fecha y hora se calculan automáticamente al cargar la asistencia.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => loadAttendance(selectedCutId)}
            disabled={!selectedCutId || isLoadingStudents}
          >
            Actualizar
          </button>
        </div>

        <form className="admin-form-grid attendance-admin-filter" onSubmit={handleFilterSubmit}>
          <label className="field">
            <span>Cohorte</span>
            <select value={selectedCutId} onChange={handleCutChange} disabled={isLoadingCuts}>
              <option value="">Selecciona una cohorte</option>
              {cuts.map((cut) => (
                <option key={cut.corte_id} value={cut.corte_id}>
                  {cutLabel(cut)}
                </option>
              ))}
            </select>
          </label>
          <div className="student-selection-actions">
            <button type="submit" className="submit-button compact-button" disabled={!selectedCutId || isLoadingStudents}>
              Cargar asistencia
            </button>
          </div>
        </form>

        {session ? (
          <div className="attendance-selected-course">
            <strong>Sesión {session.sesion_id}</strong>
            <span>{session.fecha} de {session.hora_inicio} a {session.hora_fin} - {session.estado}</span>
          </div>
        ) : result ? (
          <div className="attendance-selected-course">
            <strong>Fecha y hora automáticas</strong>
            <span>{result.fecha} - {result.hora}</span>
          </div>
        ) : null}

        {complement ? (
          <p className={`status-message ${complement.available ? 'success' : 'error'}`}>
            {complement.database}: {complement.message}
          </p>
        ) : null}
      </article>

      {result ? (
        <section className="bulk-summary-grid enrollment-summary-grid" aria-label="Resumen de asistencia">
          <div>
            <span>Matriculados</span>
            <strong>{formatNumber(metrics.total)}</strong>
          </div>
          <div>
            <span>Presentes</span>
            <strong>{formatNumber(metrics.presentes)}</strong>
          </div>
          <div>
            <span>Ausentes</span>
            <strong>{formatNumber(metrics.ausentes)}</strong>
          </div>
          <div>
            <span>Justificados</span>
            <strong>{formatNumber(metrics.justificados)}</strong>
          </div>
        </section>
      ) : null}

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Registro de asistencia</h4>
            <p>Define el estado por estudiante y guarda la sesión en educación continua.</p>
          </div>
          <div className="student-selection-actions">
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={() => markAllAs('PRESENTE')}
              disabled={!students.length || isSaving}
            >
              Marcar presentes
            </button>
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={() => markAllAs('AUSENTE')}
              disabled={!students.length || isSaving}
            >
              Marcar ausentes
            </button>
            <button
              type="button"
              className="submit-button compact-button"
              onClick={saveAttendance}
              disabled={!students.length || isSaving || !complement?.available}
            >
              {isSaving ? 'Guardando...' : 'Guardar asistencia'}
            </button>
          </div>
        </div>

        {error ? <p className="form-error">{error}</p> : null}
        {message ? <p className="form-success">{message}</p> : null}

        <div className="admin-table-wrap attendance-roster-table">
          <table className="admin-table course-cut-table enrollment-table attendance-admin-table">
            <thead>
              <tr>
                <th>Estudiante</th>
                <th>Cédula</th>
                <th>Estado</th>
                <th>Asistencia</th>
                <th>Sesiones</th>
                <th>Correo</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingStudents ? (
                <tr>
                  <td colSpan="6">Cargando asistencia...</td>
                </tr>
              ) : students.length ? (
                students.map((student) => (
                  <tr key={student.corte_estudiante_id}>
                    <td>
                      <strong>{student.nombre}</strong>
                      <span>Código {student.codigo_estud || '-'}</span>
                    </td>
                    <td>{student.cedula || '-'}</td>
                    <td>
                      <select
                        value={attendanceValues[student.corte_estudiante_id] || 'AUSENTE'}
                        onChange={(event) => updateAttendance(student.corte_estudiante_id, event.target.value)}
                      >
                        {ATTENDANCE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>{formatPercent(student.porcentaje_asistencia)}</td>
                    <td>{formatNumber(student.total_sesiones)}</td>
                    <td>{student.correo_intec || '-'}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="6">No hay estudiantes matriculados para la cohorte seleccionada.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  )
}
