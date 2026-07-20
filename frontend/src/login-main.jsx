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
  const [roleOptions, setRoleOptions] = useState([])

  async function submitLogin(scope = 'auto') {
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
          scope,
        }),
      })

      const payload = await readResponsePayload(response)

      if (!payload) {
        throw new Error(`El servidor devolvió una respuesta vacía (${response.status}).`)
      }

      if (payload.selection_required && Array.isArray(payload.roles)) {
        setRoleOptions(payload.roles)
        return
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
      setErrorMessage(
        error instanceof TypeError
          ? 'No fue posible conectar con el servidor. Verifica que el backend esté iniciado e inténtalo nuevamente.'
          : error.message,
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  function handleSubmit(event) {
    event.preventDefault()
    submitLogin('auto')
  }

  function handleChange(event) {
    const { name, value } = event.target
    setForm((current) => ({
      ...current,
      [name]: value,
    }))
    setRoleOptions([])
    setErrorMessage('')
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
            Ingresa tus credenciales. El sistema detectará automáticamente el perfil
            correspondiente.
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

            {roleOptions.length ? (
              <section className="role-selection-box" aria-labelledby="role-selection-title">
                <strong id="role-selection-title">Encontramos más de un perfil</strong>
                <p>Selecciona cómo deseas ingresar:</p>
                <div className="role-selection-actions">
                  {roleOptions.map((role) => (
                    <button
                      key={role.scope}
                      type="button"
                      className="ghost-button"
                      onClick={() => submitLogin(role.scope)}
                      disabled={isSubmitting}
                    >
                      Ingresar como {role.label}
                    </button>
                  ))}
                </div>
              </section>
            ) : null}

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
