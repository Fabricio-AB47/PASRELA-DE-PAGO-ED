import { useCallback, useEffect, useMemo, useState } from 'react'
import { downloadBlobResponse, readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const MAX_LOCAL_LOGO_SIZE = 2 * 1024 * 1024
const MAX_LOCAL_BACKGROUND_SIZE = 6 * 1024 * 1024
const numberFormatter = new Intl.NumberFormat('es-EC')
const TEMPLATE_TYPE_OPTIONS = [
  { value: 'EDUCACION_CONTINUA', label: 'Educación continua' },
  { value: 'REGULAR', label: 'Regular' },
]

function cutLabel(cut) {
  const name = cut.nombre_corte || `Corte ${cut.numero_corte || cut.corte_id}`
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || ''
  return subject ? `${name} - ${subject}` : name
}

export default function AdminCertificateTemplatePanel() {
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [isPreviewLoading, setIsPreviewLoading] = useState(false)
  const [useDefaultLogo, setUseDefaultLogo] = useState(true)
  const [showComplementLogos, setShowComplementLogos] = useState(true)
  const [logos, setLogos] = useState([])
  const [newLogos, setNewLogos] = useState([])
  const [removeLogoIds, setRemoveLogoIds] = useState([])
  const [backgrounds, setBackgrounds] = useState([])
  const [newBackgrounds, setNewBackgrounds] = useState([])
  const [removeBackgroundIds, setRemoveBackgroundIds] = useState([])
  const [selectedTemplateType, setSelectedTemplateType] = useState('EDUCACION_CONTINUA')
  const [selectedBackgroundId, setSelectedBackgroundId] = useState('')
  const [selectedLogoIds, setSelectedLogoIds] = useState([])
  const [previewUrl, setPreviewUrl] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [studentQuery, setStudentQuery] = useState('')
  const [certificateResult, setCertificateResult] = useState(null)
  const [selectedStudentIds, setSelectedStudentIds] = useState([])
  const [isLoadingCuts, setIsLoadingCuts] = useState(true)
  const [isLoadingStudents, setIsLoadingStudents] = useState(false)
  const [isGeneratingCertificates, setIsGeneratingCertificates] = useState(false)
  const [downloadingStudentId, setDownloadingStudentId] = useState('')
  const totalLogos = logos.length + newLogos.length
  const totalBackgrounds = backgrounds.length + newBackgrounds.length
  const students = useMemo(() => certificateResult?.students || [], [certificateResult])
  const certificateMetrics = certificateResult?.metrics || {}
  const selectedStudents = useMemo(
    () => students.filter((student) => selectedStudentIds.includes(student.corte_estudiante_id)),
    [selectedStudentIds, students],
  )
  const backgroundOptions = useMemo(() => [
    ...backgrounds.map((background) => ({
      id: background.id,
      label: background.display_name || background.filename || 'Fondo de certificado',
      pending: false,
    })),
    ...newBackgrounds.map((background) => ({
      id: background.local_id,
      label: `${background.display_name || background.name || 'Fondo nuevo'} (nuevo)`,
      pending: true,
    })),
  ], [backgrounds, newBackgrounds])
  const cutLogoOptions = useMemo(() => [
    ...logos
      .filter((logo) => logo.enabled !== false)
      .map((logo) => ({
        id: logo.id,
        label: logo.display_name || logo.filename || 'Logo',
        pending: false,
      })),
    ...newLogos.map((logo) => ({
      id: logo.local_id,
      label: `${logo.display_name || logo.name || 'Logo nuevo'} (nuevo)`,
      pending: true,
    })),
  ], [logos, newLogos])

  const loadPreview = useCallback(async (corteId = '') => {
    setIsPreviewLoading(true)
    try {
      const params = new URLSearchParams({ t: String(Date.now()) })
      if (corteId) {
        params.set('corte_id', corteId)
      }
      const response = await adminFetch(`/api/auth/admin/certificate-template/preview/?${params.toString()}`)
      if (!response.ok) {
        const payload = await readResponsePayload(response)
        throw new Error(payload?.message ?? `No fue posible generar la previsualización (${response.status}).`)
      }
      const blob = await response.blob()
      const objectUrl = window.URL.createObjectURL(blob)
      setPreviewUrl((current) => {
        if (current) {
          window.URL.revokeObjectURL(current)
        }
        return objectUrl
      })
    } catch (previewError) {
      setError(previewError.message)
    } finally {
      setIsPreviewLoading(false)
    }
  }, [])

  const loadConfig = useCallback(async (corteId = '') => {
    setIsLoading(true)
    setError('')
    setMessage('')
    try {
      const params = new URLSearchParams()
      if (corteId) {
        params.set('corte_id', corteId)
      }
      const response = await adminFetch(`/api/auth/admin/certificate-template/${params.toString() ? `?${params.toString()}` : ''}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.config) {
        throw new Error(payload?.message ?? `No fue posible cargar la plantilla (${response.status}).`)
      }
      applyConfig(payload.config)
      await loadPreview(corteId)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setIsLoading(false)
    }
  }, [loadPreview])

  const loadCertificateStudents = useCallback(async (corteId, searchTerm = '') => {
    if (!corteId) {
      setCertificateResult(null)
      setSelectedStudentIds([])
      return
    }

    setIsLoadingStudents(true)
    setError('')
    try {
      const params = new URLSearchParams({ corte_id: corteId })
      if (searchTerm.trim()) {
        params.set('q', searchTerm.trim())
      }
      const response = await adminFetch(`/api/auth/admin/certificates/students/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar estudiantes (${response.status}).`)
      }
      setCertificateResult(payload.result)
      setSelectedStudentIds([])
    } catch (loadError) {
      setCertificateResult(null)
      setSelectedStudentIds([])
      setError(loadError.message)
    } finally {
      setIsLoadingStudents(false)
    }
  }, [])

  const loadCuts = useCallback(async () => {
    setIsLoadingCuts(true)
    setError('')
    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/')
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible cargar cortes (${response.status}).`)
      }
      const loadedCuts = payload.cuts || []
      const initialCutId = loadedCuts[0]?.corte_id || ''
      setCuts(loadedCuts)
      setSelectedCutId(initialCutId)
      if (initialCutId) {
        await Promise.all([
          loadCertificateStudents(initialCutId),
          loadConfig(initialCutId),
        ])
      }
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setIsLoadingCuts(false)
    }
  }, [loadCertificateStudents, loadConfig])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      loadConfig()
      loadCuts()
    }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [loadConfig, loadCuts])

  useEffect(() => (
    () => {
      if (previewUrl) {
        window.URL.revokeObjectURL(previewUrl)
      }
    }
  ), [previewUrl])

  function applyConfig(config) {
    const loadedBackgrounds = config.backgrounds || []
    const cutSetting = config.cut_setting || {}
    const defaultBackgroundId = config.default_background_id || loadedBackgrounds[0]?.id || ''
    setUseDefaultLogo(Boolean(cutSetting.use_default_logo ?? config.use_default_logo))
    setShowComplementLogos(Boolean(cutSetting.show_complement_logos ?? config.show_complement_logos))
    setLogos(config.logos || [])
    setNewLogos([])
    setRemoveLogoIds([])
    setBackgrounds(loadedBackgrounds)
    setNewBackgrounds([])
    setRemoveBackgroundIds([])
    setSelectedTemplateType(cutSetting.template_type || 'EDUCACION_CONTINUA')
    setSelectedBackgroundId(cutSetting.background_id || defaultBackgroundId)
    setSelectedLogoIds(cutSetting.logo_ids || [])
  }

  function handleCutChange(event) {
    const nextCutId = event.target.value
    setSelectedCutId(nextCutId)
    loadConfig(nextCutId)
    loadCertificateStudents(nextCutId, studentQuery)
  }

  function handleStudentSearch(event) {
    event.preventDefault()
    loadCertificateStudents(selectedCutId, studentQuery)
  }

  function toggleStudent(studentId) {
    setSelectedStudentIds((current) => (
      current.includes(studentId)
        ? current.filter((item) => item !== studentId)
        : [...current, studentId]
    ))
  }

  function toggleAllAvailable() {
    const availableIds = students
      .filter((student) => student.certificado_disponible)
      .map((student) => student.corte_estudiante_id)
    const allSelected = availableIds.length > 0 && availableIds.every((studentId) => selectedStudentIds.includes(studentId))
    setSelectedStudentIds(allSelected ? [] : availableIds)
  }

  async function handleGenerateCertificates() {
    if (!selectedCutId || !selectedStudentIds.length) {
      setError('Selecciona una corte y al menos un estudiante aprobado.')
      return
    }

    setIsGeneratingCertificates(true)
    setError('')
    setMessage('')
    try {
      const response = await adminFetch('/api/auth/admin/certificates/generate/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          corte_id: selectedCutId,
          corte_estudiante_ids: selectedStudentIds,
          q: studentQuery,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible generar certificados (${response.status}).`)
      }
      setCertificateResult(payload.result.updated)
      setSelectedStudentIds([])
      const summary = payload.result.summary || {}
      setMessage(`Certificados generados: ${formatNumber(summary.generados)}. Errores: ${formatNumber(summary.errores)}.`)
    } catch (generateError) {
      setError(generateError.message)
    } finally {
      setIsGeneratingCertificates(false)
    }
  }

  async function handleDownloadCertificate(student) {
    setDownloadingStudentId(student.corte_estudiante_id)
    setError('')
    try {
      const params = new URLSearchParams({
        corte_id: selectedCutId,
        corte_estudiante_id: student.corte_estudiante_id,
      })
      const response = await adminFetch(`/api/auth/admin/certificates/download/?${params.toString()}`)
      if (!response.ok) {
        const payload = await readResponsePayload(response)
        throw new Error(payload?.message ?? `No fue posible descargar el certificado (${response.status}).`)
      }
      await downloadBlobResponse(response, `certificado_${student.codigo_estud || student.corte_estudiante_id}.pdf`)
    } catch (downloadError) {
      setError(downloadError.message)
    } finally {
      setDownloadingStudentId('')
    }
  }

  async function handleFileChange(event) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length) {
      return
    }

    setError('')
    try {
      const payloads = await Promise.all(files.map(fileToLogoPayload))
      setNewLogos((current) => [...current, ...payloads])
      setSelectedLogoIds((current) => {
        const nextIds = payloads.map((logo) => logo.local_id).filter(Boolean)
        return [...new Set([...current, ...nextIds])]
      })
    } catch (fileError) {
      setError(fileError.message)
    }
  }

  async function handleBackgroundFileChange(event) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length) {
      return
    }

    setError('')
    try {
      const payloads = await Promise.all(files.map(fileToBackgroundPayload))
      setNewBackgrounds((current) => [...current, ...payloads])
      if (!selectedBackgroundId && payloads[0]?.local_id) {
        setSelectedBackgroundId(payloads[0].local_id)
      }
    } catch (fileError) {
      setError(fileError.message)
    }
  }

  function updateLogo(logoId, field, value) {
    setLogos((current) => current.map((logo) => (
      logo.id === logoId ? { ...logo, [field]: value } : logo
    )))
  }

  function updateNewLogo(localId, field, value) {
    setNewLogos((current) => current.map((logo) => (
      logo.local_id === localId ? { ...logo, [field]: value } : logo
    )))
  }

  function removeExistingLogo(logoId) {
    setLogos((current) => current.filter((logo) => logo.id !== logoId))
    setRemoveLogoIds((current) => (current.includes(logoId) ? current : [...current, logoId]))
    setSelectedLogoIds((current) => current.filter((item) => item !== logoId))
  }

  function removePendingLogo(localId) {
    setNewLogos((current) => current.filter((logo) => logo.local_id !== localId))
    setSelectedLogoIds((current) => current.filter((item) => item !== localId))
  }

  function updateBackground(backgroundId, field, value) {
    setBackgrounds((current) => current.map((background) => (
      background.id === backgroundId ? { ...background, [field]: value } : background
    )))
  }

  function updateNewBackground(localId, field, value) {
    setNewBackgrounds((current) => current.map((background) => (
      background.local_id === localId ? { ...background, [field]: value } : background
    )))
  }

  function removeExistingBackground(background) {
    if (background.built_in) {
      return
    }
    setBackgrounds((current) => current.filter((item) => item.id !== background.id))
    setRemoveBackgroundIds((current) => (current.includes(background.id) ? current : [...current, background.id]))
    if (selectedBackgroundId === background.id) {
      setSelectedBackgroundId('')
    }
  }

  function removePendingBackground(localId) {
    setNewBackgrounds((current) => current.filter((background) => background.local_id !== localId))
    if (selectedBackgroundId === localId) {
      setSelectedBackgroundId(backgrounds[0]?.id || '')
    }
  }

  function toggleCutLogo(logoId) {
    setSelectedLogoIds((current) => (
      current.includes(logoId)
        ? current.filter((item) => item !== logoId)
        : [...current, logoId]
    ))
  }

  async function handleSave(event) {
    event.preventDefault()
    setIsSaving(true)
    setError('')
    setMessage('')
    try {
      const response = await adminFetch('/api/auth/admin/certificate-template/save/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          use_default_logo: useDefaultLogo,
          show_complement_logos: showComplementLogos,
          default_background_id: selectedBackgroundId || backgrounds[0]?.id || '',
          corte_id: selectedCutId,
          template_type: selectedTemplateType,
          background_id: selectedBackgroundId,
          logo_ids: selectedLogoIds,
          cut_use_default_logo: useDefaultLogo,
          cut_show_complement_logos: showComplementLogos,
          logos,
          new_logos: newLogos,
          remove_logo_ids: removeLogoIds,
          backgrounds,
          new_backgrounds: newBackgrounds,
          remove_background_ids: removeBackgroundIds,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.config) {
        throw new Error(payload?.message ?? `No fue posible guardar la plantilla (${response.status}).`)
      }
      applyConfig(payload.config)
      setMessage(payload.message || 'Plantilla guardada.')
      await loadPreview(selectedCutId)
    } catch (saveError) {
      setError(saveError.message)
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando certificados</h3>
          <p>Estamos consultando la plantilla vigente.</p>
        </div>
      </article>
    )
  }

  return (
    <section className="teacher-panel certificate-template-panel" aria-labelledby="certificate-template-title">
      <div className="admin-section-heading">
        <div>
          <span className="eyebrow">Administrativo</span>
          <h3 id="certificate-template-title">Certificado</h3>
        </div>
        <button type="button" className="ghost-button compact-button" onClick={() => loadConfig(selectedCutId)} disabled={isSaving || isPreviewLoading}>
          Actualizar
        </button>
        <button
          type="button"
          className="ghost-button compact-button"
          onClick={() => document.getElementById('certificate-template-config')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
        >
          Configurar plantilla y logos
        </button>
      </div>

      {message ? <p className="form-success">{message}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}

      <article className="module-card teacher-panel-card">
        <div className="module-card-header">
          <div>
            <h4>Certificado por corte</h4>
            <p>Genera y adjunta el certificado al estudiante matriculado en la corte seleccionada.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => loadCertificateStudents(selectedCutId, studentQuery)}
            disabled={!selectedCutId || isLoadingStudents}
          >
            Actualizar
          </button>
        </div>

        <form className="admin-form-grid" onSubmit={handleStudentSearch}>
          <label className="field">
            <span>Corte</span>
            <select value={selectedCutId} onChange={handleCutChange} disabled={isLoadingCuts}>
              <option value="">Selecciona una corte</option>
              {cuts.map((cut) => (
                <option key={cut.corte_id} value={cut.corte_id}>
                  {cutLabel(cut)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Buscar estudiante</span>
            <input
              type="search"
              value={studentQuery}
              onChange={(event) => setStudentQuery(event.target.value)}
              placeholder="Nombre, cédula o código"
            />
          </label>
          <div className="student-selection-actions full-span">
            <button type="submit" className="submit-button compact-button" disabled={!selectedCutId || isLoadingStudents}>
              Buscar
            </button>
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={handleGenerateCertificates}
              disabled={!selectedStudents.length || isGeneratingCertificates}
            >
              {isGeneratingCertificates ? 'Generando...' : `Generar seleccionados (${formatNumber(selectedStudents.length)})`}
            </button>
          </div>
        </form>

        {certificateResult ? (
          <section className="bulk-summary-grid enrollment-summary-grid" aria-label="Resumen de certificados">
            <div>
              <span>Matriculados</span>
              <strong>{formatNumber(certificateMetrics.total)}</strong>
            </div>
            <div>
              <span>Disponibles</span>
              <strong>{formatNumber(certificateMetrics.certificados_disponibles)}</strong>
            </div>
            <div>
              <span>Generados</span>
              <strong>{formatNumber(certificateMetrics.certificados_generados)}</strong>
            </div>
            <div>
              <span>Pendientes</span>
              <strong>{formatNumber(certificateMetrics.pendientes_certificado)}</strong>
            </div>
          </section>
        ) : null}

        <div className="admin-table-wrap">
          <table className="admin-table course-cut-table enrollment-table">
            <thead>
              <tr>
                <th>
                  <button type="button" className="table-link-button" onClick={toggleAllAvailable}>
                    Sel.
                  </button>
                </th>
                <th>Estudiante</th>
                <th>Cédula</th>
                <th>Nota</th>
                <th>Asistencia</th>
                <th>Certificado</th>
                <th>Acción</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingStudents ? (
                <tr>
                  <td colSpan="7">Cargando estudiantes...</td>
                </tr>
              ) : students.length ? (
                students.map((student) => (
                  <tr key={student.corte_estudiante_id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedStudentIds.includes(student.corte_estudiante_id)}
                        disabled={!student.certificado_disponible}
                        onChange={() => toggleStudent(student.corte_estudiante_id)}
                      />
                    </td>
                    <td>
                      <strong>{student.nombre}</strong>
                      <span>Código {student.codigo_estud || '-'}</span>
                    </td>
                    <td>{student.cedula || '-'}</td>
                    <td>{formatDecimal(student.nota_final)}</td>
                    <td>{student.porcentaje_asistencia !== null ? `${formatDecimal(student.porcentaje_asistencia)}%` : '-'}</td>
                    <td>
                      <strong>{student.certificado_estado || '-'}</strong>
                      <span>{student.certificado?.numero_certificado || '-'}</span>
                    </td>
                    <td>
                      {student.certificado ? (
                        <button
                          type="button"
                          className="ghost-button compact-button table-action-button"
                          onClick={() => handleDownloadCertificate(student)}
                          disabled={downloadingStudentId === student.corte_estudiante_id}
                        >
                          {downloadingStudentId === student.corte_estudiante_id ? 'Descargando...' : 'Descargar'}
                        </button>
                      ) : (
                        '-'
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="7">No hay estudiantes matriculados para la corte seleccionada.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>

      <form id="certificate-template-config" className="module-card teacher-panel-card certificate-template-form" onSubmit={handleSave}>
        <div className="admin-form-grid">
          <label className="field">
            <span>Formato</span>
            <select
              value={selectedTemplateType}
              onChange={(event) => setSelectedTemplateType(event.target.value)}
            >
              {TEMPLATE_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Fondo</span>
            <select
              value={selectedBackgroundId}
              onChange={(event) => setSelectedBackgroundId(event.target.value)}
            >
              <option value="">Sin fondo seleccionado</option>
              {backgroundOptions.map((background) => (
                <option key={background.id} value={background.id}>
                  {background.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="certificate-template-options">
          <label className="check-row">
            <input
              type="checkbox"
              checked={useDefaultLogo}
              onChange={(event) => setUseDefaultLogo(event.target.checked)}
            />
            <span>Logo INTEC por defecto</span>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={showComplementLogos}
              onChange={(event) => setShowComplementLogos(event.target.checked)}
            />
            <span>Logos complementarios en el lado derecho</span>
          </label>
        </div>

        {cutLogoOptions.length ? (
          <section className="certificate-cut-logo-list" aria-label="Logos asignados a la corte">
            {cutLogoOptions.map((logo) => (
              <label key={logo.id} className="check-row certificate-cut-logo-option">
                <input
                  type="checkbox"
                  checked={selectedLogoIds.includes(logo.id)}
                  onChange={() => toggleCutLogo(logo.id)}
                />
                <span>{logo.label}</span>
              </label>
            ))}
          </section>
        ) : null}

        <label className="field">
          <span>Agregar logos para el lado derecho (máximo 5)</span>
          <input
            type="file"
            accept="image/png,image/jpeg"
            multiple
            onChange={handleFileChange}
          />
        </label>

        <label className="field">
          <span>Agregar fondo de certificado</span>
          <input
            type="file"
            accept="image/png,image/jpeg"
            multiple
            onChange={handleBackgroundFileChange}
          />
        </label>

        <div className="certificate-logo-list" aria-label="Logos del certificado">
          {logos.map((logo) => (
            <article key={logo.id} className="certificate-logo-row">
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={Boolean(logo.enabled)}
                  onChange={(event) => updateLogo(logo.id, 'enabled', event.target.checked)}
                />
                <span>Activo</span>
              </label>
              <label className="field">
                <span>Nombre</span>
                <input
                  type="text"
                  value={logo.display_name || ''}
                  onChange={(event) => updateLogo(logo.id, 'display_name', event.target.value)}
                />
              </label>
              <div className="certificate-logo-meta">
                <strong>{logo.filename}</strong>
                <span>{formatBytes(logo.size_bytes)}</span>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => removeExistingLogo(logo.id)}>
                Quitar
              </button>
            </article>
          ))}

          {newLogos.map((logo) => (
            <article key={logo.local_id} className="certificate-logo-row is-pending">
              <span className="cut-status-badge is-open">Nuevo</span>
              <label className="field">
                <span>Nombre</span>
                <input
                  type="text"
                  value={logo.display_name || ''}
                  onChange={(event) => updateNewLogo(logo.local_id, 'display_name', event.target.value)}
                />
              </label>
              <div className="certificate-logo-meta">
                <strong>{logo.name}</strong>
                <span>{formatBytes(logo.size_bytes)}</span>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => removePendingLogo(logo.local_id)}>
                Quitar
              </button>
            </article>
          ))}

          {!totalLogos ? (
            <p className="teacher-panel-empty">El certificado usará únicamente el diseño INTEC por defecto.</p>
          ) : null}
        </div>

        <div className="certificate-logo-list" aria-label="Fondos del certificado">
          {backgrounds.map((background) => (
            <article key={background.id} className="certificate-logo-row certificate-background-row">
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={Boolean(background.enabled)}
                  disabled={Boolean(background.built_in)}
                  onChange={(event) => updateBackground(background.id, 'enabled', event.target.checked)}
                />
                <span>{background.built_in ? 'Formato base' : 'Activo'}</span>
              </label>
              <label className="field">
                <span>Nombre</span>
                <input
                  type="text"
                  value={background.display_name || ''}
                  onChange={(event) => updateBackground(background.id, 'display_name', event.target.value)}
                />
              </label>
              <div className="certificate-logo-meta">
                <strong>{background.filename}</strong>
                <span>{formatBytes(background.size_bytes)}</span>
              </div>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => removeExistingBackground(background)}
                disabled={Boolean(background.built_in)}
              >
                Quitar
              </button>
            </article>
          ))}

          {newBackgrounds.map((background) => (
            <article key={background.local_id} className="certificate-logo-row certificate-background-row is-pending">
              <span className="cut-status-badge is-open">Nuevo</span>
              <label className="field">
                <span>Nombre</span>
                <input
                  type="text"
                  value={background.display_name || ''}
                  onChange={(event) => updateNewBackground(background.local_id, 'display_name', event.target.value)}
                />
              </label>
              <div className="certificate-logo-meta">
                <strong>{background.name}</strong>
                <span>{formatBytes(background.size_bytes)}</span>
              </div>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => removePendingBackground(background.local_id)}
              >
                Quitar
              </button>
            </article>
          ))}

          {!totalBackgrounds ? (
            <p className="teacher-panel-empty">No hay fondos registrados para certificados.</p>
          ) : null}
        </div>

        <div className="student-selection-actions">
          <button type="submit" className="submit-button compact-button" disabled={isSaving}>
            {isSaving ? 'Guardando...' : 'Guardar plantilla'}
          </button>
          <button type="button" className="ghost-button compact-button" onClick={() => loadPreview(selectedCutId)} disabled={isSaving || isPreviewLoading}>
            {isPreviewLoading ? 'Generando...' : 'Previsualizar'}
          </button>
        </div>
        {message ? <p className="form-success" role="status">{message}</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
      </form>

      <article className="module-card teacher-panel-card certificate-preview-card">
        <div className="module-card-header">
          <div>
            <h4>Previsualización</h4>
            <p>Vista PDF con la plantilla vigente.</p>
          </div>
        </div>
        {previewUrl ? (
          <iframe className="certificate-preview-frame" src={previewUrl} title="Previsualización de certificado" />
        ) : (
          <p className="teacher-panel-empty">No hay previsualización generada.</p>
        )}
      </article>
    </section>
  )
}

function fileToLogoPayload(file) {
  if (!['image/png', 'image/jpeg'].includes(file.type)) {
    return Promise.reject(new Error('Solo se permiten logos en formato PNG o JPG.'))
  }
  if (file.size > MAX_LOCAL_LOGO_SIZE) {
    return Promise.reject(new Error('Cada logo debe pesar máximo 2 MB.'))
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      resolve({
        local_id: `${file.name}-${file.lastModified}-${file.size}`,
        name: file.name,
        display_name: file.name.replace(/\.[^.]+$/, ''),
        content_type: file.type,
        size_bytes: file.size,
        data_url: String(reader.result || ''),
      })
    }
    reader.onerror = () => reject(new Error('No fue posible leer el logo seleccionado.'))
    reader.readAsDataURL(file)
  })
}

function fileToBackgroundPayload(file) {
  if (!['image/png', 'image/jpeg'].includes(file.type)) {
    return Promise.reject(new Error('Solo se permiten fondos en formato PNG o JPG.'))
  }
  if (file.size > MAX_LOCAL_BACKGROUND_SIZE) {
    return Promise.reject(new Error('Cada fondo debe pesar máximo 6 MB.'))
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      resolve({
        local_id: `${file.name}-${file.lastModified}-${file.size}`,
        name: file.name,
        display_name: file.name.replace(/\.[^.]+$/, ''),
        content_type: file.type,
        size_bytes: file.size,
        data_url: String(reader.result || ''),
      })
    }
    reader.onerror = () => reject(new Error('No fue posible leer el fondo seleccionado.'))
    reader.readAsDataURL(file)
  })
}

function formatBytes(value) {
  const size = Number(value || 0)
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(2)} MB`
  }
  if (size >= 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${size} B`
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
