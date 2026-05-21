import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './App.css'
import { clearStoredSession, getStoredSession, readResponsePayload } from './shared.js'

function AdminPaymentsPanel() {
  const [queryForm, setQueryForm] = useState({
    transaccion_id: '',
    plataforma_id: '',
    cliente: '',
  })
  const [cancelForm, setCancelForm] = useState({
    transaccion_id: '',
    plataforma_id: '',
    motivo: 'Anulacion solicitada por administrador',
  })
  const [isLoadingInfo, setIsLoadingInfo] = useState(false)
  const [isLoadingCancel, setIsLoadingCancel] = useState(false)
  const [infoError, setInfoError] = useState('')
  const [cancelError, setCancelError] = useState('')
  const [infoResult, setInfoResult] = useState(null)
  const [cancelResult, setCancelResult] = useState(null)

  function handleQueryChange(event) {
    const { name, value } = event.target
    setQueryForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  function handleCancelChange(event) {
    const { name, value } = event.target
    setCancelForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  async function handleInfoSubmit(event) {
    event.preventDefault()
    setIsLoadingInfo(true)
    setInfoError('')
    setInfoResult(null)

    try {
      const response = await fetch('/api/auth/admin/payment-info/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(queryForm),
      })

      const payload = await readResponsePayload(response)
      if (!payload) {
        throw new Error(`El servidor devolvio una respuesta vacia (${response.status}).`)
      }
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? `No fue posible consultar (${response.status}).`)
      }

      setInfoResult(payload.result)
    } catch (error) {
      setInfoError(error.message)
    } finally {
      setIsLoadingInfo(false)
    }
  }

  async function handleCancelSubmit(event) {
    event.preventDefault()
    setIsLoadingCancel(true)
    setCancelError('')
    setCancelResult(null)

    try {
      const response = await fetch('/api/auth/admin/payment-cancel/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(cancelForm),
      })

      const payload = await readResponsePayload(response)
      if (!payload) {
        throw new Error(`El servidor devolvio una respuesta vacia (${response.status}).`)
      }
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? `No fue posible anular (${response.status}).`)
      }

      setCancelResult(payload.result)
    } catch (error) {
      setCancelError(error.message)
    } finally {
      setIsLoadingCancel(false)
    }
  }

  return (
    <section className="admin-payments">
      <article className="module-card">
        <h3>Consultar transaccion All Digital</h3>
        <form className="auth-form" onSubmit={handleInfoSubmit}>
          <div className="lookup-grid">
            <label className="field">
              <span>ID transaccion</span>
              <input
                name="transaccion_id"
                type="text"
                value={queryForm.transaccion_id}
                onChange={handleQueryChange}
                placeholder="ID interno del proveedor"
              />
            </label>
            <label className="field">
              <span>ID plataforma</span>
              <input
                name="plataforma_id"
                type="text"
                value={queryForm.plataforma_id}
                onChange={handleQueryChange}
                placeholder="Referencia de plataforma"
              />
            </label>
            <label className="field full-span">
              <span>Cliente</span>
              <input
                name="cliente"
                type="text"
                value={queryForm.cliente}
                onChange={handleQueryChange}
                placeholder="Correo, cedula o identificador"
              />
            </label>
          </div>
          {infoError ? <p className="form-error">{infoError}</p> : null}
          <button type="submit" className="submit-button" disabled={isLoadingInfo}>
            {isLoadingInfo ? 'Consultando...' : 'Obtener informacion'}
          </button>
        </form>
        {infoResult ? (
          <pre className="json-result">{JSON.stringify(infoResult, null, 2)}</pre>
        ) : null}
      </article>

      <article className="module-card">
        <h3>Anular transaccion</h3>
        <form className="auth-form" onSubmit={handleCancelSubmit}>
          <div className="lookup-grid">
            <label className="field">
              <span>ID transaccion</span>
              <input
                name="transaccion_id"
                type="text"
                value={cancelForm.transaccion_id}
                onChange={handleCancelChange}
                placeholder="ID interno del proveedor"
              />
            </label>
            <label className="field">
              <span>ID plataforma</span>
              <input
                name="plataforma_id"
                type="text"
                value={cancelForm.plataforma_id}
                onChange={handleCancelChange}
                placeholder="Referencia de plataforma"
              />
            </label>
            <label className="field full-span">
              <span>Motivo</span>
              <input
                name="motivo"
                type="text"
                value={cancelForm.motivo}
                onChange={handleCancelChange}
                placeholder="Motivo de anulacion"
              />
            </label>
          </div>
          {cancelError ? <p className="form-error">{cancelError}</p> : null}
          <button type="submit" className="submit-button" disabled={isLoadingCancel}>
            {isLoadingCancel ? 'Anulando...' : 'Anular transaccion'}
          </button>
        </form>
        {cancelResult ? (
          <pre className="json-result">{JSON.stringify(cancelResult, null, 2)}</pre>
        ) : null}
      </article>
    </section>
  )
}

function DashboardPage() {
  const [session, setSession] = useState(() => getStoredSession())

  useEffect(() => {
    if (!session?.user) {
      window.location.replace('/login/')
    }
  }, [session])

  if (!session?.user) {
    return null
  }

  const { user } = session

  function handleLogout() {
    clearStoredSession()
    setSession(null)
    window.location.replace('/login/')
  }

  return (
    <main className="dashboard-layout">
      <header className="dashboard-topbar">
        <div>
          <span className="eyebrow">Acceso concedido</span>
          <h1 className="dashboard-title">Dashboard de pagos ED</h1>
        </div>
        <button type="button" className="ghost-button" onClick={handleLogout}>
          Cerrar sesion
        </button>
      </header>

      <section className="dashboard-shell">
        <aside className="dashboard-sidebar">
          <div className="identity-card">
            <span className="identity-badge">{user.category_label}</span>
            <h2>{user.display_name}</h2>
            <p>{user.email ?? user.login}</p>
          </div>

          <dl className="identity-meta">
            <div>
              <dt>Rol</dt>
              <dd>{user.role.name}</dd>
            </div>
            <div>
              <dt>Estado</dt>
              <dd>{user.status}</dd>
            </div>
            <div>
              <dt>Acceso</dt>
              <dd>Autorizado</dd>
            </div>
          </dl>
        </aside>

        <section className="dashboard-main">
          <article className="hero-card">
            <span className="eyebrow">Perfil autenticado</span>
            <h2>{user.role.name}</h2>
            <p>
              El dashboard se cargo segun tu perfil institucional y ya puede mostrar
              la informacion disponible para tu acceso.
            </p>
          </article>

          <section className="summary-grid">
            {user.summary.map((item) => (
              <article key={item.label} className="summary-card">
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </article>
            ))}
          </section>

          <section className="module-grid">
            {user.modules.map((module) => (
              <article key={module.title} className="module-card">
                <h3>{module.title}</h3>
                <p>{module.description}</p>
              </article>
            ))}
          </section>

          {user.category === 'staff' ? <AdminPaymentsPanel /> : null}
        </section>
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <DashboardPage />
  </StrictMode>,
)
