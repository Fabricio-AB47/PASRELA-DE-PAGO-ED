import { useEffect, useMemo, useState } from 'react'
import { downloadBlobResponse, readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const integerFormatter = new Intl.NumberFormat('es-EC')
const decimalFormatter = new Intl.NumberFormat('es-EC', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const moneyFormatter = new Intl.NumberFormat('es-EC', {
  style: 'currency',
  currency: 'USD',
})

export default function StudentGradesPanel() {
  const [dashboard, setDashboard] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [sendingId, setSendingId] = useState('')
  const [previewingId, setPreviewingId] = useState('')
  const [previewCourse, setPreviewCourse] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const courses = useMemo(() => dashboard?.courses || [], [dashboard?.courses])

  useEffect(() => {
    let isMounted = true

    async function loadGrades() {
      setIsLoading(true)
      setError('')
      setMessage('')

      try {
        const response = await adminFetch('/api/auth/student/grades/')
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok || !payload.dashboard) {
          throw new Error(payload?.message ?? `No fue posible cargar las calificaciones (${response.status}).`)
        }

        if (isMounted) {
          setDashboard(payload.dashboard)
        }
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

    loadGrades()

    return () => {
      isMounted = false
    }
  }, [])

  useEffect(() => (
    () => {
      if (previewUrl) {
        window.URL.revokeObjectURL(previewUrl)
      }
    }
  ), [previewUrl])

  async function handlePreviewCertificate(course) {
    setError('')
    setMessage('')
    setPreviewCourse(course)

    setPreviewingId(course.estudiante_corte_id)
    try {
      const params = new URLSearchParams({ estudiante_corte_id: course.estudiante_corte_id })
      const response = await adminFetch(`/api/auth/student/certificate/preview/?${params.toString()}`)
      if (!response.ok) {
        const payload = await readResponsePayload(response)
        throw new Error(payload?.message ?? `No fue posible generar la vista previa (${response.status}).`)
      }
      const blob = await response.blob()
      if (!blob.size || !blob.type.startsWith('image/')) {
        throw new Error('El servidor no devolvió una imagen válida para la vista previa.')
      }
      const objectUrl = window.URL.createObjectURL(blob)
      setPreviewUrl((current) => {
        if (current) {
          window.URL.revokeObjectURL(current)
        }
        return objectUrl
      })
    } catch (previewError) {
      setPreviewUrl('')
      setError(previewError.message)
    } finally {
      setPreviewingId('')
    }
  }

  function closePreview() {
    setPreviewCourse(null)
    setPreviewUrl((current) => {
      if (current) {
        window.URL.revokeObjectURL(current)
      }
      return ''
    })
  }

  async function handleDeliverCertificate(course) {
    setError('')
    setMessage('')
    setSendingId(course.estudiante_corte_id)

    try {
      const params = new URLSearchParams({ estudiante_corte_id: course.estudiante_corte_id })
      const downloadResponse = await adminFetch(`/api/auth/student/certificate/download/?${params.toString()}`)
      if (!downloadResponse.ok) {
        const payload = await readResponsePayload(downloadResponse)
        throw new Error(payload?.message ?? `No fue posible descargar el certificado (${downloadResponse.status}).`)
      }
      await downloadBlobResponse(downloadResponse, certificateFallbackName(course))

      const sendResponse = await adminFetch('/api/auth/student/certificate/send/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ estudiante_corte_id: course.estudiante_corte_id }),
      })
      const payload = await readResponsePayload(sendResponse)
      if (!payload || !sendResponse.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible enviar el certificado (${sendResponse.status}).`)
      }

      setMessage(payload.message || 'Certificado descargado y enviado correctamente.')
    } catch (deliverError) {
      setError(deliverError.message)
    } finally {
      setSendingId('')
    }
  }

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando calificaciones</h3>
          <p>Estamos consultando tus materias matriculadas.</p>
        </div>
      </article>
    )
  }

  if (error && !dashboard) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>No se pudieron cargar las calificaciones</h3>
          <p>{error}</p>
        </div>
      </article>
    )
  }

  const metrics = dashboard?.metrics || {}

  return (
    <section className="teacher-panel" aria-labelledby="student-grades-title">
      <div className="admin-section-heading">
        <div>
          <span className="eyebrow">Estudiante</span>
          <h3 id="student-grades-title">Calificaciones</h3>
        </div>
      </div>

      {message ? <p className="form-success">{message}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}

      <section className="summary-grid teacher-summary-grid" aria-label="Indicadores de calificaciones estudiantiles">
        <article className="summary-card">
          <span>Cursos</span>
          <strong>{formatInteger(metrics.cursos)}</strong>
        </article>
        <article className="summary-card">
          <span>Con nota</span>
          <strong>{formatInteger(metrics.con_nota)}</strong>
        </article>
        <article className="summary-card">
          <span>Aprobados</span>
          <strong>{formatInteger(metrics.aprobados)}</strong>
        </article>
        <article className="summary-card">
          <span>Certificados</span>
          <strong>{formatInteger(metrics.certificados)}</strong>
        </article>
        <article className="summary-card">
          <span>Pagados</span>
          <strong>{formatInteger(metrics.pagados)}</strong>
        </article>
        <article className="summary-card">
          <span>Pendientes</span>
          <strong>{formatInteger(metrics.pendientes_culminacion)}</strong>
        </article>
      </section>

      <article className="module-card teacher-panel-card">
        {courses.length ? (
          <div className="admin-table-wrap">
            <table className="admin-table teacher-panel-table">
              <thead>
                <tr>
                  <th>Materia</th>
                  <th>Cohorte</th>
                  <th>Docente</th>
                  <th>Nota final</th>
                  <th>Asistencia</th>
                  <th>Estado</th>
                  <th>Estado financiero</th>
                  <th>Certificado</th>
                </tr>
              </thead>
              <tbody>
                {courses.map((course) => (
                  <tr key={course.estudiante_corte_id}>
                    <td>
                      <strong>{course.materia}</strong>
                      <span>{course.codigo_materia || course.cod_curso || '-'}</span>
                    </td>
                    <td>
                      <strong>{course.nombre_corte || course.codigo_periodo || '-'}</strong>
                      <span>{course.estado_matricula || '-'}</span>
                    </td>
                    <td>
                      <strong>{course.docente || '-'}</strong>
                      <span>{course.docente_correo || '-'}</span>
                    </td>
                    <td>{formatDecimal(course.nota_final)}</td>
                    <td>
                      <strong>{formatPercent(course.porcentaje_asistencia)}</strong>
                      <span>{formatInteger(course.total_sesiones)} sesión(es)</span>
                    </td>
                    <td>
                      <strong>{course.estado_nota_label || '-'}</strong>
                      <span>{course.culminacion_estado || '-'}</span>
                    </td>
                    <td>
                      <strong>{course.estado_financiero || 'PENDIENTE'}</strong>
                      <span>Saldo: {formatMoney(course.saldo_pendiente)}</span>
                    </td>
                    <td>
                      <div className="student-certificate-actions">
                        <button
                          type="button"
                          className="ghost-button compact-button table-action-button"
                          disabled={previewingId === course.estudiante_corte_id}
                          onClick={() => handlePreviewCertificate(course)}
                        >
                          {previewingId === course.estudiante_corte_id ? 'Cargando...' : 'Vista previa'}
                        </button>
                        {course.certificado_disponible ? (
                          <button
                            type="button"
                            className="ghost-button compact-button table-action-button"
                            disabled={sendingId === course.estudiante_corte_id}
                            onClick={() => handleDeliverCertificate(course)}
                          >
                            {sendingId === course.estudiante_corte_id ? 'Procesando...' : 'Descargar y enviar por correo'}
                          </button>
                        ) : null}
                        <span className={course.certificado_disponible ? '' : 'is-pending'}>
                          {course.certificado_estado || '-'}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="teacher-panel-empty">No tienes calificaciones registradas en educación continua.</p>
        )}
      </article>

      {previewCourse ? (
        <div className="modal-backdrop" role="presentation">
          <article className="registered-user-modal student-certificate-preview-modal" role="dialog" aria-modal="true" aria-labelledby="student-certificate-preview-title">
            <div className="career-modal-header">
              <div>
                <span className="eyebrow">Certificado</span>
                <h4 id="student-certificate-preview-title">Vista previa</h4>
                <p>{previewCourse.materia}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={closePreview}>
                Cerrar
              </button>
            </div>
            {!previewCourse.certificado_disponible ? (
              <p className="student-certificate-pending is-top">
                Vista informativa. La descarga se habilitará al aprobar con una nota entre 7 y 10 y completar el pago.
              </p>
            ) : (
              <section className="student-certificate-preview-meta">
                <div>
                  <span>Estudiante</span>
                  <strong>{previewCourse.nombre || dashboard?.student?.nombre || '-'}</strong>
                </div>
                <div>
                  <span>Certificado</span>
                  <strong>{previewCourse.certificado_estado || '-'}</strong>
                </div>
                <div>
                  <span>Culminación</span>
                  <strong>{previewCourse.culminacion_estado || '-'}</strong>
                </div>
              </section>
            )}
            {previewingId === previewCourse.estudiante_corte_id ? (
              <p className="student-certificate-pending">Cargando vista previa del certificado...</p>
            ) : previewUrl ? (
              <img className="student-certificate-preview-image" src={previewUrl} alt="Vista previa del certificado" />
            ) : (
              <p className="student-certificate-pending">
                No fue posible cargar la vista previa del certificado.
              </p>
            )}
          </article>
        </div>
      ) : null}
    </section>
  )
}

function formatInteger(value) {
  return integerFormatter.format(Number(value || 0))
}

function formatDecimal(value) {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  return decimalFormatter.format(Number(value))
}

function formatPercent(value) {
  if (value === null || value === undefined || value === '') {
    return '0%'
  }
  return `${decimalFormatter.format(Number(value))}%`
}

function formatMoney(value) {
  return moneyFormatter.format(Number(value || 0))
}

function certificateFallbackName(course) {
  const coursePart = String(course.materia || 'curso')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/gi, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()
  return `certificado_aprobacion_${coursePart || course.estudiante_corte_id}.pdf`
}
