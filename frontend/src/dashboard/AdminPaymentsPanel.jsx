import { useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

export default function AdminPaymentsPanel() {
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
  const [activePaymentModal, setActivePaymentModal] = useState(null)

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
      const response = await adminFetch('/api/auth/admin/payment-info/', {
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
      const response = await adminFetch('/api/auth/admin/payment-cancel/', {
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
    <section id="admin-payments" className="admin-payments">
      <div className="admin-section-heading">
        <div>
          <h3>Pagos</h3>
          <p>Consulta informacion de transacciones o ejecuta anulaciones del proveedor.</p>
        </div>
      </div>

      <div className="payment-module-grid">
        <article className="module-card payment-module-card">
          <div>
            <h4>Consultar transaccion All Digital</h4>
            <p>Busca una transaccion por ID, plataforma o cliente.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => {
              setInfoError('')
              setInfoResult(null)
              setActivePaymentModal('info')
            }}
          >
            Consultar
          </button>
        </article>

        <article className="module-card payment-module-card">
          <div>
            <h4>Anular transaccion</h4>
            <p>Registra una solicitud de anulacion contra la pasarela.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => {
              setCancelError('')
              setCancelResult(null)
              setActivePaymentModal('cancel')
            }}
          >
            Anular
          </button>
        </article>
      </div>

      {activePaymentModal === 'info' ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="career-modal payment-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="payment-info-modal-title"
          >
            <div className="career-modal-header">
              <div>
                <h4 id="payment-info-modal-title">Consultar transaccion All Digital</h4>
                <p>Ingresa los datos disponibles para solicitar informacion al proveedor.</p>
              </div>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => setActivePaymentModal(null)}
              >
                Cerrar
              </button>
            </div>
            <div className="career-modal-body">
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
            </div>
          </section>
        </div>
      ) : null}

      {activePaymentModal === 'cancel' ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="career-modal payment-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="payment-cancel-modal-title"
          >
            <div className="career-modal-header">
              <div>
                <h4 id="payment-cancel-modal-title">Anular transaccion</h4>
                <p>Ingresa la referencia y el motivo para ejecutar la anulacion.</p>
              </div>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => setActivePaymentModal(null)}
              >
                Cerrar
              </button>
            </div>
            <div className="career-modal-body">
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
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}

