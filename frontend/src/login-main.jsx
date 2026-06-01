/* eslint-disable react-refresh/only-export-components */
import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './App.css'
import { readResponsePayload, setStoredSession } from './shared.js'

function LoginPage() {
  const [form, setForm] = useState({
    identifier: '',
    password: '',
  })
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  async function handleSubmit(event) {
    event.preventDefault()
    setIsSubmitting(true)
    setErrorMessage('')

    try {
      const response = await fetch('/api/auth/login/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...form,
          scope: 'auto',
        }),
      })

      const payload = await readResponsePayload(response)

      if (!payload) {
        throw new Error(`El servidor devolvió una respuesta vacía (${response.status}).`)
      }

      if (!response.ok || !payload.ok) {
        throw new Error(
          payload.message ??
            `No fue posible validar tus credenciales (${response.status}).`,
        )
      }

      setStoredSession(payload)
      window.location.assign('/dashboard/')
    } catch (error) {
      setErrorMessage(error.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  function handleChange(event) {
    const { name, value } = event.target
    setForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  return (
    <main className="login-layout">
      <section className="visual-panel">
        <div className="visual-backdrop">
          <div className="visual-badge">Dashboard institucional</div>
          <div className="visual-copy">
            <span className="eyebrow">Acceso protegido</span>
            <h1>Ingreso exclusivo para dashboard administrativo, docente y estudiantil.</h1>
            <p>
              Esta página es independiente del formulario público de inscripción y se
              usa solo para entrar al dashboard autenticado.
            </p>
          </div>
          <div className="visual-figures" aria-hidden="true">
            <span className="figure figure-one"></span>
            <span className="figure figure-two"></span>
            <span className="figure figure-three"></span>
          </div>
        </div>
      </section>

      <section className="auth-panel">
        <div className="auth-card">
          <div className="brand-mark" aria-hidden="true">
            <span></span>
            <span></span>
            <span></span>
          </div>

          <span className="eyebrow">Login dashboard</span>
          <h2>Iniciar sesión</h2>
          <p className="auth-intro">
            El sistema detecta automáticamente si tu acceso corresponde a estudiante,
            docente o administrativo.
          </p>
          <p className="auto-detection-note">
            Si buscas consultar una inscripción, usa la página pública separada.
          </p>

          <a className="secondary-link" href="/inscripcion/">
            Ir a la página pública de inscripción
          </a>

          <form className="auth-form" onSubmit={handleSubmit}>
            <label className="field">
              <span>Usuario</span>
              <input
                name="identifier"
                type="text"
                value={form.identifier}
                onChange={handleChange}
                placeholder="Correo, login, matrícula, cédula o código"
                autoComplete="username"
              />
            </label>

            <label className="field">
              <span>Contraseña</span>
              <div className="password-field">
                <input
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  value={form.password}
                  onChange={handleChange}
                  placeholder="Ingresa tu contraseña"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  className="toggle-visibility"
                  onClick={() => setShowPassword((current) => !current)}
                >
                  {showPassword ? 'Ocultar' : 'Mostrar'}
                </button>
              </div>
            </label>

            {errorMessage ? <p className="form-error">{errorMessage}</p> : null}

            <button className="submit-button" type="submit" disabled={isSubmitting}>
              {isSubmitting ? 'Validando acceso...' : 'Entrar al dashboard'}
            </button>
          </form>
        </div>
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <LoginPage />
  </StrictMode>,
)
