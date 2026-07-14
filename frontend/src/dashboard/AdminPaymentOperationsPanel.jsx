import { useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const money = new Intl.NumberFormat('es-EC', { style: 'currency', currency: 'USD' })

function amount(value) {
  const parsed = Number(value || 0)
  return Number.isFinite(parsed) ? money.format(parsed) : money.format(0)
}

export default function AdminPaymentOperationsPanel() {
  const [operation, setOperation] = useState('links')
  const [linkSearch, setLinkSearch] = useState('')
  const [linksData, setLinksData] = useState(null)
  const [cedula, setCedula] = useState('')
  const [data, setData] = useState(null)
  const [selectedEnrollment, setSelectedEnrollment] = useState(null)
  const [paymentAmount, setPaymentAmount] = useState('')
  const [selectedTransaction, setSelectedTransaction] = useState(null)
  const [detail, setDetail] = useState(null)
  const [cancelReason, setCancelReason] = useState('Anulación solicitada por financiero')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState('')

  async function request(url, options) {
    const response = await adminFetch(url, options)
    const payload = await readResponsePayload(response)
    if (!payload || !response.ok || !payload.ok) {
      throw new Error(payload?.message ?? `No fue posible completar la operación (${response.status}).`)
    }
    return payload
  }

  function changeOperation(nextOperation) {
    setOperation(nextOperation)
    setError('')
    setMessage('')
    setSelectedTransaction(null)
    setDetail(null)
  }

  async function loadLinks(event) {
    event?.preventDefault()
    setLoading('links')
    setError('')
    setMessage('')
    setSelectedTransaction(null)
    setDetail(null)
    try {
      const payload = await request(`/api/auth/admin/payment-operations/links/?q=${encodeURIComponent(linkSearch.trim())}`)
      setLinksData(payload.result)
    } catch (requestError) {
      setLinksData(null)
      setError(requestError.message)
    } finally {
      setLoading('')
    }
  }

  async function loadStudent(identity = cedula) {
    const clean = String(identity || '').replace(/\D/g, '')
    if (clean.length < 6) {
      setError('Ingresa una cédula válida.')
      return
    }
    setLoading('student')
    setError('')
    setMessage('')
    setData(null)
    setSelectedEnrollment(null)
    try {
      const payload = await request(`/api/auth/admin/payment-operations/?cedula=${encodeURIComponent(clean)}`)
      setCedula(clean)
      setData(payload.result)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading('')
    }
  }

  function openPayment(enrollment) {
    setSelectedEnrollment(enrollment)
    setPaymentAmount(Math.min(500, Number(enrollment.available_to_generate || 0)).toFixed(2))
    setError('')
    setMessage('')
  }

  async function generatePayment(event) {
    event.preventDefault()
    if (!selectedEnrollment) return
    setLoading('generate')
    setError('')
    setMessage('')
    try {
      const payload = await request('/api/auth/admin/payment-operations/generate/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cedula, estudiante_corte_id: selectedEnrollment.estudiante_corte_id, monto: paymentAmount }),
      })
      const typeLabel = payload.result.payment_type === 'PARCIAL' ? 'abono parcial' : 'pago total'
      setSelectedEnrollment(null)
      await loadStudent(cedula)
      setMessage(`Se generó el enlace para ${typeLabel} y se notificó al estudiante.`)
      if (payload.result.payment_link) window.open(payload.result.payment_link, '_blank', 'noopener,noreferrer')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading('')
    }
  }

  async function showTransaction(transaction) {
    if (!transaction.provider_transaction_id) {
      setError('Esta solicitud todavía no tiene un identificador de transacción de AllDigital.')
      return
    }
    setLoading(`detail-${transaction.provider_transaction_id}`)
    setError('')
    setMessage('')
    setSelectedTransaction(transaction)
    setDetail(null)
    try {
      const payload = await request('/api/auth/admin/payment-info/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transaccion_id: transaction.provider_transaction_id }),
      })
      setDetail(payload.result)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading('')
    }
  }

  async function cancelTransaction(event) {
    event.preventDefault()
    if (!selectedTransaction?.provider_transaction_id) return
    if (!window.confirm('¿Confirma que desea anular esta transacción? Esta acción se enviará a AllDigital.')) return
    setLoading('cancel')
    setError('')
    setMessage('')
    try {
      await request('/api/auth/admin/payment-cancel/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transaccion_id: selectedTransaction.provider_transaction_id, motivo: cancelReason }),
      })
      setSelectedTransaction(null)
      setDetail(null)
      await loadLinks()
      setMessage('La transacción fue anulada y retirada de la lista activa.')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading('')
    }
  }

  const student = data?.student || {}
  const visibleTransactions = operation === 'cancellations'
    ? (linksData?.transactions || []).filter((transaction) => !transaction.is_paid)
    : (linksData?.transactions || [])
  const selectedPending = Number(selectedEnrollment?.pending_balance || 0)
  const selectedAvailable = Number(selectedEnrollment?.available_to_generate || 0)
  const selectedAmount = Number(paymentAmount || 0)
  const remainingAfterLink = Math.max(0, selectedPending - selectedAmount)
  const validPaymentAmount = selectedAmount > 0 && selectedAmount <= selectedAvailable

  return (
    <section id="admin-payment-operations" className="admin-payments payment-operations">
      <div className="admin-section-heading">
        <div><h3>Operaciones AllDigital</h3><p>Consulta, anula o genera enlaces desde flujos independientes.</p></div>
        <a className="ghost-button compact-button" href="#payments">Volver a pagos</a>
      </div>

      <nav className="payment-operation-menu" aria-label="Operaciones disponibles en AllDigital">
        <button type="button" className={operation === 'links' ? 'is-active' : ''} onClick={() => changeOperation('links')}><strong>Consultar enlaces</strong><span>Ver pagados y pendientes</span></button>
        <button type="button" className={operation === 'cancellations' ? 'is-active' : ''} onClick={() => changeOperation('cancellations')}><strong>Anulaciones</strong><span>Seleccionar un enlace pendiente</span></button>
        <button type="button" className={operation === 'generate' ? 'is-active' : ''} onClick={() => changeOperation('generate')}><strong>Generar enlace de pago</strong><span>Pago total o parcial</span></button>
      </nav>

      {operation !== 'generate' ? (
        <>
          <article className="module-card course-cut-card payment-operation-search">
            <form className="payment-identity-search" onSubmit={loadLinks}>
              <label className="field"><span>Buscar por cédula o nombre</span><input type="search" value={linkSearch} onChange={(event) => setLinkSearch(event.target.value)} placeholder="Nombre completo o número de cédula" /></label>
              <button type="submit" className="submit-button" disabled={loading === 'links'}>{loading === 'links' ? 'Consultando...' : (linkSearch.trim() ? 'Buscar enlaces' : 'Mostrar todos')}</button>
            </form>
          </article>

          {linksData?.summary ? (
            <section className="bulk-summary-grid payment-summary-grid" aria-label="Resumen de enlaces AllDigital">
              <div><span>Total activos</span><strong>{linksData.summary.total}</strong></div>
              <div><span>Pagados</span><strong>{linksData.summary.paid}</strong></div>
              <div><span>Pendientes</span><strong>{linksData.summary.pending}</strong></div>
              <div><span>Valor generado</span><strong>{amount(linksData.summary.generated_value)}</strong></div>
              <div><span>Valor pagado</span><strong>{amount(linksData.summary.paid_value)}</strong></div>
            </section>
          ) : null}

          {linksData ? (
            <article className="module-card course-cut-card">
              <div className="module-card-header"><div><h4>{operation === 'cancellations' ? 'Enlaces disponibles para anulación' : 'Enlaces generados'}</h4><p>{operation === 'cancellations' ? 'Solo aparecen transacciones pendientes que pueden seleccionarse.' : 'Revisa cuáles están pagados y cuáles continúan pendientes.'}</p></div></div>
              {visibleTransactions.length ? <div className="payment-operation-list">{visibleTransactions.map((transaction, index) => (
                <div className={`payment-operation-row transaction-row ${selectedTransaction === transaction ? 'is-selected' : ''}`} key={`${transaction.origin}-${transaction.request_id || transaction.inscription_payment_id || index}`}>
                  <div className="payment-operation-course"><strong>{transaction.nombre || 'Estudiante'}</strong><span>{transaction.cedula || '-'} · {transaction.course_name || transaction.description || 'Pago de curso'}</span></div>
                  <div><span>Valor</span><strong>{amount(transaction.amount)}</strong></div>
                  <div><span>Tipo</span><strong>{transaction.payment_type || 'INSCRIPCIÓN'}</strong></div>
                  <div><span>Estado</span><strong className={transaction.is_paid ? 'operation-paid' : 'operation-pending'}>{transaction.is_paid ? 'PAGADO' : 'PENDIENTE'}</strong></div>
                  <div><span>Transacción</span><strong>{transaction.provider_transaction_id || 'Sin ID'}</strong></div>
                  <div className="inline-actions">
                    {transaction.payment_link ? <a className="ghost-button compact-button" href={transaction.payment_link} target="_blank" rel="noreferrer">Abrir link</a> : null}
                    <button className="ghost-button compact-button" type="button" disabled={!transaction.provider_transaction_id || loading.startsWith('detail-')} onClick={() => showTransaction(transaction)}>{operation === 'cancellations' ? 'Seleccionar para anular' : 'Consultar información'}</button>
                  </div>
                </div>
              ))}</div> : <p className="empty-state">No se encontraron enlaces para esta búsqueda.</p>}
            </article>
          ) : null}
        </>
      ) : (
        <>
          <section className="payment-generation-steps" aria-label="Proceso para generar enlace de pago">
            <div className={data ? 'is-complete' : 'is-active'}><span>1</span><strong>Buscar estudiante</strong></div>
            <div className={selectedEnrollment ? 'is-complete' : (data ? 'is-active' : '')}><span>2</span><strong>Seleccionar matrícula</strong></div>
            <div className={selectedEnrollment ? 'is-active' : ''}><span>3</span><strong>Confirmar valor</strong></div>
          </section>

          <article className="module-card course-cut-card payment-operation-search generation-search-card">
            <div className="module-card-header"><div><h4>1. Buscar estudiante</h4><p>Ingresa la cédula para cargar sus matrículas y saldos vigentes.</p></div></div>
            <form className="payment-identity-search" onSubmit={(event) => { event.preventDefault(); loadStudent() }}>
              <label className="field"><span>Cédula del estudiante</span><input value={cedula} onChange={(event) => setCedula(event.target.value.replace(/\D/g, ''))} placeholder="Ej. 1104371859" inputMode="numeric" maxLength={20} /></label>
              <button type="submit" className="submit-button" disabled={loading === 'student'}>{loading === 'student' ? 'Buscando...' : 'Buscar estudiante'}</button>
            </form>
          </article>

          {data ? <>
            <article className="module-card payment-operation-student">
              <div><span>Estudiante</span><strong>{student.nombre || data.enrollments[0]?.nombre}</strong></div>
              <div><span>Cédula</span><strong>{student.cedula || cedula}</strong></div>
              <div><span>Código</span><strong>{student.codigo_estud || data.enrollments[0]?.codigo_estud}</strong></div>
              <div><span>Correo de notificación</span><strong>{student.correo_personal || student.correo_intec || data.enrollments[0]?.email}</strong></div>
            </article>

            <article className="module-card course-cut-card">
              <div className="module-card-header"><div><h4>2. Seleccionar matrícula</h4><p>Elige el curso sobre el que se generará el cobro por tarjeta.</p></div></div>
              <div className="payment-operation-list">{data.enrollments.map((enrollment) => (
                <div className={`payment-operation-row generation-enrollment-row ${selectedEnrollment?.estudiante_corte_id === enrollment.estudiante_corte_id ? 'is-selected' : ''}`} key={enrollment.estudiante_corte_id}>
                  <div className="payment-operation-course"><strong>{enrollment.course_name || 'Curso'}</strong><span>{enrollment.cut_name}</span></div>
                  <div><span>Valor curso</span><strong>{amount(enrollment.total_value)}</strong></div>
                  <div><span>Cancelado</span><strong>{amount(enrollment.registered_value)}</strong></div>
                  <div><span>Descuentos</span><strong>{amount(enrollment.discount_value)}</strong></div>
                  <div><span>Disponible</span><strong>{amount(enrollment.available_to_generate)}</strong></div>
                  <button className="ghost-button compact-button" type="button" disabled={Number(enrollment.available_to_generate) <= 0} onClick={() => openPayment(enrollment)}>{Number(enrollment.pending_balance) <= 0 ? 'Cuenta pagada' : (Number(enrollment.available_to_generate) <= 0 ? 'Tiene enlace activo' : 'Elegir matrícula')}</button>
                </div>
              ))}</div>
            </article>

            {selectedEnrollment ? (
              <article className="module-card course-cut-card payment-generation-card">
                <div className="module-card-header"><div><h4>3. Confirmar y generar enlace</h4><p>{selectedEnrollment.course_name} · {selectedEnrollment.cut_name}</p></div><button type="button" className="ghost-button compact-button" onClick={() => setSelectedEnrollment(null)}>Cambiar matrícula</button></div>
                <div className="payment-generation-balance">
                  <div><span>Valor del curso</span><strong>{amount(selectedEnrollment.total_value)}</strong></div>
                  <div><span>Valor cancelado</span><strong>{amount(selectedEnrollment.registered_value)}</strong></div>
                  <div><span>Descuentos</span><strong>{amount(selectedEnrollment.discount_value)}</strong></div>
                  <div><span>Valor pendiente</span><strong>{amount(selectedEnrollment.pending_balance)}</strong></div>
                  <div className="is-highlighted"><span>Disponible para enlace</span><strong>{amount(selectedEnrollment.available_to_generate)}</strong></div>
                </div>
                <form className="payment-generation-form enhanced-payment-generation-form" onSubmit={generatePayment}>
                  <div className="payment-amount-editor">
                    <label className="field"><span>Valor que se cobrará por AllDigital</span><input type="number" min="0.01" step="0.01" max={selectedEnrollment.available_to_generate} value={paymentAmount} onChange={(event) => setPaymentAmount(event.target.value)} required /></label>
                    <div className="payment-amount-shortcuts">
                      {selectedAvailable >= 500 ? <button type="button" className="ghost-button compact-button" onClick={() => setPaymentAmount('500.00')}>Usar valor de pago $500</button> : null}
                      <button type="button" className="ghost-button compact-button" onClick={() => setPaymentAmount(selectedAvailable.toFixed(2))}>Cobrar disponible</button>
                    </div>
                    {!validPaymentAmount ? <p className="form-error">El valor debe ser mayor a cero y no superar el disponible.</p> : null}
                  </div>
                  <aside className="payment-generation-preview">
                    <span>Vista previa del enlace</span>
                    <div><small>Tipo</small><strong>{selectedAmount < selectedPending ? 'ABONO PARCIAL' : 'PAGO TOTAL'}</strong></div>
                    <div><small>Se cobrará</small><strong>{amount(selectedAmount)}</strong></div>
                    <div><small>Quedará pendiente</small><strong>{amount(remainingAfterLink)}</strong></div>
                    <p>El enlace se abrirá en una nueva pestaña y también se enviará al correo registrado.</p>
                  </aside>
                  <div className="payment-generation-actions">
                    <button type="button" className="ghost-button" onClick={() => setSelectedEnrollment(null)}>Cancelar</button>
                    <button type="submit" className="submit-button" disabled={loading === 'generate' || !validPaymentAmount}>{loading === 'generate' ? 'Generando enlace...' : 'Generar enlace y notificar'}</button>
                  </div>
                </form>
              </article>
            ) : null}
          </> : null}
        </>
      )}

      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="status-message success">{message}</p> : null}

      {selectedTransaction ? (
        <article className="module-card course-cut-card payment-cancel-card">
          <div className="module-card-header"><div><h4>{operation === 'cancellations' ? 'Confirmar anulación' : 'Información de la transacción'}</h4><p>Transacción {selectedTransaction.provider_transaction_id}</p></div><button className="ghost-button compact-button" type="button" onClick={() => { setSelectedTransaction(null); setDetail(null) }}>Cerrar</button></div>
          {detail ? <pre className="json-result">{JSON.stringify(detail, null, 2)}</pre> : <p>Consultando la información vigente en AllDigital...</p>}
          {operation === 'cancellations' ? <form className="payment-cancel-form" onSubmit={cancelTransaction}><label className="field"><span>Motivo de anulación</span><textarea value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} required /></label><button type="submit" className="submit-button danger-button" disabled={loading === 'cancel' || !detail}>{loading === 'cancel' ? 'Anulando...' : 'Anular transacción seleccionada'}</button></form> : null}
        </article>
      ) : null}
    </section>
  )
}
