import { useState } from 'react'
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
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [result, setResult] = useState(null)

  function handleChange(event) {
    const { name, value } = event.target
    setResult(null)
    setMessage('')
    setForm((current) => ({
      ...current,
      [name]: value,
    }))
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

        {error ? <p className="form-error">{error}</p> : null}
        {message ? <p className="form-success">{message}</p> : null}

        <button className="submit-button" type="submit" disabled={isSubmitting}>
          {isSubmitting ? 'Procesando ingreso docente...' : 'Ingresar docente y enviar credenciales'}
        </button>
      </form>

      {result ? (
        <section className="bulk-results">
          <div className="bulk-result-summary">
            <div>
              <span>Docente</span>
              <strong>{result.teacher?.nombre || '-'}</strong>
            </div>
            <div>
              <span>Usuario</span>
              <strong>{result.credentials?.correo_intec || result.user?.login || '-'}</strong>
            </div>
            <div>
              <span>Contraseña temporal</span>
              <strong>{result.credentials?.password_temporal || '-'}</strong>
            </div>
            <div>
              <span>Correo</span>
              <strong>{result.email_result?.sent ? 'Enviado' : 'Pendiente'}</strong>
            </div>
          </div>

          <pre className="json-result">{JSON.stringify(result, null, 2)}</pre>
        </section>
      ) : null}
    </section>
  )
}
