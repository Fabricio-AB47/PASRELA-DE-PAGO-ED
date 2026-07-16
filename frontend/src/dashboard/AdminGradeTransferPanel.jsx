import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')

function cutLabel(cut) {
  const name = cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || ''
  return subject ? `${name} - ${subject}` : name
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function formatGrade(value) {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  return numberFormatter.format(Number(value))
}

function gradeStatusClass(status) {
  if (status === 'PASADA') {
    return 'is-open'
  }
  if (status === 'PENDIENTE') {
    return 'is-unavailable'
  }
  return 'is-closed'
}

function gradeValueFromStudent(student) {
  if (student.nota_final === null || student.nota_final === undefined) {
    return ''
  }
  return String(student.nota_final)
}

function clampGradeInput(value) {
  const normalized = String(value || '').replace(',', '.').trim()
  if (!normalized) {
    return ''
  }
  const numericValue = Number(normalized)
  if (!Number.isFinite(numericValue)) {
    return ''
  }
  if (numericValue < 0) {
    return '0.00'
  }
  if (numericValue > 10) {
    return '10.00'
  }
  const [integerPart, decimalPart] = normalized.split('.')
  if (decimalPart !== undefined) {
    return `${integerPart || '0'}.${decimalPart.slice(0, 2)}`
  }
  return normalized
}

function formatGradeInput(value) {
  const clamped = clampGradeInput(value)
  if (!clamped) {
    return ''
  }
  return Number(clamped).toFixed(2)
}

export default function AdminGradeTransferPanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [gradeValues, setGradeValues] = useState({})
  const [selectedStudentIds, setSelectedStudentIds] = useState([])
  const [isLoadingCuts, setIsLoadingCuts] = useState(true)
  const [isLoadingStudents, setIsLoadingStudents] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const applyLoadedResult = useCallback((nextResult) => {
    setResult(nextResult)
    setSelectedStudentIds([])
    setGradeValues(
      Object.fromEntries(
        (nextResult?.students || []).map((student) => [
          student.corte_estudiante_id,
          gradeValueFromStudent(student),
        ]),
      ),
    )
  }, [])

  const loadStudents = useCallback(async (corteId, searchTerm = '') => {
    if (!corteId) {
      return
    }

    setIsLoadingStudents(true)
    setMessage('')
    setError('')

    try {
      const params = new URLSearchParams({ corte_id: corteId })
      if (searchTerm.trim()) {
        params.set('q', searchTerm.trim())
      }
      const response = await adminFetch(`/api/auth/admin/grade-transfer/?${params.toString()}`)
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar notas (${response.status}).`)
      }

      applyLoadedResult(payload.result)
    } catch (loadError) {
      setResult(null)
      setSelectedStudentIds([])
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

  const students = useMemo(() => result?.students || [], [result])
  const metrics = result?.metrics || {}
  const complement = result?.continuing_education
  const selectableStudents = useMemo(
    () => students.filter((student) => student.corte_estudiante_id),
    [students],
  )
  const selectedCount = selectedStudentIds.length
  const allSelectableSelected = selectableStudents.length > 0 && selectedCount === selectableStudents.length

  function handleCutChange(event) {
    const nextCutId = event.target.value
    setSelectedCutId(nextCutId)
    if (nextCutId) {
      loadStudents(nextCutId, query)
    } else {
      setResult(null)
      setGradeValues({})
      setSelectedStudentIds([])
    }
  }

  function handleSearch(event) {
    event.preventDefault()
    loadStudents(selectedCutId, query)
  }

  function updateGrade(studentId, value) {
    setGradeValues((current) => ({
      ...current,
      [studentId]: clampGradeInput(value),
    }))
  }

  function normalizeGrade(studentId) {
    setGradeValues((current) => ({
      ...current,
      [studentId]: formatGradeInput(current[studentId]),
    }))
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

  function selectPendingStudents() {
    setSelectedStudentIds(
      students
        .filter((student) => (
          student.pase_estado !== 'PASADA' &&
          String(gradeValues[student.corte_estudiante_id] || '').trim()
        ))
        .map((student) => student.corte_estudiante_id),
    )
  }

  function buildGradeRecords(studentIds) {
    const selectedSet = new Set(studentIds)
    return students
      .filter((student) => selectedSet.has(student.corte_estudiante_id))
      .map((student) => ({
        corte_estudiante_id: student.corte_estudiante_id,
        nota_final: gradeValues[student.corte_estudiante_id],
      }))
  }

  function allStudentsWithGrade() {
    return students
      .filter((student) => (
        String(gradeValues[student.corte_estudiante_id] || '').trim()
      ))
      .map((student) => student.corte_estudiante_id)
  }

  async function submitGrades(studentIds) {
    if (!selectedCutId) {
      setError('Selecciona una cohorte antes de guardar notas.')
      return
    }
    if (!studentIds.length) {
      setError('Selecciona al menos un estudiante con nota final.')
      return
    }

    setIsSaving(true)
    setMessage('')
    setError('')

    try {
      const response = await adminFetch('/api/auth/admin/grade-transfer/save/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          corte_id: selectedCutId,
          q: query,
          records: buildGradeRecords(studentIds),
        }),
      })
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible procesar notas (${response.status}).`)
      }

      applyLoadedResult(payload.result.updated)
      const summary = payload.result.summary || {}
      setMessage(
        `Procesados: ${formatNumber(summary.procesados)}. Complemento: ${formatNumber(summary.notas_pasadas)}. INTECBDD: ${formatNumber(summary.sincronizadas_intecbdd)}. Errores: ${formatNumber(summary.errores)}.`,
      )
    } catch (saveError) {
      setError(saveError.message)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section id="admin-grade-transfer" className="admin-course-cuts">
      <div className="admin-section-heading">
        <div>
          <h3>Pase de notas</h3>
        </div>
      </div>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Filtro de cohorte</h4>
            <p>Selecciona una cohorte para listar sus estudiantes matriculados y procesar sus notas.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => loadStudents(selectedCutId, query)}
            disabled={!selectedCutId || isLoadingStudents}
          >
            Actualizar
          </button>
        </div>

        <form className="admin-form-grid" onSubmit={handleSearch}>
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
          <label className="field">
            <span>Buscar</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Nombre, cédula o código"
            />
          </label>
          <div className="student-selection-actions full-span">
            <button type="submit" className="submit-button compact-button" disabled={!selectedCutId || isLoadingStudents}>
              Buscar estudiantes
            </button>
          </div>
        </form>

        {complement ? (
          <p className={`status-message ${complement.available ? 'success' : 'error'}`}>
            {complement.database}: {complement.message}
          </p>
        ) : null}
      </article>

      {result ? (
        <section className="bulk-summary-grid enrollment-summary-grid" aria-label="Resumen de pase de notas">
          <div>
            <span>Matriculados</span>
            <strong>{formatNumber(metrics.total)}</strong>
          </div>
          <div>
            <span>Con nota</span>
            <strong>{formatNumber(metrics.con_nota)}</strong>
          </div>
          <div>
            <span>Pasadas</span>
            <strong>{formatNumber(metrics.notas_pasadas)}</strong>
          </div>
          <div>
            <span>Pendientes</span>
            <strong>{formatNumber(metrics.pendientes_pase)}</strong>
          </div>
        </section>
      ) : null}

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Notas finales</h4>
            <p>Edita notas entre 1 y 10; la misma nota se guarda en ambas bases.</p>
          </div>
          <div className="student-selection-actions">
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={selectPendingStudents}
              disabled={!students.length || isSaving}
            >
              Seleccionar pendientes
            </button>
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={() => submitGrades(allStudentsWithGrade())}
              disabled={!students.length || isSaving || !complement?.available}
            >
              {isSaving ? 'Procesando...' : 'Sincronizar todas con nota'}
            </button>
            <button
              type="button"
              className="submit-button compact-button"
              onClick={() => submitGrades(selectedStudentIds)}
              disabled={!selectedCount || isSaving || !complement?.available}
            >
              {isSaving ? 'Guardando...' : `Sincronizar seleccionados (${selectedCount})`}
            </button>
          </div>
        </div>

        {error ? <p className="form-error">{error}</p> : null}
        {message ? <p className="form-success">{message}</p> : null}

        <div className="admin-table-wrap">
          <table className="admin-table course-cut-table enrollment-table grade-transfer-table">
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
                <th>Nota final</th>
                <th>Asistencia</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingStudents ? (
                <tr>
                  <td colSpan="6">Cargando notas...</td>
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
                          onChange={() => toggleStudent(student.corte_estudiante_id)}
                        />
                      </td>
                      <td>
                        <strong>{student.nombre}</strong>
                        <span>Código {student.codigo_estud || '-'}</span>
                      </td>
                      <td>{student.cedula || '-'}</td>
                      <td>
                        <input
                          className="grade-input"
                          type="number"
                          min="0"
                          max="10"
                          step="0.01"
                          inputMode="decimal"
                          value={gradeValues[student.corte_estudiante_id] ?? ''}
                          onChange={(event) => updateGrade(student.corte_estudiante_id, event.target.value)}
                          onBlur={() => normalizeGrade(student.corte_estudiante_id)}
                          placeholder="0.00"
                        />
                      </td>
                      <td>{student.porcentaje_asistencia !== null ? `${formatGrade(student.porcentaje_asistencia)}%` : '-'}</td>
                      <td>
                        <span className={`cut-status-badge ${gradeStatusClass(student.pase_estado)}`}>
                          {student.pase_estado_label}
                        </span>
                      </td>
                    </tr>
                  )
                })
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
