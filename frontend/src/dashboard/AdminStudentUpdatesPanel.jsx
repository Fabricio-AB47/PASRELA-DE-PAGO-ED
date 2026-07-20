import { useCallback, useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'
import ListExportModal from './ListExportModal.jsx'
import { downloadPeopleList } from './listExports.js'

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
  const [modalMode, setModalMode] = useState('edit')
  const [form, setForm] = useState(EMPTY_FORM)
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [canViewCredentials, setCanViewCredentials] = useState(false)
  const [credentials, setCredentials] = useState(null)
  const [isLoadingCredentials, setIsLoadingCredentials] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [credentialError, setCredentialError] = useState('')
  const [copiedCredential, setCopiedCredential] = useState('')
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
      const params = new URLSearchParams({ corte_id: corteId, limit: '500' })
      if (search.trim()) params.set('q', search.trim())
      const response = await adminFetch(`/api/auth/admin/student-updates/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!response.ok || !payload?.ok || !payload.result) {
        throw new Error(payload?.message || `No fue posible cargar estudiantes (${response.status}).`)
      }
      setStudents(payload.result.students || [])
      setCanViewCredentials(Boolean(payload.result.can_view_credentials))
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

  function selectStudent(student, mode = 'edit') {
    setSelectedStudent(student)
    setModalMode(mode)
    setForm(formFromStudent(student))
    setError('')
    setMessage('')
    setCredentials(null)
    setShowPassword(false)
    setCredentialError('')
    setCopiedCredential('')
    if (mode === 'view' && canViewCredentials) void loadMigrationCredentials(student)
  }

  function closeEditor() {
    if (isSaving) return
    setSelectedStudent(null)
    setForm(EMPTY_FORM)
    setError('')
    setMessage('')
    setCredentials(null)
    setShowPassword(false)
    setCredentialError('')
    setCopiedCredential('')
  }

  async function loadMigrationCredentials(student = selectedStudent) {
    if (!student || !canViewCredentials) return
    setIsLoadingCredentials(true)
    setCredentialError('')
    setCopiedCredential('')
    try {
      const response = await adminFetch('/api/auth/admin/student-updates/credentials/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          corte_id: student.corte_id,
          codigo_estud: student.codigo_estud,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!response.ok || !payload?.ok || !payload.credentials) {
        throw new Error(payload?.message || `No fue posible cargar las credenciales (${response.status}).`)
      }
      setCredentials(payload.credentials)
      setShowPassword(false)
    } catch (loadError) {
      setCredentialError(loadError.message)
    } finally {
      setIsLoadingCredentials(false)
    }
  }

  async function copyCredential(label, value) {
    if (!value) return
    try {
      await navigator.clipboard.writeText(value)
      setCopiedCredential(label)
    } catch {
      setCredentialError('No fue posible copiar el dato. Selecciónalo manualmente.')
    }
  }

  function updateField(event) {
    const { name, value } = event.target
    setForm((current) => ({
      ...current,
      [name]: name === 'nombre' ? value.toLocaleUpperCase('es-EC') : value,
    }))
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
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => setIsExportOpen(true)}
            disabled={isLoading || !students.length}
          >
            Descargar listado
          </button>
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
                  <td className="student-update-row-actions">
                    <button type="button" className="ghost-button compact-button" onClick={() => selectStudent(student, 'view')}>
                      Ver
                    </button>
                    <button type="button" className="ghost-button compact-button" onClick={() => selectStudent(student, 'edit')}>
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
                <span>{modalMode === 'view' ? 'INFORMACIÓN DEL ESTUDIANTE' : 'ACTUALIZAR ESTUDIANTE'}</span>
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

              {modalMode === 'view' ? (
                <div className="student-view-content">
                  <div className="student-view-grid">
                    <div className="student-view-wide"><span>Nombres y apellidos</span><strong>{selectedStudent.nombre || '-'}</strong></div>
                    <div><span>Código estudiantil</span><strong>{selectedStudent.codigo_estud || '-'}</strong></div>
                    <div><span>Identificación</span><strong>{selectedStudent.cedula || '-'}</strong></div>
                    <div><span>Fecha de nacimiento</span><strong>{selectedStudent.fecha_nacimiento || '-'}</strong></div>
                    <div><span>Sexo</span><strong>{selectedStudent.sexo === '1' ? 'Masculino' : selectedStudent.sexo === '2' ? 'Femenino' : selectedStudent.sexo === '3' ? 'Otro' : '-'}</strong></div>
                    <div className="student-view-wide"><span>Correo personal</span><strong>{selectedStudent.correo_personal || '-'}</strong></div>
                    <div className="student-view-wide"><span>Correo institucional</span><strong>{selectedStudent.correo_intec || '-'}</strong></div>
                    <div><span>Teléfono</span><strong>{selectedStudent.telefono || '-'}</strong></div>
                    <div><span>Móvil</span><strong>{selectedStudent.movil || '-'}</strong></div>
                    <div><span>Ciudad</span><strong>{selectedStudent.ciudad || '-'}</strong></div>
                    <div className="student-view-wide"><span>Dirección</span><strong>{selectedStudent.direccion || '-'}</strong></div>
                    <div className="student-view-full"><span>Cohorte</span><strong>{selectedCut ? cutLabel(selectedCut) : `Cohorte ${selectedStudent.corte_id}`}</strong></div>
                  </div>

                  {canViewCredentials ? (
                    <section className="student-migration-credentials" aria-label="Credenciales para migración">
                      <div className="student-migration-header">
                        <div>
                          <h4>Cuenta registrada en CorreosEstudIntec</h4>
                          <p>Correo personal, correo institucional y datos de migración.</p>
                        </div>
                        {!credentials ? (
                          <button type="button" className="ghost-button compact-button" disabled={isLoadingCredentials} onClick={() => loadMigrationCredentials()}>
                            {isLoadingCredentials ? 'Consultando...' : 'Consultar credenciales'}
                          </button>
                        ) : null}
                      </div>
                      {credentialError ? <p className="form-error">{credentialError}</p> : null}
                      {isLoadingCredentials ? <p className="student-view-loading">Cargando información de CorreosEstudIntec...</p> : null}
                      {credentials ? (
                        <>
                          <div className="student-migration-grid">
                            <div><span>Nombres registrados</span><strong>{credentials.nombres || '-'}</strong></div>
                            <div><span>Código</span><strong>{credentials.codigo_estud || '-'}</strong></div>
                            <div><span>Estado</span><strong>{credentials.estado || '-'}</strong></div>
                            <div className="student-migration-wide"><span>Correo personal</span><strong>{credentials.correo_personal || '-'}</strong><button type="button" onClick={() => copyCredential('correo personal', credentials.correo_personal)}>Copiar</button></div>
                            <div className="student-migration-wide"><span>Correo institucional</span><strong>{credentials.correo_intec || '-'}</strong><button type="button" onClick={() => copyCredential('correo institucional', credentials.correo_intec)}>Copiar</button></div>
                            <div className="student-migration-password student-migration-wide">
                              <span>Contraseña registrada</span>
                              <input type="text" value={credentials.password || ''} readOnly autoComplete="off" aria-label="Contraseña registrada del estudiante" />
                              <div><button type="button" onClick={() => copyCredential('contraseña', credentials.password)}>Copiar contraseña</button></div>
                            </div>
                            <div><span>Fecha de registro</span><strong>{credentials.fecha || '-'}</strong></div>
                            <div><span>Período</span><strong>{credentials.periodo || '-'}</strong></div>
                            <div><span>Correo enviado</span><strong>{credentials.correo_enviado || '-'}</strong></div>
                            <div><span>Último acceso Moodle</span><strong>{credentials.ultimo_acceso_moodle || '-'}</strong></div>
                            <div><span>Número de migración</span><strong>{credentials.numero_migracion || '-'}</strong></div>
                            <div><span>Tipo de curso</span><strong>{credentials.tipo_curso_migracion || '-'}</strong></div>
                            <div className="student-migration-wide"><span>Descripción</span><strong>{credentials.descripcion || '-'}</strong></div>
                          </div>
                          {copiedCredential ? <p className="student-migration-copy-status">{copiedCredential} copiado.</p> : null}
                        </>
                      ) : null}
                    </section>
                  ) : null}
                </div>
              ) : (
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
                {canViewCredentials ? (
                  <section className="student-migration-credentials full-span" aria-label="Credenciales para migración">
                    <div className="student-migration-header">
                      <div>
                        <h4>Cuenta registrada en CorreosEstudIntec</h4>
                        <p>Información restringida para la migración de la cuenta institucional.</p>
                      </div>
                      {!credentials ? (
                        <button
                          type="button"
                          className="ghost-button compact-button"
                          disabled={isLoadingCredentials}
                          onClick={loadMigrationCredentials}
                        >
                          {isLoadingCredentials ? 'Consultando...' : 'Consultar credenciales'}
                        </button>
                      ) : null}
                    </div>
                    {credentialError ? <p className="form-error">{credentialError}</p> : null}
                    {credentials ? (
                      <>
                        <div className="student-migration-grid">
                          <div><span>Nombres</span><strong>{credentials.nombres || '-'}</strong></div>
                          <div><span>Código</span><strong>{credentials.codigo_estud || '-'}</strong></div>
                          <div className="student-migration-wide">
                            <span>Correo personal</span>
                            <strong>{credentials.correo_personal || '-'}</strong>
                            <button type="button" onClick={() => copyCredential('correo personal', credentials.correo_personal)}>Copiar</button>
                          </div>
                          <div className="student-migration-wide">
                            <span>Correo INTEC</span>
                            <strong>{credentials.correo_intec || '-'}</strong>
                            <button type="button" onClick={() => copyCredential('correo INTEC', credentials.correo_intec)}>Copiar</button>
                          </div>
                          <div className="student-migration-password student-migration-wide">
                            <span>Contraseña</span>
                            <input type={showPassword ? 'text' : 'password'} value={credentials.password || ''} readOnly autoComplete="off" />
                            <div>
                              <button type="button" onClick={() => setShowPassword((current) => !current)}>
                                {showPassword ? 'Ocultar' : 'Mostrar'}
                              </button>
                              <button type="button" onClick={() => copyCredential('contraseña', credentials.password)}>Copiar</button>
                            </div>
                          </div>
                          <div><span>Estado</span><strong>{credentials.estado || '-'}</strong></div>
                          <div><span>Fecha de registro</span><strong>{credentials.fecha || '-'}</strong></div>
                          <div><span>Período</span><strong>{credentials.periodo || '-'}</strong></div>
                          <div><span>Correo enviado</span><strong>{credentials.correo_enviado || '-'}</strong></div>
                          <div><span>Último acceso Moodle</span><strong>{credentials.ultimo_acceso_moodle || '-'}</strong></div>
                          <div><span>Número de migración</span><strong>{credentials.numero_migracion || '-'}</strong></div>
                          <div><span>Tipo de curso</span><strong>{credentials.tipo_curso_migracion || '-'}</strong></div>
                          <div className="student-migration-wide"><span>Descripción</span><strong>{credentials.descripcion || '-'}</strong></div>
                        </div>
                        {copiedCredential ? <p className="student-migration-copy-status">{copiedCredential} copiado.</p> : null}
                      </>
                    ) : null}
                  </section>
                ) : null}
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
              )}
            </div>
          </section>
        </div>
      ) : null}
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
