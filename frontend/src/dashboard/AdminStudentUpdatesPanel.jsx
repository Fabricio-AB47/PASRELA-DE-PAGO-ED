import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const EMPTY_FORM = {
  nombre: '',
  cedula: '',
  correo_personal: '',
  correo_intec: '',
  telefono: '',
  movil: '',
  ciudad: '',
  direccion: '',
  fecha_nacimiento: '',
  sexo: '',
}

function cutLabel(cut) {
  const name = cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const course = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || ''
  return course ? `${name} - ${course}` : name
}

function formFromStudent(student) {
  return Object.fromEntries(
    Object.keys(EMPTY_FORM).map((key) => [key, student?.[key] || '']),
  )
}

export default function AdminStudentUpdatesPanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [query, setQuery] = useState('')
  const [students, setStudents] = useState([])
  const [selectedStudent, setSelectedStudent] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const selectedCut = useMemo(
    () => cuts.find((cut) => String(cut.corte_id) === String(selectedCutId)) || null,
    [cuts, selectedCutId],
  )

  const loadStudents = useCallback(async (corteId, search = '') => {
    if (!corteId) return
    setIsLoading(true)
    setError('')
    setMessage('')
    try {
      const params = new URLSearchParams({ corte_id: corteId })
      if (search.trim()) params.set('q', search.trim())
      const response = await adminFetch(`/api/auth/admin/student-updates/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!response.ok || !payload?.ok || !payload.result) {
        throw new Error(payload?.message || `No fue posible cargar estudiantes (${response.status}).`)
      }
      setStudents(payload.result.students || [])
      setSelectedStudent(null)
      setForm(EMPTY_FORM)
    } catch (loadError) {
      setStudents([])
      setError(loadError.message)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    async function loadCuts() {
      setIsLoading(true)
      try {
        const response = await adminFetch('/api/auth/admin/course-cuts/')
        const payload = await readResponsePayload(response)
        if (!response.ok || !payload?.ok) {
          throw new Error(payload?.message || `No fue posible cargar cohortes (${response.status}).`)
        }
        if (!mounted) return
        const loadedCuts = payload.cuts || []
        const initialCut = loadedCuts[0]?.corte_id || ''
        setCuts(loadedCuts)
        setSelectedCutId(initialCut)
        if (initialCut) await loadStudents(initialCut)
      } catch (loadError) {
        if (mounted) setError(loadError.message)
      } finally {
        if (mounted) setIsLoading(false)
      }
    }
    loadCuts()
    return () => {
      mounted = false
    }
  }, [loadStudents])

  const hasChanges = useMemo(() => {
    if (!selectedStudent) return false
    return Object.keys(EMPTY_FORM).some(
      (key) => String(form[key] || '').trim() !== String(selectedStudent[key] || '').trim(),
    )
  }, [form, selectedStudent])

  function selectStudent(student) {
    setSelectedStudent(student)
    setForm(formFromStudent(student))
    setError('')
    setMessage('')
  }

  function closeEditor() {
    if (isSaving) return
    setSelectedStudent(null)
    setForm(EMPTY_FORM)
    setError('')
    setMessage('')
  }

  function updateField(event) {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: value }))
  }

  async function handleSave(event) {
    event.preventDefault()
    if (!selectedStudent || !hasChanges) return
    setIsSaving(true)
    setError('')
    setMessage('')
    try {
      const response = await adminFetch('/api/auth/admin/student-updates/save/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          corte_id: selectedStudent.corte_id,
          codigo_estud: selectedStudent.codigo_estud,
          ...form,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!response.ok || !payload?.ok || !payload.student) {
        throw new Error(payload?.message || `No fue posible actualizar el estudiante (${response.status}).`)
      }
      const updated = payload.student
      setStudents((current) => current.map(
        (student) => student.codigo_estud === updated.codigo_estud ? updated : student,
      ))
      setSelectedStudent(updated)
      setForm(formFromStudent(updated))
      setMessage(payload.message || 'Información actualizada.')
      if (payload.complement_sync && !payload.complement_sync.synced) {
        setMessage(`${payload.message} ${payload.complement_sync.message}`)
      }
    } catch (saveError) {
      setError(saveError.message)
    } finally {
      setIsSaving(false)
    }
  }

  function handleSearch(event) {
    event.preventDefault()
    loadStudents(selectedCutId, query)
  }

  return (
    <section id="admin-student-updates" className="admin-course-cuts student-update-workspace">
      <div className="admin-section-heading">
        <div>
          <h3>Actualizar estudiantes matriculados</h3>
          <p>Edita información principal y de contacto sin modificar pagos, notas ni matrícula.</p>
        </div>
      </div>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Buscar matrícula</h4>
            <p>Selecciona una cohorte y localiza al estudiante por nombre, identificación, código o correo.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            disabled={!selectedCutId || isLoading}
            onClick={() => loadStudents(selectedCutId, query)}
          >
            Actualizar lista
          </button>
        </div>
        <form className="admin-form-grid" onSubmit={handleSearch}>
          <label className="field">
            <span>Cohorte</span>
            <select
              value={selectedCutId}
              onChange={(event) => {
                const corteId = event.target.value
                setSelectedCutId(corteId)
                if (corteId) loadStudents(corteId, query)
              }}
            >
              <option value="">Selecciona una cohorte</option>
              {cuts.map((cut) => (
                <option key={cut.corte_id} value={cut.corte_id}>{cutLabel(cut)}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Buscar estudiante</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Nombre, cédula, código o correo"
            />
          </label>
          <div className="student-selection-actions full-span">
            <button type="submit" className="submit-button compact-button" disabled={!selectedCutId || isLoading}>
              {isLoading ? 'Buscando...' : 'Buscar estudiantes'}
            </button>
          </div>
        </form>
        {error && !selectedStudent ? <p className="form-error student-update-search-status">{error}</p> : null}
      </article>

      <article className="module-card course-cut-card student-update-list-card">
        <div className="module-card-header">
          <div>
            <h4>Listado de estudiantes matriculados</h4>
            <p>
              {students.length} registro(s)
              {selectedCut ? ` en ${cutLabel(selectedCut)}` : ''}
              {query.trim() ? ` para “${query.trim()}”` : ''}.
            </p>
          </div>
        </div>
        <div className="admin-table-wrap student-update-table-wrap">
          <table className="admin-table student-update-table">
            <thead>
              <tr>
                <th>Estudiante</th>
                <th>Código</th>
                <th>Identificación</th>
                <th>Correo</th>
                <th>Teléfono</th>
                <th>Cohorte</th>
                <th>Acción</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr><td colSpan="7">Cargando estudiantes...</td></tr>
              ) : students.length ? students.map((student) => (
                <tr key={`${student.corte_id}-${student.codigo_estud}`}>
                  <td><strong>{student.nombre}</strong></td>
                  <td>{student.codigo_estud || '-'}</td>
                  <td>{student.cedula || '-'}</td>
                  <td>
                    <span className="student-update-primary-contact">
                      {student.correo_personal || student.correo_intec || '-'}
                    </span>
                    {student.correo_personal && student.correo_intec
                      ? <span>{student.correo_intec}</span>
                      : null}
                  </td>
                  <td>{student.movil || student.telefono || '-'}</td>
                  <td>{selectedCut ? cutLabel(selectedCut) : `Cohorte ${student.corte_id}`}</td>
                  <td>
                    <button type="button" className="ghost-button compact-button" onClick={() => selectStudent(student)}>
                      Editar
                    </button>
                  </td>
                </tr>
              )) : (
                <tr><td colSpan="7">No hay estudiantes para la cohorte y el nombre seleccionados.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </article>

      {selectedStudent ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={closeEditor}>
          <section
            className="career-modal student-update-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="student-update-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="career-modal-header student-update-modal-header">
              <div>
                <span>ACTUALIZAR ESTUDIANTE</span>
                <h3 id="student-update-modal-title">{selectedStudent.nombre}</h3>
                <p>
                  Código {selectedStudent.codigo_estud}
                  {selectedCut ? ` · ${cutLabel(selectedCut)}` : ''}
                </p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={closeEditor} disabled={isSaving}>
                Cerrar
              </button>
            </header>

            <div className="career-modal-body student-update-modal-body">
              {error ? <p className="form-error">{error}</p> : null}
              {message ? <p className="status-message success">{message}</p> : null}

              <form className="admin-form-grid student-update-form" onSubmit={handleSave}>
                <label className="field full-span">
                  <span>Nombres y apellidos *</span>
                  <input name="nombre" value={form.nombre} onChange={updateField} maxLength="70" required />
                </label>
                <label className="field">
                  <span>Identificación *</span>
                  <input name="cedula" value={form.cedula} onChange={updateField} maxLength="50" required />
                </label>
                <label className="field">
                  <span>Fecha de nacimiento</span>
                  <input type="date" name="fecha_nacimiento" value={form.fecha_nacimiento} onChange={updateField} />
                </label>
                <label className="field">
                  <span>Correo personal</span>
                  <input type="email" name="correo_personal" value={form.correo_personal} onChange={updateField} maxLength="80" />
                </label>
                <label className="field">
                  <span>Correo INTEC</span>
                  <input type="email" name="correo_intec" value={form.correo_intec} onChange={updateField} maxLength="100" />
                </label>
                <label className="field">
                  <span>Teléfono</span>
                  <input name="telefono" value={form.telefono} onChange={updateField} maxLength="30" />
                </label>
                <label className="field">
                  <span>Móvil</span>
                  <input name="movil" value={form.movil} onChange={updateField} maxLength="15" />
                </label>
                <label className="field">
                  <span>Ciudad</span>
                  <input name="ciudad" value={form.ciudad} onChange={updateField} maxLength="70" />
                </label>
                <label className="field">
                  <span>Sexo</span>
                  <select name="sexo" value={form.sexo} onChange={updateField}>
                    <option value="">Sin especificar</option>
                    <option value="1">Masculino</option>
                    <option value="2">Femenino</option>
                    <option value="3">Otro</option>
                  </select>
                </label>
                <label className="field full-span">
                  <span>Dirección</span>
                  <textarea name="direccion" value={form.direccion} onChange={updateField} maxLength="150" rows="3" />
                </label>
                <div className="student-selection-actions full-span">
                  <button
                    type="button"
                    className="ghost-button compact-button"
                    disabled={isSaving}
                    onClick={() => setForm(formFromStudent(selectedStudent))}
                  >
                    Restablecer
                  </button>
                  <button type="submit" className="submit-button compact-button" disabled={!hasChanges || isSaving}>
                    {isSaving ? 'Guardando...' : 'Guardar actualización'}
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
