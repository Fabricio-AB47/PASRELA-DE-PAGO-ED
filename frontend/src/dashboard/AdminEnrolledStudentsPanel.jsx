import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'
import ListExportModal from './ListExportModal.jsx'
import { downloadPeopleList } from './listExports.js'

const numberFormatter = new Intl.NumberFormat('es-EC')

function cutLabel(cut) {
  const name = cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || ''
  return subject ? `${name} - ${subject}` : name
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function formatDecimal(value) {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  return numberFormatter.format(Number(value))
}

function statusClass(status) {
  if (status === 'ACTIVO' || status === 'A' || status === 'INSCRITO' || status === 'CURSANDO') {
    return 'is-open'
  }
  if (status === 'PENDIENTE') {
    return 'is-unavailable'
  }
  return 'is-closed'
}

export default function AdminEnrolledStudentsPanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [isLoadingCuts, setIsLoadingCuts] = useState(true)
  const [isLoadingStudents, setIsLoadingStudents] = useState(false)
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [error, setError] = useState('')

  const loadStudents = useCallback(async (corteId, searchTerm = '') => {
    if (!corteId) {
      return
    }

    setIsLoadingStudents(true)
    setError('')

    try {
      const params = new URLSearchParams({ corte_id: corteId, limit: '1000' })
      if (searchTerm.trim()) {
        params.set('q', searchTerm.trim())
      }
      const response = await adminFetch(`/api/auth/admin/enrolled-students/?${params.toString()}`)
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar matriculados (${response.status}).`)
      }

      setResult(payload.result)
    } catch (loadError) {
      setResult(null)
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

  const students = useMemo(() => result?.students || [], [result])
  const selectedCut = useMemo(
    () => cuts.find((cut) => String(cut.corte_id) === String(selectedCutId)) || null,
    [cuts, selectedCutId],
  )
  const metrics = result?.metrics || {}
  const complement = result?.continuing_education

  function handleCutChange(event) {
    const nextCutId = event.target.value
    setSelectedCutId(nextCutId)
    if (nextCutId) {
      loadStudents(nextCutId, query)
    } else {
      setResult(null)
    }
  }

  function handleSearch(event) {
    event.preventDefault()
    loadStudents(selectedCutId, query)
  }

  async function handleDownload(format) {
    setIsExporting(true)
    setError('')
    try {
      await downloadPeopleList({
        kind: 'students',
        format,
        title: `LISTADO DE ESTUDIANTES${selectedCut ? ` - ${cutLabel(selectedCut)}` : ''}`,
        rows: students,
      })
      setIsExportOpen(false)
    } catch (downloadError) {
      setError(downloadError.message)
      setIsExportOpen(false)
    } finally {
      setIsExporting(false)
    }
  }

  return (
    <section id="admin-enrolled-students" className="admin-course-cuts">
      <div className="admin-section-heading">
        <div>
          <h3>Estudiantes matriculados</h3>
          <p>Consulta los estudiantes activos que ya constan en la matrícula complementaria de la cohorte.</p>
        </div>
      </div>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Filtro de cohorte</h4>
            <p>La consulta toma los registros desde INTECEDUCONTINUA vinculados por CorteId.</p>
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
              Buscar matriculados
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
        <section className="bulk-summary-grid enrollment-summary-grid" aria-label="Resumen de estudiantes matriculados">
          <div>
            <span>Matriculados</span>
            <strong>{formatNumber(metrics.total)}</strong>
          </div>
          <div>
            <span>Con nota</span>
            <strong>{formatNumber(metrics.con_nota)}</strong>
          </div>
          <div>
            <span>Notas cerradas</span>
            <strong>{formatNumber(metrics.notas_pasadas)}</strong>
          </div>
          <div>
            <span>Sin cerrar</span>
            <strong>{formatNumber(metrics.pendientes_pase)}</strong>
          </div>
        </section>
      ) : null}

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Matriculados</h4>
            <p>Revisa identificación, correo, asistencia y nota registrada por estudiante.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => setIsExportOpen(true)}
            disabled={isLoadingStudents || !students.length}
          >
            Descargar listado
          </button>
        </div>

        {error ? <p className="form-error">{error}</p> : null}

        <div className="admin-table-wrap">
          <table className="admin-table course-cut-table enrollment-table">
            <thead>
              <tr>
                <th>Estudiante</th>
                <th>Cédula</th>
                <th>Correo</th>
                <th>Estado</th>
                <th>Asistencia</th>
                <th>Nota</th>
                <th>Estado nota</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingStudents ? (
                <tr>
                  <td colSpan="7">Cargando estudiantes matriculados...</td>
                </tr>
              ) : students.length ? (
                students.map((student) => (
                  <tr key={student.corte_estudiante_id}>
                    <td>
                      <strong>{student.nombre}</strong>
                      <span>Código {student.codigo_estud || '-'}</span>
                    </td>
                    <td>{student.cedula || '-'}</td>
                    <td>{student.correo_intec || student.correo_personal || '-'}</td>
                    <td>
                      <span className={`cut-status-badge ${statusClass(student.estado_participacion || student.estado_complemento)}`}>
                        {student.estado_participacion || student.estado_complemento || '-'}
                      </span>
                    </td>
                    <td>{student.porcentaje_asistencia !== null ? `${formatDecimal(student.porcentaje_asistencia)}%` : '-'}</td>
                    <td>{formatDecimal(student.nota_final)}</td>
                    <td>{student.pase_estado_label}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="7">No hay estudiantes matriculados para la cohorte seleccionada.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>
      <ListExportModal
        isOpen={isExportOpen}
        title="Descargar listado de estudiantes"
        recordCount={students.length}
        isDownloading={isExporting}
        onClose={() => setIsExportOpen(false)}
        onDownload={handleDownload}
      />
    </section>
  )
}
