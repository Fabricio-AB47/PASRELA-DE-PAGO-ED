import { useEffect, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const initialForm = {
  nombre: '',
  cedula: '',
  email: '',
  telefono: '',
  movil: '',
  direccion: '',
}

export default function AdminTeacherEntryPanel() {
  const [form, setForm] = useState(initialForm)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isCheckingIdentity, setIsCheckingIdentity] = useState(false)
  const [identityStatus, setIdentityStatus] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [result, setResult] = useState(null)

  useEffect(() => {
    if (!result) return undefined
    const timer = window.setTimeout(() => setResult(null), 15000)
    return () => window.clearTimeout(timer)
  }, [result])

  function handleChange(event) {
    const { name, value } = event.target
    setResult(null)
    setMessage('')
    if (name === 'cedula' || name === 'nombre') {
      setIdentityStatus(null)
    }
    setForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  async function validateIdentity() {
    const cedula = form.cedula.replace(/\D+/g, '')
    if (!/^\d{6,20}$/.test(cedula)) {
      setIdentityStatus(null)
      setError('Ingresa una cédula válida de 6 a 20 dígitos para consultar.')
      return null
    }

    setIsCheckingIdentity(true)
    setError('')
    setMessage('')
    try {
      const response = await adminFetch('/api/auth/admin/teacher-identity/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cedula, nombre: form.nombre }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible validar la cédula (${response.status}).`)
      }
      setIdentityStatus(payload.result)
      setMessage(payload.message || 'Cédula validada.')
      setForm((current) => ({
        ...current,
        cedula,
        nombre: current.nombre || payload.result?.nombre || '',
        email: current.email || payload.result?.correo_personal || '',
      }))
      return payload.result
    } catch (validationError) {
      setIdentityStatus(null)
      setError(validationError.message)
      return null
    } finally {
      setIsCheckingIdentity(false)
    }
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setIsSubmitting(true)
    setError('')
    setMessage('')
    setResult(null)

    try {
      const response = await adminFetch('/api/auth/admin/teacher-entry/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(form),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible ingresar al docente (${response.status}).`)
      }

      setResult(payload.result)
      setMessage(payload.message || 'Ingreso docente procesado.')
      setForm(initialForm)
      setIdentityStatus(null)
    } catch (submitError) {
      setError(submitError.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <section id="admin-teacher-entry" className="admin-bulk-enrollment">
      <div className="admin-section-heading">
        <div>
          <h3>Registro docente</h3>
          <p>Registra al docente, crea su cuenta Office 365 A1 para profesores y envía sus credenciales.</p>
        </div>
      </div>

      <form className="auth-form bulk-enrollment-form" onSubmit={handleSubmit}>
        <div className="registration-grid registration-data-grid">
          <label className="field">
            <span>Nombre completo *</span>
            <input name="nombre" type="text" value={form.nombre} onChange={handleChange} required />
          </label>

          <label className="field">
            <span>Cédula *</span>
            <input
              name="cedula"
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6,20}"
              value={form.cedula}
              onChange={handleChange}
              required
            />
          </label>

          <label className="field">
            <span>Correo personal *</span>
            <input name="email" type="email" value={form.email} onChange={handleChange} required />
          </label>

          <label className="field">
            <span>Teléfono</span>
            <input name="telefono" type="text" value={form.telefono} onChange={handleChange} />
          </label>

          <label className="field">
            <span>Móvil</span>
            <input name="movil" type="text" value={form.movil} onChange={handleChange} />
          </label>

          <label className="field">
            <span>Dirección</span>
            <input name="direccion" type="text" value={form.direccion} onChange={handleChange} />
          </label>
        </div>

        <div className="student-selection-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={validateIdentity}
            disabled={isCheckingIdentity || isSubmitting}
          >
            {isCheckingIdentity ? 'Validando...' : 'Validar'}
          </button>
          <button
            className="submit-button compact-button"
            type="submit"
            disabled={isSubmitting || isCheckingIdentity || !identityStatus}
          >
            {isSubmitting ? 'Procesando ingreso...' : 'Ingresar y crear credenciales'}
          </button>
        </div>

        {identityStatus ? (
          <div className="bulk-result-summary">
            <div>
              <span>Registro encontrado</span>
              <strong>{identityStatus.profiles?.length ? identityStatus.profiles.join(' / ') : 'NUEVO'}</strong>
            </div>
            <div>
              <span>Correo institucional</span>
              <strong>{identityStatus.correo_intec || 'Se generará al registrar'}</strong>
            </div>
            <div>
              <span>Tratamiento de credenciales</span>
              <strong>
                {identityStatus.credentials_found
                  ? 'Reutilizar existentes'
                  : identityStatus.office365_found
                    ? 'Reutilizar Office 365 sin modificar'
                    : identityStatus.office365_check_performed
                      ? 'Crear nuevas'
                      : 'Completa el nombre para validar Office 365'}
              </strong>
            </div>
          </div>
        ) : null}

        {error ? <p className="form-error">{error}</p> : null}
        {message ? <p className="form-success">{message}</p> : null}

      </form>

      {result ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setResult(null)}>
          <section
            className="registration-result-modal teacher-entry-result-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="teacher-entry-result-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="registration-result-header">
              <div>
                <h2 id="teacher-entry-result-title">Registro docente completado</h2>
                <p>Esta información se cerrará automáticamente en 15 segundos.</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => setResult(null)}>
                Cerrar
              </button>
            </div>

            <div className="registration-result-grid">
            <div>
              <span>Docente</span>
              <strong>{result.teacher?.nombre || '-'}</strong>
            </div>
            <div>
              <span>Cédula</span>
              <strong>{result.teacher?.cedula || '-'}</strong>
            </div>
            <div>
              <span>Usuario</span>
              <strong>{result.credentials?.correo_intec || result.user?.login || '-'}</strong>
            </div>
            <div>
              <span>{result.office365_reused ? 'Contraseña del dashboard' : 'Contraseña temporal'}</span>
              <strong>{result.credentials?.password_temporal || '-'}</strong>
            </div>
            <div>
              <span>Correo</span>
              <strong>{result.email_result?.sent ? 'Enviado' : 'Pendiente'}</strong>
            </div>
            <div>
              <span>Office 365</span>
              <strong>{result.office365_reused ? 'Existente, sin modificaciones' : 'Cuenta nueva'}</strong>
            </div>
            <div>
              <span>Credenciales</span>
              <strong>{result.credentials_reused ? 'Reutilizadas' : 'Procesadas correctamente'}</strong>
            </div>
          </div>
            <div className="registration-result-actions">
              <button type="button" className="submit-button compact-button" onClick={() => setResult(null)}>
                Entendido
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}
