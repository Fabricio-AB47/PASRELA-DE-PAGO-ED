import { useEffect, useState } from 'react'
import { downloadBlobResponse, readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const FIXED_COD_ANIO_BASICA = '13'

const onlyActiveCourses = (courses) => (courses || []).filter((course) => course.es_activo !== false)

const removeNumbersFromLabel = (value) =>
  String(value || '')
    .replace(/[0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve(result.includes(',') ? result.split(',', 2)[1] : result)
    }
    reader.onerror = () => reject(new Error('No fue posible leer el archivo Excel.'))
    reader.readAsDataURL(file)
  })
}

export default function AdminBulkEnrollmentPanel() {
  const [catalogs, setCatalogs] = useState({
    carreras: [],
    periodos: [],
    cursos_por_carrera: {},
  })
  const [bulkForm, setBulkForm] = useState({
    carrera_num: '',
    cod_anio_basica: '',
    codigo_materia: '',
    codigo_periodo: '',
    estado_periodo: '',
    nombre_materia: '',
  })
  const [selectedFile, setSelectedFile] = useState(null)
  const [isLoadingCatalogs, setIsLoadingCatalogs] = useState(true)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isDownloadingTemplate, setIsDownloadingTemplate] = useState(false)
  const [downloadingCertificateKey, setDownloadingCertificateKey] = useState('')
  const [bulkError, setBulkError] = useState('')
  const [bulkMessage, setBulkMessage] = useState('')
  const [bulkResult, setBulkResult] = useState(null)

  useEffect(() => {
    let isMounted = true

    async function loadCatalogs() {
      try {
        const response = await adminFetch('/api/auth/inscription/catalogs/')
        const payload = await readResponsePayload(response)

        if (!payload || !response.ok || !payload.ok || !payload.catalogs) {
          throw new Error(payload?.message ?? `No fue posible cargar catálogos (${response.status}).`)
        }

        if (!isMounted) {
          return
        }

        const loadedCatalogs = payload.catalogs
        const fixedCareer = (loadedCatalogs.carreras || []).find(
          (item) => String(item.cod_anio_basica) === FIXED_COD_ANIO_BASICA,
        )
        const activeCareer = fixedCareer || (loadedCatalogs.carreras || []).find((item) => item.es_activo)
        const activeCourses = activeCareer
          ? onlyActiveCourses(loadedCatalogs.cursos_por_carrera?.[String(activeCareer.cod_anio_basica)])
          : []
        const defaultCourse = activeCourses[0] || null
        const activePeriod = (loadedCatalogs.periodos || []).find((item) => item.es_activo) || null

        setCatalogs(loadedCatalogs)
        setBulkForm({
          carrera_num: activeCareer?.num ? String(activeCareer.num) : '',
          cod_anio_basica: activeCareer?.cod_anio_basica ? String(activeCareer.cod_anio_basica) : '',
          codigo_materia: defaultCourse?.codigo_materia ? String(defaultCourse.codigo_materia) : '',
          codigo_periodo: activePeriod?.cod_periodo ? String(activePeriod.cod_periodo) : '',
          estado_periodo: activePeriod?.estado || '',
          nombre_materia: defaultCourse?.nombre_materia || '',
        })
      } catch (error) {
        if (isMounted) {
          setBulkError(error.message)
        }
      } finally {
        if (isMounted) {
          setIsLoadingCatalogs(false)
        }
      }
    }

    loadCatalogs()

    return () => {
      isMounted = false
    }
  }, [])

  function handleSelectionChange(event) {
    const { name, value } = event.target

    if (name === 'carrera_num') {
      const selectedCareer = catalogs.carreras.find((item) => String(item.num) === String(value))
      const codAnio = selectedCareer?.cod_anio_basica ? String(selectedCareer.cod_anio_basica) : ''
      const activeCourses = onlyActiveCourses(catalogs.cursos_por_carrera?.[codAnio])
      const firstCourse = activeCourses[0] || null
      setBulkForm((current) => ({
        ...current,
        carrera_num: String(value),
        cod_anio_basica: codAnio,
        codigo_materia: firstCourse?.codigo_materia ? String(firstCourse.codigo_materia) : '',
        nombre_materia: firstCourse?.nombre_materia || '',
      }))
      return
    }

    if (name === 'codigo_materia') {
      const activeCourses = onlyActiveCourses(catalogs.cursos_por_carrera?.[bulkForm.cod_anio_basica])
      const selectedCourse = activeCourses.find((item) => String(item.codigo_materia) === String(value))
      setBulkForm((current) => ({
        ...current,
        codigo_materia: String(value),
        nombre_materia: selectedCourse?.nombre_materia || '',
      }))
      return
    }

    if (name === 'codigo_periodo') {
      const selectedPeriod = catalogs.periodos.find((item) => String(item.cod_periodo) === String(value))
      setBulkForm((current) => ({
        ...current,
        codigo_periodo: String(value),
        estado_periodo: selectedPeriod?.estado || '',
      }))
    }
  }

  function handleFileChange(event) {
    setSelectedFile(event.target.files?.[0] || null)
    setBulkResult(null)
    setBulkMessage('')
    setBulkError('')
  }

  async function handleTemplateDownload() {
    setIsDownloadingTemplate(true)
    setBulkError('')

    try {
      const response = await adminFetch('/api/auth/admin/bulk-enrollment/template/')
      if (!response.ok) {
        const payload = await readResponsePayload(response)
        throw new Error(payload?.message ?? `No fue posible descargar la plantilla (${response.status}).`)
      }

      await downloadBlobResponse(response, 'plantilla_matricula_masiva.xlsx')
    } catch (error) {
      setBulkError(error.message)
    } finally {
      setIsDownloadingTemplate(false)
    }
  }

  async function handleCertificateDownload(item) {
    const certificateToken = item?.certificate?.token
    const certificateKey = `${item?.fila || ''}-${item?.cedula || ''}-${item?.email || ''}`
    if (!certificateToken) {
      setBulkError('No hay datos de certificado para este registro.')
      return
    }

    setDownloadingCertificateKey(certificateKey)
    setBulkError('')

    try {
      const response = await adminFetch('/api/auth/inscription/certificate/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          certificate: {
            token: certificateToken,
          },
        }),
      })

      if (!response.ok) {
        const payload = await readResponsePayload(response)
        throw new Error(payload?.message ?? `No fue posible generar el certificado (${response.status}).`)
      }

      await downloadBlobResponse(
        response,
        item?.certificate?.filename || `certificado_inscripcion_${item?.matricula || item?.cedula || 'registro'}.pdf`,
      )
    } catch (error) {
      setBulkError(error.message)
    } finally {
      setDownloadingCertificateKey('')
    }
  }

  async function handleBulkSubmit(event) {
    event.preventDefault()
    setIsSubmitting(true)
    setBulkError('')
    setBulkMessage('')
    setBulkResult(null)

    if (!selectedFile) {
      setBulkError('Selecciona un archivo Excel .xlsx para procesar.')
      setIsSubmitting(false)
      return
    }

    try {
      const fileContent = await fileToBase64(selectedFile)

      const response = await adminFetch('/api/auth/admin/bulk-enrollment/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...bulkForm,
          excel: {
            name: selectedFile.name,
            content_base64: fileContent,
          },
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible procesar la carga (${response.status}).`)
      }

      setBulkResult(payload.result)
      setBulkMessage(payload.message || 'Carga masiva procesada.')
    } catch (error) {
      setBulkError(error.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const activeCourses = onlyActiveCourses(catalogs.cursos_por_carrera?.[bulkForm.cod_anio_basica])
  const activePeriods = catalogs.periodos.filter((period) => period.es_activo)

  return (
    <section id="admin-bulk-enrollment" className="admin-bulk-enrollment">
      <div className="admin-section-heading">
        <div>
          <h3>Matrícula masiva</h3>
          <p>Sube un Excel y aplica la misma carrera, curso y periodo a todos los registros.</p>
        </div>
      </div>

      <article className="module-card bulk-enrollment-card">
        <form className="auth-form bulk-enrollment-form" onSubmit={handleBulkSubmit}>
          <div className="admin-form-grid">
            <label className="field">
              <span>Carrera *</span>
              <select
                name="carrera_num"
                value={bulkForm.carrera_num}
                onChange={handleSelectionChange}
                disabled={isLoadingCatalogs}
                required
              >
                <option value="">Selecciona una carrera</option>
                {catalogs.carreras.map((career) => (
                  <option key={career.num} value={career.num}>
                    {removeNumbersFromLabel(career.nombre_basica)}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Curso *</span>
              <select
                name="codigo_materia"
                value={bulkForm.codigo_materia}
                onChange={handleSelectionChange}
                disabled={isLoadingCatalogs || !bulkForm.cod_anio_basica}
                required
              >
                <option value="">Selecciona un curso</option>
                {activeCourses.map((course) => (
                  <option key={course.codigo_materia} value={course.codigo_materia}>
                    {removeNumbersFromLabel(course.nombre_materia)}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Periodo *</span>
              <select
                name="codigo_periodo"
                value={bulkForm.codigo_periodo}
                onChange={handleSelectionChange}
                disabled={isLoadingCatalogs}
                required
              >
                <option value="">Selecciona un periodo</option>
                {activePeriods.map((period) => (
                  <option key={period.cod_periodo} value={period.cod_periodo}>
                    {removeNumbersFromLabel(period.detalle_periodo)}
                  </option>
                ))}
              </select>
            </label>

            <div className="bulk-status-note">
              <strong>Sin cargo de pago</strong>
              <span>Esta carga solo matrícula, crea credenciales y envía la bienvenida.</span>
            </div>
          </div>

          <div className="bulk-file-sections">
            <section className="bulk-template-panel">
              <div>
                <h4>Plantilla Excel</h4>
                <p>Descarga el formato oficial antes de cargar estudiantes.</p>
              </div>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={handleTemplateDownload}
                disabled={isDownloadingTemplate}
              >
                {isDownloadingTemplate ? 'Descargando...' : 'Descargar plantilla'}
              </button>
            </section>

            <section className="bulk-upload-panel">
              <label className="field">
                <span>Archivo Excel .xlsx *</span>
                <input type="file" accept=".xlsx" onChange={handleFileChange} required />
              </label>
              <div className="bulk-upload-help">
                <strong>Columnas aceptadas</strong>
                <p>
                  Nombres, Apellidos, Cédula, Correo, Número de celular, Ocupación,
                  Empresa. Localidad y Dirección son opcionales.
                </p>
              </div>
            </section>
          </div>

          {bulkError ? <p className="form-error">{bulkError}</p> : null}
          {bulkMessage ? <p className="form-success">{bulkMessage}</p> : null}

          <button
            type="submit"
            className="submit-button"
            disabled={isSubmitting || isLoadingCatalogs}
          >
            {isSubmitting ? 'Procesando matrícula masiva...' : 'Procesar matrícula masiva'}
          </button>
        </form>
      </article>

      {bulkResult ? (
        <article className="module-card bulk-result-card">
          <div className="bulk-summary-grid">
            <div>
              <span>Total</span>
              <strong>{bulkResult.total}</strong>
            </div>
            <div>
              <span>Exitosos</span>
              <strong>{bulkResult.exitosos}</strong>
            </div>
            <div>
              <span>Fallidos</span>
              <strong>{bulkResult.fallidos}</strong>
            </div>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Fila</th>
                  <th>Estudiante</th>
                  <th>Cédula</th>
                  <th>Correo</th>
                  <th>Matrícula</th>
                  <th>Materia</th>
                  <th>Bienvenida</th>
                  <th>Correo cert.</th>
                  <th>Certificado</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {(bulkResult.results || []).map((item) => {
                  const certificateKey = `${item.fila}-${item.cedula}-${item.email}`
                  return (
                    <tr key={certificateKey}>
                      <td>{item.fila}</td>
                      <td>{item.nombre}</td>
                      <td>{item.cedula}</td>
                      <td>{item.email}</td>
                      <td>{item.matricula || '-'}</td>
                      <td>{item.materia || item.codigo_materia || '-'}</td>
                      <td>{item.ok ? (item.welcome_email_sent ? 'Enviada' : 'Pendiente') : '-'}</td>
                      <td>{item.ok ? (item.certificate_email_sent ? 'Enviado' : 'Pendiente') : '-'}</td>
                      <td>
                        {item.ok && item.certificate ? (
                          <button
                            type="button"
                            className="ghost-button compact-button table-action-button"
                            onClick={() => handleCertificateDownload(item)}
                            disabled={downloadingCertificateKey === certificateKey}
                          >
                            {downloadingCertificateKey === certificateKey ? 'Generando...' : 'Descargar'}
                          </button>
                        ) : (
                          '-'
                        )}
                      </td>
                      <td>{item.ok ? item.message || 'Procesado' : item.message}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}
    </section>
  )
}
