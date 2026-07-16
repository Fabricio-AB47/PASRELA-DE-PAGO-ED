import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')

function cutLabel(cut) {
  const name = cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || ''
  return subject ? `${name} - ${subject}` : name
}

function studentSyncStatus(student) {
  if (student.continuing_education?.synced) {
    return student.continuing_education.estado || 'Sincronizado'
  }
  if (!student.activo) {
    return 'Inactivo'
  }
  return 'Pendiente'
}

export default function AdminCourseStudentsPanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [cutStudents, setCutStudents] = useState(null)
  const [selectedStudentIds, setSelectedStudentIds] = useState([])
  const [isLoadingCuts, setIsLoadingCuts] = useState(true)
  const [isLoadingStudents, setIsLoadingStudents] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadStudents = useCallback(async (corteId) => {
    if (!corteId) {
      return
    }

    setIsLoadingStudents(true)
    setSelectedStudentIds([])
    setMessage('')
    setError('')

    try {
      const params = new URLSearchParams({ corte_id: corteId })
      const response = await adminFetch(`/api/auth/admin/course-cuts/students/?${params.toString()}`)
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar estudiantes (${response.status}).`)
      }

      setCutStudents(payload.result)
    } catch (loadError) {
      setCutStudents(null)
      setError(loadError.message)
    } finally {
      setIsLoadingStudents(false)
    }
  }, [])

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
          await loadStudents(initialCutId)
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
  }, [loadStudents])

  const students = useMemo(() => cutStudents?.students || [], [cutStudents])
  const pendingStudents = useMemo(
    () => students.filter((student) => student.activo && !student.continuing_education?.synced),
    [students],
  )
  const selectableStudents = useMemo(
    () => students.filter((student) => student.activo),
    [students],
  )
  const selectedCount = selectedStudentIds.length
  const allSelectableSelected = selectableStudents.length > 0 && selectedCount === selectableStudents.length

  function handleCutChange(event) {
    const nextCutId = event.target.value
    setSelectedCutId(nextCutId)
    if (nextCutId) {
      loadStudents(nextCutId)
    } else {
      setCutStudents(null)
      setSelectedStudentIds([])
    }
  }

  function toggleStudent(studentId) {
    setSelectedStudentIds((current) => (
      current.includes(studentId)
        ? current.filter((item) => item !== studentId)
        : [...current, studentId]
    ))
  }

  function toggleAllSelectable() {
    setSelectedStudentIds(
      allSelectableSelected
        ? []
        : selectableStudents.map((student) => student.corte_estudiante_id),
    )
  }

  async function syncStudents(studentIds) {
    if (!selectedCutId) {
      setError('Selecciona una cohorte antes de sincronizar.')
      return
    }
    if (!studentIds.length) {
      setError('No hay estudiantes seleccionados para sincronizar.')
      return
    }

    setIsSyncing(true)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/students/sync/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          corte_id: selectedCutId,
          student_ids: studentIds,
        }),
      })
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible sincronizar estudiantes (${response.status}).`)
      }

      setCutStudents(payload.result.updated)
      setSelectedStudentIds([])
      const summary = payload.result.summary || {}
      setMessage(
        `Procesados: ${formatNumber(summary.procesados)}. Sincronizados: ${formatNumber(summary.sincronizados)}. Errores: ${formatNumber(summary.errores)}.`,
      )
    } catch (syncError) {
      setError(syncError.message)
    } finally {
      setIsSyncing(false)
    }
  }

  const complement = cutStudents?.continuing_education
  const metrics = cutStudents?.metrics || {}

  return (
    <section id="admin-course-students" className="admin-course-cuts">
      <div className="admin-section-heading">
        <div>
          <h3>Estudiantes de cohorte</h3>
          <p>Consulta los estudiantes oficiales de INTECBDD y matricúlalos en la base complementaria de educación continua.</p>
        </div>
      </div>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Seleccionar cohorte</h4>
            <p>La sincronización mantiene INTECBDD como fuente oficial y registra el control complementario por CorteId.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => loadStudents(selectedCutId)}
            disabled={!selectedCutId || isLoadingStudents}
          >
            Actualizar
          </button>
        </div>

        <div className="admin-form-grid">
          <label className="field full-span">
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
        </div>

        {complement ? (
          <p className={`status-message ${complement.available ? 'success' : 'error'}`}>
            {complement.database}: {complement.message}
          </p>
        ) : null}
      </article>

      {cutStudents ? (
        <section className="bulk-summary-grid" aria-label="Resumen de estudiantes de cohorte">
          <div>
            <span>Total</span>
            <strong>{formatNumber(metrics.total)}</strong>
          </div>
          <div>
            <span>Activos</span>
            <strong>{formatNumber(metrics.activos)}</strong>
          </div>
          <div>
            <span>Sincronizados</span>
            <strong>{formatNumber(metrics.sincronizados)}</strong>
          </div>
          <div>
            <span>Pendientes</span>
            <strong>{formatNumber(metrics.pendientes)}</strong>
          </div>
        </section>
      ) : null}

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Estudiantes</h4>
            <p>Selecciona estudiantes activos o sincroniza todos los pendientes hacia INTECEDUCONTINUA.</p>
          </div>
          <div className="student-selection-actions">
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={() => syncStudents(pendingStudents.map((student) => student.corte_estudiante_id))}
              disabled={!pendingStudents.length || isSyncing || !complement?.available}
            >
              {isSyncing ? 'Sincronizando...' : 'Sincronizar pendientes'}
            </button>
            <button
              type="button"
              className="submit-button compact-button"
              onClick={() => syncStudents(selectedStudentIds)}
              disabled={!selectedCount || isSyncing || !complement?.available}
            >
              {isSyncing ? 'Procesando...' : `Sincronizar seleccionados (${selectedCount})`}
            </button>
          </div>
        </div>

        {error ? <p className="form-error">{error}</p> : null}
        {message ? <p className="form-success">{message}</p> : null}

        <div className="admin-table-wrap">
          <table className="admin-table course-cut-table">
            <thead>
              <tr>
                <th>
                  <label className="attendance-toggle">
                    <input
                      type="checkbox"
                      checked={allSelectableSelected}
                      onChange={toggleAllSelectable}
                      disabled={!selectableStudents.length}
                    />
                    <span>Sel.</span>
                  </label>
                </th>
                <th>Estudiante</th>
                <th>Cédula</th>
                <th>Matrícula</th>
                <th>Participación</th>
                <th>Registro</th>
                <th>Educación continua</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingStudents ? (
                <tr>
                  <td colSpan="7">Cargando estudiantes...</td>
                </tr>
              ) : students.length ? (
                students.map((student) => {
                  const selected = selectedStudentIds.includes(student.corte_estudiante_id)
                  return (
                    <tr key={student.corte_estudiante_id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={!student.activo}
                          onChange={() => toggleStudent(student.corte_estudiante_id)}
                        />
                      </td>
                      <td>
                        <strong>{student.nombre}</strong>
                        <span>Código {student.codigo_estud || '-'}</span>
                      </td>
                      <td>{student.cedula || '-'}</td>
                      <td>{student.num_matricula || '-'}</td>
                      <td>{student.estado_participacion || '-'}</td>
                      <td>{student.estado_registro || '-'}</td>
                      <td>{studentSyncStatus(student)}</td>
                    </tr>
                  )
                })
              ) : (
                <tr>
                  <td colSpan="7">No hay estudiantes registrados en la cohorte seleccionada.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  )
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}
