import { useCallback, useEffect, useState } from 'react'
import { getStoredSession, readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'
import { ECUADOR_FINANCIAL_INSTITUTIONS } from './ecuadorFinancialInstitutions.js'

const moneyFormatter = new Intl.NumberFormat('es-EC', {
  style: 'currency',
  currency: 'USD',
})

const FINANCIAL_INSTITUTION_RESULT_LIMIT = 15

function normalizeInstitutionSearch(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
}

function formatMoney(value) {
  const number = Number(value || 0)
  return moneyFormatter.format(Number.isFinite(number) ? number : 0)
}

function isAccountPaid(account) {
  return Number(account?.registered_value || 0) > 0 && Number(account?.pending_balance || 0) <= 0
}

function accountBalanceStatus(account) {
  return isAccountPaid(account) ? 'PAGADO' : 'PENDIENTE'
}

function discountTypeFromMovement(payment) {
  const detail = String(payment?.detalle || '').toUpperCase()
  if (detail.startsWith('BECA ')) return 'BECA'
  const match = detail.match(/^DESCUENTO\s+([A-Z_]+)\s+/)
  return match?.[1] || 'OTRO'
}

function discountPercentageFromMovement(payment, courseValue) {
  const observationMatch = String(payment?.observacion || '').match(/PORCENTAJE=([\d.,]+)%/i)
  if (observationMatch?.[1]) return observationMatch[1].replace(',', '.')
  const value = Number(payment?.valor_registrado || 0)
  return courseValue > 0 ? String(Math.min(100, Number(((value / courseValue) * 100).toFixed(2)))) : ''
}

function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('No fue posible leer el documento seleccionado.'))
    reader.readAsDataURL(file)
  })
}

const STUDENT_FIELD_LABELS = {
  codigo_estud: 'Código estudiantil', cedula: 'Cédula', nombre: 'Nombres completos',
  correo_personal: 'Correo personal', correo_intec: 'Correo INTEC', telefono: 'Teléfono', movil: 'Móvil',
  fecha_nacimiento: 'Fecha de nacimiento', sexo: 'Sexo', nacionalidad: 'Nacionalidad', estado_civil: 'Estado civil',
  ciudad: 'Ciudad', canton: 'Cantón', sector: 'Sector', direccion: 'Dirección domiciliaria', ocupacion: 'Ocupación',
  empresa: 'Empresa', lugar_trabajo: 'Lugar de trabajo', direccion_trabajo: 'Dirección de trabajo',
  telefono_trabajo: 'Teléfono de trabajo', fecha_ingreso: 'Fecha de ingreso', tipo_documento: 'Tipo de documento',
  estado: 'Estado académico', matricula: 'Matrícula', monto: 'Monto del link', descripcion_pago: 'Descripción del pago',
  transaccion_id: 'ID de transacción', estado_pago: 'Estado actual del pago', fecha_solicitud: 'Fecha de solicitud',
  solicitud_id: 'Número de solicitud', link_pago: 'Link de pago',
  valor_pagado_link: 'Valor pagado por link', fecha_pago_link: 'Fecha de pago confirmada',
  estado_financiero_educontinua: 'Estado financiero', total_facturado_educontinua: 'Total facturado',
  total_pagado_educontinua: 'Total pagado', saldo_pendiente_educontinua: 'Saldo pendiente',
  ultimo_pago_educontinua: 'Último pago registrado',
}

function studentFieldLabel(key) {
  return STUDENT_FIELD_LABELS[key] || key.replaceAll('_', ' ')
}

const FULL_WIDTH_STUDENT_FIELDS = new Set(['descripcion_pago', 'link_pago', 'direccion', 'direccion_trabajo'])
const MEDIUM_WIDTH_STUDENT_FIELDS = new Set([
  'nombre', 'correo_personal', 'correo_intec', 'empresa', 'lugar_trabajo', 'ocupacion',
])
const MONEY_STUDENT_FIELDS = new Set([
  'monto', 'valor_pagado_link', 'total_facturado_educontinua', 'total_pagado_educontinua',
  'saldo_pendiente_educontinua',
])

function studentFieldValue(key, value) {
  if (MONEY_STUDENT_FIELDS.has(key)) return formatMoney(value)
  return value || '-'
}

function studentFieldSize(key, value) {
  if (FULL_WIDTH_STUDENT_FIELDS.has(key) || String(value || '').length > 90) return 'is-full'
  if (MEDIUM_WIDTH_STUDENT_FIELDS.has(key) || String(value || '').length > 32) return 'is-medium'
  return 'is-compact'
}

export default function AdminPaymentsPanel() {
  const isAdministrator = String(getStoredSession()?.user?.role?.name || '').trim().toUpperCase() === 'ADMINISTRADOR'
  const [paymentsResult, setPaymentsResult] = useState(null)
  const [paymentsQuery, setPaymentsQuery] = useState('')
  const [paymentStatus, setPaymentStatus] = useState('all')
  const [paymentWorkspace, setPaymentWorkspace] = useState('links')
  const [isLoadingPayments, setIsLoadingPayments] = useState(true)
  const [paymentsError, setPaymentsError] = useState('')
  const [selectedPaymentUser, setSelectedPaymentUser] = useState(null)
  const [isLoadingDetail, setIsLoadingDetail] = useState(false)
  const [activePaymentModal, setActivePaymentModal] = useState(null)
  const [selectedTransaction, setSelectedTransaction] = useState(null)
  const [studentProfile, setStudentProfile] = useState(null)
  const [isLoadingStudent, setIsLoadingStudent] = useState(false)
  const [paymentEntryUser, setPaymentEntryUser] = useState(null)
  const [paymentEntryError, setPaymentEntryError] = useState('')
  const [paymentEntryResult, setPaymentEntryResult] = useState(null)
  const [isSavingPayment, setIsSavingPayment] = useState(false)
  const [invoiceUploadId, setInvoiceUploadId] = useState('')
  const [invoiceUploadError, setInvoiceUploadError] = useState('')
  const [invoiceEntryData, setInvoiceEntryData] = useState(null)
  const [invoiceEntryForm, setInvoiceEntryForm] = useState({ movimiento_id: '', numero_factura: '', file: null })
  const [invoiceEntryError, setInvoiceEntryError] = useState('')
  const [isLoadingInvoiceEntry, setIsLoadingInvoiceEntry] = useState(false)
  const [isBankCatalogOpen, setIsBankCatalogOpen] = useState(false)
  const [generatingReceiptId, setGeneratingReceiptId] = useState('')
  const [copiedPaymentLinkId, setCopiedPaymentLinkId] = useState('')
  const [discountEntryUser, setDiscountEntryUser] = useState(null)
  const [discountEntryForm, setDiscountEntryForm] = useState({ tipo_descuento: 'BECA', porcentaje: '', motivo: '', observacion: '' })
  const [discountEntryError, setDiscountEntryError] = useState('')
  const [discountSuccess, setDiscountSuccess] = useState('')
  const [isSavingDiscount, setIsSavingDiscount] = useState(false)
  const [discountCorrectionPayment, setDiscountCorrectionPayment] = useState(null)
  const [discountCorrectionForm, setDiscountCorrectionForm] = useState({
    tipo_descuento: 'OTRO', porcentaje: '', motivo: '', motivo_correccion: '', observacion: '',
  })
  const [discountCorrectionError, setDiscountCorrectionError] = useState('')
  const [isSavingDiscountCorrection, setIsSavingDiscountCorrection] = useState(false)
  const [paymentEntryForm, setPaymentEntryForm] = useState({
    valor: '',
    forma_pago: 'VOUCHER',
    banco: '',
    numero_deposito: '',
    fecha_deposito: new Date().toISOString().slice(0, 10),
    numero_comprobante: '',
    observacion: '',
    voucher: null,
  })
  const discountPercentage = Number(discountEntryForm.porcentaje || 0)
  const discountCourseValue = Number(discountEntryUser?.total_value || 0)
  const discountPendingBalance = Number(discountEntryUser?.pending_balance || 0)
  const calculatedDiscountValue = (
    discountPercentage >= 1 && discountPercentage <= 100
      ? Math.min(discountPendingBalance, (discountCourseValue * discountPercentage) / 100)
      : 0
  )
  const correctionPercentage = Number(discountCorrectionForm.porcentaje || 0)
  const correctionCourseValue = Number(selectedPaymentUser?.summary?.total_value || 0)
  const otherDiscountValue = Math.max(
    0,
    Number(selectedPaymentUser?.summary?.discount_value || 0)
      - Number(discountCorrectionPayment?.valor_registrado || 0),
  )
  const correctedDiscountValue = (
    correctionPercentage > 0 && correctionPercentage <= 100
      ? Math.min(
        (correctionCourseValue * correctionPercentage) / 100,
        Math.max(0, correctionCourseValue - otherDiscountValue),
      )
      : 0
  )

  const loadRegisteredPayments = useCallback(async ({ page = 1, query = '', status = 'all' } = {}) => {
    setIsLoadingPayments(true)
    setPaymentsError('')

    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: '25',
        payment_status: status,
      })
      if (query.trim()) {
        params.set('q', query.trim())
      }
      const response = await adminFetch(`/api/auth/admin/payments/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar los pagos (${response.status}).`)
      }
      setPaymentsResult(payload.result)
    } catch (error) {
      setPaymentsResult(null)
      setPaymentsError(error.message)
    } finally {
      setIsLoadingPayments(false)
    }
  }, [])

  useEffect(() => {
    const loadTimer = window.setTimeout(() => {
      loadRegisteredPayments({ page: 1, query: '', status: 'all' })
    }, 0)
    return () => window.clearTimeout(loadTimer)
  }, [loadRegisteredPayments])

  async function openPaymentDetail(user) {
    setIsLoadingDetail(true)
    setPaymentsError('')
    setSelectedPaymentUser({ student: user, payments: [] })
    setActivePaymentModal('detail')
    try {
      const params = new URLSearchParams({ codigo_estud: user.codigo_estud })
      if (user.cuenta_id) params.set('cuenta_id', user.cuenta_id)
      const response = await adminFetch(`/api/auth/admin/payments/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar el detalle (${response.status}).`)
      }
      setSelectedPaymentUser(payload.result)
    } catch (error) {
      setPaymentsError(error.message)
      setActivePaymentModal(null)
    } finally {
      setIsLoadingDetail(false)
    }
  }

  function openTransactionDetails(transaction) {
    setSelectedTransaction(transaction)
    setActivePaymentModal('transaction-detail')
  }

  async function openStudentProfile(user) {
    if (!user?.codigo_estud && !user?.cedula) {
      setPaymentsError('No hay código ni cédula para consultar al estudiante.')
      return
    }
    setIsLoadingStudent(true)
    setStudentProfile(null)
    setActivePaymentModal('student-profile')
    try {
      const params = new URLSearchParams()
      if (user.codigo_estud) params.set('student_codigo', user.codigo_estud)
      if (user.cedula) params.set('student_cedula', user.cedula)
      const response = await adminFetch(`/api/auth/admin/payments/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar el estudiante (${response.status}).`)
      }
      setStudentProfile(payload.result)
    } catch (error) {
      setPaymentsError(error.message)
      setActivePaymentModal(null)
    } finally {
      setIsLoadingStudent(false)
    }
  }

  function openPaymentEntry(user) {
    if (isAccountPaid(user)) return
    setPaymentEntryUser(user)
    setPaymentEntryError('')
    setPaymentEntryResult(null)
    setIsBankCatalogOpen(false)
    setPaymentEntryForm({
      valor: '', forma_pago: 'VOUCHER', banco: '', numero_deposito: '',
      fecha_deposito: new Date().toISOString().slice(0, 10), numero_comprobante: '',
      observacion: '', voucher: null,
    })
    setActivePaymentModal('register-payment')
  }

  function openDiscountEntry(user) {
    setDiscountEntryUser(user)
    setDiscountEntryError('')
    setDiscountSuccess('')
    setDiscountEntryForm({ tipo_descuento: 'BECA', porcentaje: '', motivo: '', observacion: '' })
    setActivePaymentModal('register-discount')
  }

  function handleDiscountEntryChange(event) {
    const { name, value } = event.target
    if (name === 'porcentaje') {
      const numericValue = Number(value)
      const boundedValue = value === '' || !Number.isFinite(numericValue)
        ? ''
        : String(Math.min(100, Math.max(0, numericValue)))
      setDiscountEntryForm((current) => ({ ...current, porcentaje: boundedValue }))
      return
    }
    setDiscountEntryForm((current) => ({ ...current, [name]: value }))
  }

  async function handleDiscountEntrySubmit(event) {
    event.preventDefault()
    if (discountPercentage <= 0 || discountPercentage > 100) {
      setDiscountEntryError('Ingresa un porcentaje mayor que 0 y máximo de 100 %.')
      return
    }
    setIsSavingDiscount(true)
    setDiscountEntryError('')
    try {
      const response = await adminFetch('/api/auth/admin/payments/discount/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...discountEntryForm,
          codigo_estud: discountEntryUser.codigo_estud,
          corte_id: discountEntryUser.corte_id,
          estudiante_corte_id: discountEntryUser.estudiante_corte_id,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible registrar el descuento (${response.status}).`)
      }
      const discountLabel = discountEntryForm.tipo_descuento.replaceAll('_', ' ').toLowerCase()
      const appliedValue = payload.result?.value ?? calculatedDiscountValue
      setDiscountSuccess(
        `${discountEntryForm.tipo_descuento === 'BECA' ? 'Beca' : `Descuento por ${discountLabel}`} del ${discountEntryForm.porcentaje} % (${formatMoney(appliedValue)}) aplicado a ${discountEntryUser.nombre}.`,
      )
      setActivePaymentModal(null)
      await loadRegisteredPayments({
        page: paymentsResult?.pagination?.page || 1,
        query: paymentsQuery,
        status: paymentStatus,
      })
    } catch (error) {
      setDiscountEntryError(error.message)
    } finally {
      setIsSavingDiscount(false)
    }
  }

  function handlePaymentEntryChange(event) {
    const { name, value, files } = event.target
    setPaymentEntryForm((current) => ({ ...current, [name]: files ? files[0] || null : value }))
  }

  function selectFinancialInstitution(institutionName) {
    setPaymentEntryForm((current) => ({ ...current, banco: institutionName }))
    setIsBankCatalogOpen(false)
  }

  async function handlePaymentEntrySubmit(event) {
    event.preventDefault()
    setIsSavingPayment(true)
    setPaymentEntryError('')
    setPaymentEntryResult(null)
    try {
      const voucherBase64 = paymentEntryForm.voucher ? await fileAsDataUrl(paymentEntryForm.voucher) : ''
      const response = await adminFetch('/api/auth/admin/payments/register/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...paymentEntryForm,
          voucher: undefined,
          voucher_base64: voucherBase64,
          voucher_name: paymentEntryForm.voucher?.name || '',
          codigo_estud: paymentEntryUser.codigo_estud,
          corte_id: paymentEntryUser.corte_id,
          estudiante_corte_id: paymentEntryUser.estudiante_corte_id,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible registrar el pago (${response.status}).`)
      }
      setPaymentEntryResult(payload.result)
      await loadRegisteredPayments({
        page: paymentsResult?.pagination?.page || 1,
        query: paymentsQuery,
        status: paymentStatus,
      })
    } catch (error) {
      setPaymentEntryError(error.message)
    } finally {
      setIsSavingPayment(false)
    }
  }

  function openDiscountCorrection(payment) {
    const courseValue = Number(selectedPaymentUser?.summary?.total_value || 0)
    setDiscountCorrectionPayment(payment)
    setDiscountCorrectionError('')
    setDiscountCorrectionForm({
      tipo_descuento: discountTypeFromMovement(payment),
      porcentaje: discountPercentageFromMovement(payment, courseValue),
      motivo: '',
      motivo_correccion: '',
      observacion: '',
    })
    setActivePaymentModal('correct-discount')
  }

  function handleDiscountCorrectionChange(event) {
    const { name, value } = event.target
    if (name === 'porcentaje') {
      const numericValue = Number(value)
      const boundedValue = value === '' || !Number.isFinite(numericValue)
        ? ''
        : String(Math.min(100, Math.max(0, numericValue)))
      setDiscountCorrectionForm((current) => ({ ...current, porcentaje: boundedValue }))
      return
    }
    setDiscountCorrectionForm((current) => ({ ...current, [name]: value }))
  }

  async function handleDiscountCorrectionSubmit(event) {
    event.preventDefault()
    const percentage = Number(discountCorrectionForm.porcentaje || 0)
    if (percentage <= 0 || percentage > 100) {
      setDiscountCorrectionError('Ingresa un porcentaje mayor que 0 y máximo de 100 %.')
      return
    }
    setIsSavingDiscountCorrection(true)
    setDiscountCorrectionError('')
    try {
      const response = await adminFetch('/api/auth/admin/payments/discount/correct/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...discountCorrectionForm,
          movimiento_id: discountCorrectionPayment.num,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible corregir el descuento (${response.status}).`)
      }
      const student = selectedPaymentUser?.student
      setDiscountSuccess(payload.message || 'Descuento o beca corregido.')
      setDiscountCorrectionPayment(null)
      if (student) {
        await openPaymentDetail(student)
      } else {
        setActivePaymentModal(null)
      }
      await loadRegisteredPayments({
        page: paymentsResult?.pagination?.page || 1,
        query: paymentsQuery,
        status: paymentStatus,
      })
    } catch (error) {
      setDiscountCorrectionError(error.message)
    } finally {
      setIsSavingDiscountCorrection(false)
    }
  }

  async function uploadPaymentInvoice(payment, file) {
    if (!file || !selectedPaymentUser?.student) return
    const movementId = String(payment?.num || '')
    setInvoiceUploadId(movementId)
    setInvoiceUploadError('')
    try {
      const invoiceBase64 = await fileAsDataUrl(file)
      const response = await adminFetch('/api/auth/admin/payments/invoice/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          movimiento_id: movementId,
          invoice_base64: invoiceBase64,
          invoice_name: file.name,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible guardar la factura (${response.status}).`)
      }
      await Promise.all([
        openPaymentDetail(selectedPaymentUser.student),
        loadRegisteredPayments({
          page: paymentsResult?.pagination?.page || 1,
          query: paymentsQuery,
          status: paymentStatus,
        }),
      ])
    } catch (error) {
      setInvoiceUploadError(error.message)
    } finally {
      setInvoiceUploadId('')
    }
  }

  async function openInvoiceEntry(user) {
    setInvoiceEntryData(null)
    setInvoiceEntryError('')
    setInvoiceEntryForm({ movimiento_id: '', numero_factura: '', file: null })
    setIsLoadingInvoiceEntry(true)
    setActivePaymentModal('upload-invoice')
    try {
      const params = new URLSearchParams({ codigo_estud: user.codigo_estud })
      if (user.cuenta_id) params.set('cuenta_id', user.cuenta_id)
      const response = await adminFetch(`/api/auth/admin/payments/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible cargar los movimientos (${response.status}).`)
      }
      const billablePayments = (payload.result.payments || []).filter(
        (payment) => payment.estado_factura !== 'NO_APLICA',
      )
      if (!billablePayments.length) throw new Error('La cuenta no tiene pagos que puedan facturarse.')
      const preferredPayment = billablePayments.find((payment) => payment.estado_factura === 'PENDIENTE') || billablePayments[0]
      setInvoiceEntryData({ user, payments: billablePayments })
      setInvoiceEntryForm((current) => ({ ...current, movimiento_id: String(preferredPayment.num || '') }))
    } catch (error) {
      setInvoiceEntryError(error.message)
    } finally {
      setIsLoadingInvoiceEntry(false)
    }
  }

  function handleInvoiceEntryChange(event) {
    const { name, value, files } = event.target
    setInvoiceEntryForm((current) => ({ ...current, [name]: files ? files[0] || null : value }))
  }

  async function handleInvoiceEntrySubmit(event) {
    event.preventDefault()
    if (!invoiceEntryForm.file) {
      setInvoiceEntryError('Debes seleccionar el PDF de la factura.')
      return
    }
    setInvoiceUploadId(invoiceEntryForm.movimiento_id)
    setInvoiceEntryError('')
    try {
      const invoiceBase64 = await fileAsDataUrl(invoiceEntryForm.file)
      const response = await adminFetch('/api/auth/admin/payments/invoice/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          movimiento_id: invoiceEntryForm.movimiento_id,
          numero_factura: invoiceEntryForm.numero_factura,
          invoice_base64: invoiceBase64,
          invoice_name: invoiceEntryForm.file.name,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible guardar la factura (${response.status}).`)
      }
      setActivePaymentModal(null)
      setInvoiceEntryData(null)
      await loadRegisteredPayments({
        page: paymentsResult?.pagination?.page || 1,
        query: paymentsQuery,
        status: paymentStatus,
      })
    } catch (error) {
      setInvoiceEntryError(error.message)
    } finally {
      setInvoiceUploadId('')
    }
  }

  async function generatePaymentReceipt(payment) {
    const requestId = String(payment?.inscription_payment_id || '')
    if (!requestId) return
    setGeneratingReceiptId(requestId)
    setPaymentsError('')
    try {
      const response = await adminFetch('/api/auth/admin/payments/receipt/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inscription_payment_id: requestId }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok || !payload.result) {
        throw new Error(payload?.message ?? `No fue posible generar el documento (${response.status}).`)
      }
      const receipt = payload.result
      setPaymentsResult((current) => current ? {
        ...current,
        payment_links: current.payment_links.map((item) => String(item.inscription_payment_id) === requestId
          ? { ...item, receipt_status: receipt.status, receipt_file_name: receipt.file_name, receipt_web_url: receipt.web_url }
          : item),
      } : current)
      setSelectedTransaction((current) => current && String(current.inscription_payment_id) === requestId
        ? { ...current, receipt_status: receipt.status, receipt_file_name: receipt.file_name, receipt_web_url: receipt.web_url }
        : current)
    } catch (error) {
      setPaymentsError(error.message)
    } finally {
      setGeneratingReceiptId('')
    }
  }

  async function copyPaymentLink(payment) {
    const paymentLink = String(payment?.payment_link || '').trim()
    const requestId = String(payment?.inscription_payment_id || '')
    if (!paymentLink || payment?.is_paid) return

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(paymentLink)
      } else {
        const temporaryInput = document.createElement('textarea')
        temporaryInput.value = paymentLink
        temporaryInput.setAttribute('readonly', '')
        temporaryInput.style.position = 'fixed'
        temporaryInput.style.opacity = '0'
        document.body.appendChild(temporaryInput)
        temporaryInput.select()
        const copied = document.execCommand('copy')
        temporaryInput.remove()
        if (!copied) throw new Error('El navegador no permitió copiar el enlace.')
      }
      setCopiedPaymentLinkId(requestId)
      window.setTimeout(() => {
        setCopiedPaymentLinkId((current) => current === requestId ? '' : current)
      }, 2500)
    } catch {
      setPaymentsError('No fue posible copiar el enlace. Puedes abrir los detalles e intentarlo nuevamente.')
    }
  }

  function handleRegisteredPaymentsSearch(event) {
    event.preventDefault()
    loadRegisteredPayments({ page: 1, query: paymentsQuery, status: paymentStatus })
  }

  const normalizedBankSearch = normalizeInstitutionSearch(paymentEntryForm.banco)
  const filteredFinancialInstitutions = ECUADOR_FINANCIAL_INSTITUTIONS
    .filter((institution) => (
      !normalizedBankSearch
      || normalizeInstitutionSearch(institution.name).includes(normalizedBankSearch)
    ))
    .slice(0, FINANCIAL_INSTITUTION_RESULT_LIMIT)

  return (
    <section id="admin-payments" className="admin-payments">
      <div className="admin-section-heading">
        <div>
          <h3>Pagos</h3>
          <p>Consulta estudiantes, enlaces de pago y movimientos de Educación Continua.</p>
        </div>
        <a className="ghost-button compact-button" href="#payment-operations">Operaciones All Digital</a>
      </div>

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Pagos por usuario registrado</h4>
            <p>Consulta matrículas, cuentas y movimientos financieros validados en INTECEDUCONTINUA.</p>
          </div>
          <button
            type="button"
            className="ghost-button compact-button"
            onClick={() => loadRegisteredPayments({
              page: paymentsResult?.pagination?.page || 1,
              query: paymentsQuery,
              status: paymentStatus,
            })}
            disabled={isLoadingPayments}
          >
            Actualizar
          </button>
        </div>

        <form className="admin-form-grid payment-filter-form" onSubmit={handleRegisteredPaymentsSearch}>
          <label className="field">
            <span>Buscar usuario</span>
            <input
              type="search"
              value={paymentsQuery}
              onChange={(event) => setPaymentsQuery(event.target.value)}
              placeholder="Nombre, cédula, matrícula o correo"
            />
          </label>
          <label className="field">
            <span>Estado del pago</span>
            <select value={paymentStatus} onChange={(event) => setPaymentStatus(event.target.value)}>
              <option value="all">Generados y pagados</option>
              <option value="with_payments">Pagados</option>
              <option value="without_payments">Generados sin pago</option>
            </select>
          </label>
          <div className="student-selection-actions full-span">
            <button type="submit" className="submit-button compact-button" disabled={isLoadingPayments}>
              {isLoadingPayments ? 'Consultando...' : 'Buscar pagos'}
            </button>
          </div>
        </form>
        {paymentsError ? <p className="form-error">{paymentsError}</p> : null}
      </article>

      <nav className="payment-workspace-tabs" aria-label="Secciones del módulo de pagos">
        <button
          type="button"
          className={paymentWorkspace === 'links' ? 'is-active' : ''}
          aria-pressed={paymentWorkspace === 'links'}
          onClick={() => setPaymentWorkspace('links')}
        >
          <strong>Pagos por link</strong>
          <span>Solicitudes y transacciones AllDigital</span>
        </button>
        <button
          type="button"
          className={paymentWorkspace === 'accounts' ? 'is-active' : ''}
          aria-pressed={paymentWorkspace === 'accounts'}
          onClick={() => setPaymentWorkspace('accounts')}
        >
          <strong>Cuentas de Educación Continua</strong>
          <span>Cargos, abonos, descuentos y saldos</span>
        </button>
      </nav>

      {paymentWorkspace === 'links' ? (
        <div className="payment-workspace payment-link-workspace">
      {paymentsResult?.payment_link_metrics ? (
        <section className="bulk-summary-grid payment-summary-grid" aria-label="Resumen de enlaces de pago">
          <div>
            <span>Links generados</span>
            <strong>{paymentsResult.payment_link_metrics.generated_links}</strong>
          </div>
          <div>
            <span>Pagos confirmados por link</span>
            <strong>{paymentsResult.payment_link_metrics.paid_links}</strong>
          </div>
          <div>
            <span>Generados sin pago</span>
            <strong>{paymentsResult.payment_link_metrics.generated_pending_links}</strong>
          </div>
          <div>
            <span>Valor generado</span>
            <strong>{formatMoney(paymentsResult.payment_link_metrics.generated_value)}</strong>
          </div>
          <div>
            <span>Valor pagado por link</span>
            <strong>{formatMoney(paymentsResult.payment_link_metrics.paid_value)}</strong>
          </div>
        </section>
      ) : null}

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Estudiantes generados mediante link de pago</h4>
            <p>Muestra cada solicitud generada y diferencia los enlaces pendientes de los pagos confirmados.</p>
          </div>
        </div>
        <div className="admin-table-wrap">
          <table className="admin-table course-cut-table payment-users-table payment-links-table">
            <colgroup>
              <col className="payment-col-student" />
              <col className="payment-col-enrollment" />
              <col className="payment-col-value" />
              <col className="payment-col-status" />
              <col className="payment-col-transaction" />
              <col className="payment-col-date" />
              <col className="payment-col-actions" />
            </colgroup>
            <thead>
              <tr>
                <th>Estudiante</th>
                <th>Matrícula</th>
                <th>Valor</th>
                <th>Estado</th>
                <th>Transacción</th>
                <th>Fecha</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingPayments ? (
                <tr><td colSpan="7">Cargando links generados...</td></tr>
              ) : paymentsResult?.payment_links?.length ? (
                paymentsResult.payment_links.map((paymentLink) => (
                  <tr key={paymentLink.inscription_payment_id}>
                    <td>
                      <strong>{paymentLink.nombre || '-'}</strong>
                      <span>{paymentLink.cedula || '-'} · {paymentLink.email || 'Sin correo'}</span>
                    </td>
                    <td>{paymentLink.matricula || '-'}<span>Solicitud #{paymentLink.inscription_payment_id}</span></td>
                    <td>{formatMoney(paymentLink.amount)}<span>Pagado: {formatMoney(paymentLink.registered_value)}</span></td>
                    <td>
                      <span className={`cut-status-badge ${paymentLink.is_paid ? 'is-open' : 'is-unavailable'}`}>
                        {paymentLink.display_status}
                      </span>
                    </td>
                    <td>{paymentLink.provider_transaction_id || '-'}<span>{paymentLink.codigo_periodo ? `Período ${paymentLink.codigo_periodo}` : ''}</span></td>
                    <td className="payment-link-date">{paymentLink.paid_at || paymentLink.created_at || '-'}</td>
                    <td>
                      <div className="table-actions-stack payment-link-actions">
                        {!paymentLink.is_paid && paymentLink.payment_link ? (
                          <button
                            type="button"
                            className="ghost-button compact-button"
                            onClick={() => copyPaymentLink(paymentLink)}
                          >
                            {copiedPaymentLinkId === String(paymentLink.inscription_payment_id)
                              ? 'Link copiado'
                              : 'Copiar link de pago'}
                          </button>
                        ) : null}
                        {paymentLink.provider_transaction_id ? (
                          <button
                            type="button"
                            className="ghost-button compact-button"
                            onClick={() => openTransactionDetails(paymentLink)}
                          >
                            Detalles de transacción
                          </button>
                        ) : null}
                        <button type="button" className="ghost-button compact-button" onClick={() => openStudentProfile(paymentLink)}>
                          Datos del estudiante
                        </button>
                        {paymentLink.is_paid ? (
                          <button
                            type="button"
                            className="submit-button compact-button"
                            disabled={generatingReceiptId === String(paymentLink.inscription_payment_id)}
                            onClick={() => generatePaymentReceipt(paymentLink)}
                          >
                            {generatingReceiptId === String(paymentLink.inscription_payment_id) ? 'Generando...' : 'Generar documento'}
                          </button>
                        ) : null}
                        {paymentLink.receipt_web_url ? (
                          <a className="ghost-button compact-button" href={paymentLink.receipt_web_url} target="_blank" rel="noreferrer">
                            Comprobante de pago
                          </a>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr><td colSpan="7">No se encontraron links de pago para los filtros seleccionados.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </article>

        </div>
      ) : (
        <div className="payment-workspace payment-account-workspace">

      {paymentsResult ? (
        <section className="bulk-summary-grid payment-summary-grid" aria-label="Resumen general de pagos">
          <div>
            <span>Matrículas registradas</span>
            <strong>{paymentsResult.metrics.registered_users}</strong>
          </div>
          <div>
            <span>Usuarios con pagos</span>
            <strong>{paymentsResult.metrics.users_with_payments}</strong>
          </div>
          <div>
            <span>Movimientos</span>
            <strong>{paymentsResult.metrics.payment_records}</strong>
          </div>
          <div>
            <span>Facturas subidas</span>
            <strong>{paymentsResult.metrics.uploaded_invoices || 0}</strong>
          </div>
          <div>
            <span>Facturas pendientes</span>
            <strong>{paymentsResult.metrics.pending_invoices || 0}</strong>
          </div>
          <div>
            <span>Total facturado</span>
            <strong>{formatMoney(paymentsResult.metrics.total_value)}</strong>
          </div>
          <div>
            <span>Total pagado</span>
            <strong>{formatMoney(paymentsResult.metrics.registered_value)}</strong>
          </div>
          <div>
            <span>Descuentos y becas</span>
            <strong>{formatMoney(paymentsResult.metrics.discount_value)}</strong>
          </div>
        </section>
      ) : null}

      <article className="module-card course-cut-card">
        <div className="module-card-header">
          <div>
            <h4>Cuentas de Educación Continua</h4>
            <p>Los valores corresponden a matrículas, cargos y abonos registrados en INTECEDUCONTINUA.</p>
          </div>
        </div>
        {discountSuccess ? <p className="status-message success payment-discount-success">{discountSuccess}</p> : null}
        <div className="admin-table-wrap">
          <table className="admin-table course-cut-table payment-users-table">
            <thead>
              <tr>
                <th>Usuario</th>
                <th>Identificación</th>
                <th>Movimientos</th>
                <th>Facturación</th>
                <th>Valor curso</th>
                <th>Pagado</th>
                <th>Descuentos / becas</th>
                <th>Último movimiento</th>
                <th>Saldo</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingPayments ? (
                <tr><td colSpan="10">Cargando usuarios y pagos...</td></tr>
              ) : paymentsResult?.users?.length ? (
                paymentsResult.users.map((user) => (
                  <tr key={`${user.codigo_estud}-${user.estudiante_corte_id || user.corte_id}`}>
                    <td><strong>{user.nombre || '-'}</strong><span>{user.course_name || user.email || 'Sin curso'}</span></td>
                    <td>{user.cedula || '-'}<span>{user.cut_name || `Código ${user.codigo_estud}`}</span></td>
                    <td>
                      <span className={`cut-status-badge ${user.payment_count ? 'is-open' : 'is-unavailable'}`}>
                        {user.payment_count ? `${user.payment_count} pagos` : 'Sin pagos'}
                      </span>
                    </td>
                    <td>
                      <span className={`cut-status-badge ${user.invoice_status === 'SUBIDA' ? 'is-open' : 'is-unavailable'}`}>
                        {user.invoice_status === 'SUBIDA' ? 'Subida' : user.invoice_status === 'SIN_PAGOS' ? 'Sin pagos' : 'Pendiente'}
                      </span>
                      {user.pending_invoice_count ? <span>{user.pending_invoice_count} pendiente(s)</span> : null}
                    </td>
                    <td>{formatMoney(user.total_value)}</td>
                    <td>{formatMoney(user.registered_value)}</td>
                    <td>{formatMoney(user.discount_value)}</td>
                    <td>{user.last_payment_date || '-'}<span>{user.last_payment_detail || ''}</span></td>
                    <td>
                      {formatMoney(user.pending_balance)}
                      <span className={`payment-account-status ${isAccountPaid(user) ? 'is-paid' : 'is-pending'}`}>
                        {accountBalanceStatus(user)}
                      </span>
                    </td>
                    <td>
                      <div className="table-actions-stack">
                        <button type="button" className="ghost-button compact-button" onClick={() => openPaymentDetail(user)}>
                          Ver movimientos
                        </button>
                        <button type="button" className="ghost-button compact-button" onClick={() => openStudentProfile(user)}>
                          Datos del estudiante
                        </button>
                        <button type="button" className="submit-button compact-button" disabled={isAccountPaid(user)} onClick={() => openPaymentEntry(user)}>
                          {isAccountPaid(user) ? 'Cuenta pagada' : 'Registrar pago'}
                        </button>
                        <button
                          type="button"
                          className="ghost-button compact-button"
                          disabled={!user.payment_count}
                          onClick={() => openInvoiceEntry(user)}
                        >
                          {!user.payment_count ? 'Sin pago para facturar' : user.invoice_status === 'SUBIDA' ? 'Reemplazar factura' : 'Subir factura'}
                        </button>
                        <button
                          type="button"
                          className="ghost-button compact-button discount-action-button"
                          disabled={Number(user.pending_balance || 0) <= 0}
                          onClick={() => openDiscountEntry(user)}
                        >
                          {Number(user.pending_balance || 0) > 0 ? 'Aplicar descuento o beca' : 'Sin saldo para beneficio'}
                        </button>
                        {isAdministrator && Number(user.discount_value || 0) > 0 ? (
                          <button
                            type="button"
                            className="ghost-button compact-button discount-action-button"
                            onClick={() => openPaymentDetail(user)}
                          >
                            Corregir descuento o beca
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr><td colSpan="10">No se encontraron usuarios para los filtros seleccionados.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {paymentsResult?.pagination ? (
          <div className="payment-pagination">
            <span>
              Página {paymentsResult.pagination.page} de {paymentsResult.pagination.total_pages} · {paymentsResult.pagination.total} usuarios
            </span>
            <div>
              <button
                type="button"
                className="ghost-button compact-button"
                disabled={isLoadingPayments || paymentsResult.pagination.page <= 1}
                onClick={() => loadRegisteredPayments({
                  page: paymentsResult.pagination.page - 1,
                  query: paymentsQuery,
                  status: paymentStatus,
                })}
              >
                Anterior
              </button>
              <button
                type="button"
                className="ghost-button compact-button"
                disabled={isLoadingPayments || paymentsResult.pagination.page >= paymentsResult.pagination.total_pages}
                onClick={() => loadRegisteredPayments({
                  page: paymentsResult.pagination.page + 1,
                  query: paymentsQuery,
                  status: paymentStatus,
                })}
              >
                Siguiente
              </button>
            </div>
          </div>
        ) : null}
      </article>

        </div>
      )}

      {activePaymentModal === 'transaction-detail' && selectedTransaction ? (
        <div className="modal-backdrop" role="presentation">
          <section className="career-modal payment-detail-modal" role="dialog" aria-modal="true" aria-labelledby="transaction-detail-title">
            <div className="career-modal-header">
              <div>
                <h4 id="transaction-detail-title">Detalles de la transacción</h4>
                <p>Solicitud #{selectedTransaction.inscription_payment_id} · {selectedTransaction.nombre}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => setActivePaymentModal(null)}>Cerrar</button>
            </div>
            <div className="career-modal-body">
              <dl className="identity-meta">
                <div><dt>Estado</dt><dd>{selectedTransaction.display_status}</dd></div>
                <div><dt>ID transacción</dt><dd>{selectedTransaction.provider_transaction_id || '-'}</dd></div>
                <div><dt>Valor generado</dt><dd>{formatMoney(selectedTransaction.amount)}</dd></div>
                <div><dt>Valor pagado</dt><dd>{formatMoney(selectedTransaction.registered_value)}</dd></div>
                <div><dt>Matrícula</dt><dd>{selectedTransaction.matricula || '-'}</dd></div>
                <div><dt>Período</dt><dd>{selectedTransaction.codigo_periodo || '-'}</dd></div>
                <div><dt>Fecha de generación</dt><dd>{selectedTransaction.created_at || '-'}</dd></div>
                <div><dt>Fecha confirmada</dt><dd>{selectedTransaction.paid_at || '-'}</dd></div>
                <div><dt>Comprobante</dt><dd>{selectedTransaction.receipt_status || 'PENDIENTE'}</dd></div>
              </dl>
              <div className="module-card"><strong>Concepto</strong><p>{selectedTransaction.description || '-'}</p></div>
              <div className="table-actions-stack">
                {!selectedTransaction.is_paid && selectedTransaction.payment_link ? (
                  <button
                    type="button"
                    className="ghost-button compact-button"
                    onClick={() => copyPaymentLink(selectedTransaction)}
                  >
                    {copiedPaymentLinkId === String(selectedTransaction.inscription_payment_id)
                      ? 'Link copiado'
                      : 'Copiar link de pago'}
                  </button>
                ) : null}
                {selectedTransaction.receipt_web_url ? <a className="ghost-button compact-button" href={selectedTransaction.receipt_web_url} target="_blank" rel="noreferrer">Abrir comprobante PDF</a> : null}
                {selectedTransaction.is_paid ? (
                  <button
                    type="button"
                    className="submit-button compact-button"
                    disabled={generatingReceiptId === String(selectedTransaction.inscription_payment_id)}
                    onClick={() => generatePaymentReceipt(selectedTransaction)}
                  >
                    {generatingReceiptId === String(selectedTransaction.inscription_payment_id) ? 'Generando...' : 'Generar documento'}
                  </button>
                ) : null}
                <button type="button" className="ghost-button compact-button" onClick={() => openStudentProfile(selectedTransaction)}>Datos del estudiante</button>
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {activePaymentModal === 'student-profile' ? (
        <div className="modal-backdrop" role="presentation">
          <section className="career-modal payment-detail-modal" role="dialog" aria-modal="true" aria-labelledby="student-profile-title">
            <div className="career-modal-header">
              <div><h4 id="student-profile-title">Datos del estudiante</h4></div>
              <button type="button" className="ghost-button compact-button" onClick={() => setActivePaymentModal(null)}>Cerrar</button>
            </div>
            <div className="career-modal-body">
              {isLoadingStudent ? <p>Cargando información...</p> : studentProfile?.student ? (
                <>
                  <dl className="student-profile-grid">
                    {Object.entries(studentProfile.student).map(([key, value]) => (
                      <div key={key} className={`student-data-card ${studentFieldSize(key, value)}`}>
                        <dt>{studentFieldLabel(key)}</dt><dd>{studentFieldValue(key, value)}</dd>
                      </div>
                    ))}
                  </dl>
                  <div className="admin-table-wrap">
                    <table className="admin-table"><thead><tr><th>Corte</th><th>Curso</th><th>Estado</th><th>Inicio</th></tr></thead>
                      <tbody>{studentProfile.enrollments?.length ? studentProfile.enrollments.map((item) => (
                        <tr key={item.estudiante_corte_id}><td>{item.corte}</td><td>{item.curso}</td><td>{item.estado}</td><td>{item.fecha_inicio}</td></tr>
                      )) : <tr><td colSpan="4">No registra matrículas académicas asociadas.</td></tr>}</tbody>
                    </table>
                  </div>
                </>
              ) : <p>No se encontró información.</p>}
            </div>
          </section>
        </div>
      ) : null}

      {activePaymentModal === 'register-payment' && paymentEntryUser ? (
        <div className="modal-backdrop" role="presentation">
          <section className="career-modal payment-modal payment-entry-modal" role="dialog" aria-modal="true" aria-labelledby="register-payment-title">
            <div className="career-modal-header">
              <div>
                <h4 id="register-payment-title">Registrar pago en Educación Continua</h4>
                <p>{paymentEntryUser.nombre}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => setActivePaymentModal(null)}>Cerrar</button>
            </div>
            <div className="career-modal-body">
              <div className="payment-entry-context">
                <div><span>Curso</span><strong>{paymentEntryUser.course_name || '-'}</strong></div>
                <div><span>Corte</span><strong>{paymentEntryUser.cut_name || '-'}</strong></div>
                <div><span>Valor del curso</span><strong>{formatMoney(paymentEntryUser.total_value)}</strong></div>
                <div><span>Saldo pendiente</span><strong>{formatMoney(paymentEntryUser.pending_balance)}</strong></div>
              </div>
              <form className="auth-form payment-entry-form" onSubmit={handlePaymentEntrySubmit}>
                <div className="lookup-grid payment-entry-grid">
                  <label className="field"><span>Valor pagado *</span><input name="valor" type="number" min="0.01" max={paymentEntryUser.pending_balance || undefined} step="0.01" required value={paymentEntryForm.valor} onChange={handlePaymentEntryChange} placeholder="0,00" /></label>
                  <label className="field"><span>Forma de pago</span><select name="forma_pago" value={paymentEntryForm.forma_pago} onChange={handlePaymentEntryChange}><option value="VOUCHER">Voucher</option><option value="DEPOSITO">Depósito</option><option value="TRANSFERENCIA">Transferencia</option><option value="EFECTIVO">Efectivo</option></select></label>
                  <div className="field bank-selector-field">
                    <span>Banco o cooperativa</span>
                    <div
                      className={`bank-combobox${isBankCatalogOpen ? ' is-open' : ''}`}
                      onBlur={(event) => {
                        if (!event.currentTarget.contains(event.relatedTarget)) setIsBankCatalogOpen(false)
                      }}
                    >
                      <input
                        name="banco"
                        type="text"
                        role="combobox"
                        aria-autocomplete="list"
                        aria-expanded={isBankCatalogOpen}
                        aria-controls="ecuador-financial-institutions"
                        autoComplete="off"
                        value={paymentEntryForm.banco}
                        onFocus={() => setIsBankCatalogOpen(true)}
                        onChange={(event) => {
                          handlePaymentEntryChange(event)
                          setIsBankCatalogOpen(true)
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') setIsBankCatalogOpen(false)
                        }}
                        placeholder="Escribe para buscar una entidad"
                      />
                      <button
                        type="button"
                        className="bank-combobox-toggle"
                        aria-label={isBankCatalogOpen ? 'Cerrar entidades financieras' : 'Mostrar entidades financieras'}
                        onClick={() => setIsBankCatalogOpen((current) => !current)}
                      >
                        <span aria-hidden="true">⌄</span>
                      </button>
                      {isBankCatalogOpen ? (
                        <div id="ecuador-financial-institutions" className="bank-combobox-menu" role="listbox">
                          <div className="bank-combobox-summary">
                            <strong>Entidades financieras de Ecuador</strong>
                            <span>{normalizedBankSearch ? 'Resultados encontrados' : 'Empieza a escribir para filtrar'}</span>
                          </div>
                          {filteredFinancialInstitutions.length ? filteredFinancialInstitutions.map((institution) => (
                            <button
                              key={`${institution.type}-${institution.name}`}
                              type="button"
                              className="bank-combobox-option"
                              role="option"
                              aria-selected={paymentEntryForm.banco === institution.name}
                              onClick={() => selectFinancialInstitution(institution.name)}
                            >
                              <strong>{institution.name}</strong>
                              <span>{institution.type}</span>
                            </button>
                          )) : (
                            <div className="bank-combobox-empty">
                              No encontramos coincidencias. Puedes conservar el nombre escrito manualmente.
                            </div>
                          )}
                        </div>
                      ) : null}
                    </div>
                    <small className="field-hint">
                      Selecciona una entidad registrada o escribe el nombre si no aparece.
                    </small>
                  </div>
                  <label className="field"><span>Número de depósito</span><input name="numero_deposito" value={paymentEntryForm.numero_deposito} onChange={handlePaymentEntryChange} placeholder="Referencia bancaria" /></label>
                  <label className="field"><span>Fecha de depósito</span><input name="fecha_deposito" type="date" value={paymentEntryForm.fecha_deposito} onChange={handlePaymentEntryChange} /></label>
                  <label className="field"><span>Número de comprobante</span><input name="numero_comprobante" value={paymentEntryForm.numero_comprobante} onChange={handlePaymentEntryChange} placeholder="Código del comprobante" /></label>
                  <label className="field full-span payment-voucher-field">
                    <span>Comprobante adjunto {paymentEntryForm.forma_pago === 'VOUCHER' ? '*' : ''}</span>
                    <input name="voucher" type="file" accept=".pdf,.png,.jpg,.jpeg" required={paymentEntryForm.forma_pago === 'VOUCHER'} onChange={handlePaymentEntryChange} />
                    <small className="field-hint">Formatos permitidos: PDF, PNG o JPG. Tamaño máximo: 5 MB.</small>
                  </label>
                  <label className="field full-span payment-observation-field">
                    <span>Observaciones</span>
                    <textarea name="observacion" maxLength="500" rows="4" value={paymentEntryForm.observacion} onChange={handlePaymentEntryChange} placeholder="Describe cualquier detalle importante del pago, validación o comprobante..." />
                    <small className="field-hint">{paymentEntryForm.observacion.length}/500 caracteres</small>
                  </label>
                </div>
                {paymentEntryError ? <p className="form-error">{paymentEntryError}</p> : null}
                {paymentEntryResult ? (
                  <div className="status-message success voucher-storage-status">
                    <strong>Pago y comprobante guardados correctamente.</strong>
                    <span>Base: {paymentEntryResult.database}</span>
                    {paymentEntryResult.voucher?.location ? <span>OneDrive: {paymentEntryResult.voucher.location}</span> : null}
                    {paymentEntryResult.voucher?.web_url ? <a href={paymentEntryResult.voucher.web_url} target="_blank" rel="noreferrer">Abrir comprobante</a> : null}
                  </div>
                ) : null}
                <button type="submit" className="submit-button payment-entry-submit" disabled={isSavingPayment}>{isSavingPayment ? 'Guardando...' : 'Guardar pago y comprobante'}</button>
              </form>
            </div>
          </section>
        </div>
      ) : null}

      {activePaymentModal === 'upload-invoice' ? (
        <div className="modal-backdrop" role="presentation">
          <section className="career-modal payment-modal payment-entry-modal" role="dialog" aria-modal="true" aria-labelledby="upload-invoice-title">
            <div className="career-modal-header">
              <div>
                <h4 id="upload-invoice-title">Subir factura</h4>
                <p>{invoiceEntryData?.user?.nombre || 'Cuenta de Educación Continua'}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => setActivePaymentModal(null)}>Cerrar</button>
            </div>
            <div className="career-modal-body">
              {isLoadingInvoiceEntry ? <p>Cargando movimientos de pago...</p> : null}
              {invoiceEntryData ? (
                <form className="auth-form payment-entry-form" onSubmit={handleInvoiceEntrySubmit}>
                  <div className="lookup-grid payment-entry-grid">
                    <label className="field full-span">
                      <span>Pago correspondiente *</span>
                      <select name="movimiento_id" required value={invoiceEntryForm.movimiento_id} onChange={handleInvoiceEntryChange}>
                        {invoiceEntryData.payments.map((payment) => (
                          <option key={payment.num} value={payment.num}>
                            {payment.fecha_deposito || payment.fecha_pago || 'Sin fecha'} · {formatMoney(payment.valor_registrado)} · {payment.estado_factura === 'SUBIDA' ? 'Factura subida' : 'Factura pendiente'}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field full-span">
                      <span>Número de factura</span>
                      <input name="numero_factura" maxLength="100" value={invoiceEntryForm.numero_factura} onChange={handleInvoiceEntryChange} placeholder="Ejemplo: 001-001-000012345" />
                    </label>
                    <label className="field full-span payment-voucher-field">
                      <span>Documento de factura PDF *</span>
                      <input name="file" type="file" accept="application/pdf,.pdf" required onChange={handleInvoiceEntryChange} />
                      <small className="field-hint">Solo PDF. Tamaño máximo: 5 MB.</small>
                    </label>
                  </div>
                  {invoiceEntryError ? <p className="form-error">{invoiceEntryError}</p> : null}
                  <button type="submit" className="submit-button payment-entry-submit" disabled={Boolean(invoiceUploadId)}>
                    {invoiceUploadId ? 'Subiendo factura...' : 'Guardar factura'}
                  </button>
                </form>
              ) : null}
              {!isLoadingInvoiceEntry && invoiceEntryError && !invoiceEntryData ? <p className="form-error">{invoiceEntryError}</p> : null}
            </div>
          </section>
        </div>
      ) : null}

      {activePaymentModal === 'register-discount' && discountEntryUser ? (
        <div className="modal-backdrop" role="presentation">
          <section className="career-modal payment-modal payment-entry-modal discount-entry-modal" role="dialog" aria-modal="true" aria-labelledby="register-discount-title">
            <div className="career-modal-header">
              <div><h4 id="register-discount-title">Aplicar descuento o beca al curso</h4><p>{discountEntryUser.nombre}</p></div>
              <button type="button" className="ghost-button compact-button" onClick={() => setActivePaymentModal(null)}>Cerrar</button>
            </div>
            <div className="career-modal-body">
              <div className="payment-entry-context">
                <div><span>Curso</span><strong>{discountEntryUser.course_name || '-'}</strong></div>
                <div><span>Corte</span><strong>{discountEntryUser.cut_name || '-'}</strong></div>
                <div><span>Descuentos y becas</span><strong>{formatMoney(discountEntryUser.discount_value)}</strong></div>
                <div><span>Saldo pendiente</span><strong>{formatMoney(discountEntryUser.pending_balance)}</strong></div>
              </div>
              <div className="discount-balance-preview" aria-live="polite">
                <div><span>Porcentaje a aplicar</span><strong>{discountPercentage || 0} %</strong></div>
                <div><span>Valor calculado</span><strong>- {formatMoney(calculatedDiscountValue)}</strong></div>
                <div><span>Nuevo saldo pendiente</span><strong>{formatMoney(Math.max(0, discountPendingBalance - calculatedDiscountValue))}</strong></div>
              </div>
              <form className="auth-form payment-entry-form" onSubmit={handleDiscountEntrySubmit}>
                <div className="lookup-grid payment-entry-grid">
                  <label className="field"><span>Tipo de beneficio *</span><select name="tipo_descuento" value={discountEntryForm.tipo_descuento} onChange={handleDiscountEntryChange}><option value="BECA">Beca</option><option value="CONVENIO">Convenio</option><option value="PRONTO_PAGO">Pronto pago</option><option value="PROMOCIONAL">Promocional</option><option value="INSTITUCIONAL">Institucional</option><option value="DESCUENTO_REFERIDO">Descuento referido</option><option value="OTRO">Otro</option></select></label>
                  <label className="field"><span>Porcentaje del descuento o beca *</span><input name="porcentaje" type="number" min="0" max="100" step="0.01" required value={discountEntryForm.porcentaje} onChange={handleDiscountEntryChange} placeholder="0 a 100" /><small className="field-hint">Rango permitido: 0 a 100 %. Para aplicar el beneficio debe ser mayor que 0. Se calcula sobre {formatMoney(discountCourseValue)}.</small></label>
                  <label className="field full-span"><span>Motivo *</span><input name="motivo" maxLength="200" required value={discountEntryForm.motivo} onChange={handleDiscountEntryChange} placeholder="Ejemplo: convenio institucional autorizado" /></label>
                  <label className="field full-span payment-observation-field"><span>Observaciones</span><textarea name="observacion" maxLength="500" rows="4" value={discountEntryForm.observacion} onChange={handleDiscountEntryChange} placeholder="Agrega detalles, autorización o condiciones aplicadas..." /><small className="field-hint">{discountEntryForm.observacion.length}/500 caracteres</small></label>
                </div>
                {discountEntryError ? <p className="form-error">{discountEntryError}</p> : null}
                <div className="discount-warning">El descuento o beca reducirá el saldo pendiente, pero no se contabilizará como dinero pagado. Si el cálculo supera el saldo, se aplicará únicamente el valor pendiente.</div>
                <button type="submit" className="submit-button payment-entry-submit" disabled={isSavingDiscount}>{isSavingDiscount ? 'Aplicando...' : discountEntryForm.tipo_descuento === 'BECA' ? 'Guardar beca' : 'Guardar descuento'}</button>
              </form>
            </div>
          </section>
        </div>
      ) : null}

      {activePaymentModal === 'correct-discount' && discountCorrectionPayment && selectedPaymentUser ? (
        <div className="modal-backdrop" role="presentation">
          <section className="career-modal payment-modal payment-entry-modal discount-entry-modal" role="dialog" aria-modal="true" aria-labelledby="correct-discount-title">
            <div className="career-modal-header">
              <div>
                <h4 id="correct-discount-title">Corregir descuento o beca</h4>
                <p>{selectedPaymentUser.student?.nombre || 'Estudiante'} · Movimiento {discountCorrectionPayment.num}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => setActivePaymentModal('detail')}>Cerrar</button>
            </div>
            <div className="career-modal-body">
              <div className="payment-entry-context">
                <div><span>Beneficio actual</span><strong>{discountCorrectionPayment.detalle || '-'}</strong></div>
                <div><span>Valor actual</span><strong>{formatMoney(discountCorrectionPayment.valor_registrado)}</strong></div>
                <div><span>Valor del curso</span><strong>{formatMoney(selectedPaymentUser.summary?.total_value)}</strong></div>
                <div><span>Estado de cuenta</span><strong>{selectedPaymentUser.summary?.payment_status || '-'}</strong></div>
              </div>
              <div className="discount-balance-preview" aria-live="polite">
                <div><span>Nuevo porcentaje</span><strong>{correctionPercentage || 0} %</strong></div>
                <div><span>Nuevo valor calculado</span><strong>{formatMoney(correctedDiscountValue)}</strong></div>
                <div><span>Diferencia</span><strong>{formatMoney(correctedDiscountValue - Number(discountCorrectionPayment.valor_registrado || 0))}</strong></div>
              </div>
              <form className="auth-form payment-entry-form" onSubmit={handleDiscountCorrectionSubmit}>
                <div className="lookup-grid payment-entry-grid">
                  <label className="field"><span>Tipo de beneficio corregido *</span><select name="tipo_descuento" value={discountCorrectionForm.tipo_descuento} onChange={handleDiscountCorrectionChange}><option value="BECA">Beca</option><option value="CONVENIO">Convenio</option><option value="PRONTO_PAGO">Pronto pago</option><option value="PROMOCIONAL">Promocional</option><option value="INSTITUCIONAL">Institucional</option><option value="DESCUENTO_REFERIDO">Descuento referido</option><option value="OTRO">Otro</option></select></label>
                  <label className="field"><span>Nuevo porcentaje *</span><input name="porcentaje" type="number" min="0" max="100" step="0.01" required value={discountCorrectionForm.porcentaje} onChange={handleDiscountCorrectionChange} placeholder="0 a 100" /><small className="field-hint">Puede corregirse aunque la cuenta esté pagada.</small></label>
                  <label className="field full-span"><span>Motivo del beneficio *</span><input name="motivo" maxLength="200" required value={discountCorrectionForm.motivo} onChange={handleDiscountCorrectionChange} placeholder="Motivo que quedará en el nuevo movimiento" /></label>
                  <label className="field full-span"><span>Motivo de la corrección *</span><textarea name="motivo_correccion" maxLength="300" rows="3" required value={discountCorrectionForm.motivo_correccion} onChange={handleDiscountCorrectionChange} placeholder="Explica por qué se corrige el movimiento anterior" /></label>
                  <label className="field full-span"><span>Observaciones</span><textarea name="observacion" maxLength="300" rows="3" value={discountCorrectionForm.observacion} onChange={handleDiscountCorrectionChange} /></label>
                </div>
                {discountCorrectionError ? <p className="form-error">{discountCorrectionError}</p> : null}
                <div className="discount-warning">El movimiento anterior será anulado, no eliminado. El nuevo movimiento quedará relacionado para conservar la trazabilidad financiera.</div>
                <button type="submit" className="submit-button payment-entry-submit" disabled={isSavingDiscountCorrection}>{isSavingDiscountCorrection ? 'Corrigiendo...' : 'Guardar corrección'}</button>
              </form>
            </div>
          </section>
        </div>
      ) : null}

      {activePaymentModal === 'detail' ? (
        <div className="modal-backdrop" role="presentation">
          <section className="career-modal payment-detail-modal" role="dialog" aria-modal="true" aria-labelledby="payment-detail-title">
            <div className="career-modal-header">
              <div>
                <h4 id="payment-detail-title">Detalle de pagos</h4>
                <p>{selectedPaymentUser?.student?.nombre || 'Usuario registrado'} · Código {selectedPaymentUser?.student?.codigo_estud || '-'}</p>
              </div>
              <button type="button" className="ghost-button compact-button" onClick={() => setActivePaymentModal(null)}>
                Cerrar
              </button>
            </div>
            <div className="career-modal-body">
              {selectedPaymentUser?.summary ? (
                <div className="payment-detail-summary">
                  <div><span>Valor total</span><strong>{formatMoney(selectedPaymentUser.summary.total_value)}</strong></div>
                  <div><span>Valor cancelado</span><strong>{formatMoney(selectedPaymentUser.summary.registered_value)}</strong></div>
                  <div><span>Descuentos y becas</span><strong>{formatMoney(selectedPaymentUser.summary.discount_value)}</strong></div>
                  <div><span>Valor pendiente</span><strong>{formatMoney(selectedPaymentUser.summary.pending_balance)}</strong></div>
                  <div className={`payment-detail-status ${selectedPaymentUser.summary.payment_status === 'PAGADO' ? 'is-paid' : 'is-pending'}`}>
                    <span>Estado</span><strong>{selectedPaymentUser.summary.payment_status || 'PENDIENTE'}</strong>
                  </div>
                </div>
              ) : null}
              {invoiceUploadError ? <p className="form-error">{invoiceUploadError}</p> : null}
              <div className="admin-table-wrap">
                <table className="admin-table payment-detail-table">
                  <thead>
                    <tr>
                      <th>Fecha</th><th>Detalle</th><th>Período</th><th>Cargo</th><th>Valor cancelado</th><th>Estado</th><th>Banco / referencia / comprobante</th><th>Facturación</th>{isAdministrator ? <th>Administración</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {isLoadingDetail ? (
                      <tr><td colSpan={isAdministrator ? 9 : 8}>Cargando movimientos...</td></tr>
                    ) : selectedPaymentUser?.payments?.length ? (
                      selectedPaymentUser.payments.map((payment) => (
                        <tr key={`${payment.codigo_periodo}-${payment.num}`}>
                          <td>{payment.fecha_deposito || payment.fecha_pago || '-'}</td>
                          <td>{payment.detalle || '-'}</td>
                          <td>{payment.codigo_periodo || '-'}</td>
                          <td>{formatMoney(payment.valor)}</td>
                          <td>{formatMoney(payment.valor_registrado)}</td>
                          <td><span className={`cut-status-badge ${payment.estado_cuenta === 'PAGADO' ? 'is-open' : 'is-unavailable'}`}>{payment.estado_cuenta || 'PENDIENTE'}</span></td>
                          <td>
                            {payment.banco || '-'}
                            <span>{payment.referencia || payment.numero_deposito || ''}</span>
                            {payment.url_deposito ? <a href={payment.url_deposito} target="_blank" rel="noreferrer">Abrir comprobante</a> : null}
                          </td>
                          <td>
                            {payment.estado_factura === 'NO_APLICA' ? (
                              <span>No aplica</span>
                            ) : (
                              <>
                                <span className={`cut-status-badge ${payment.estado_factura === 'SUBIDA' ? 'is-open' : 'is-unavailable'}`}>
                                  {payment.estado_factura === 'SUBIDA' ? 'Subida' : 'Pendiente'}
                                </span>
                                {payment.url_factura ? <a href={payment.url_factura} target="_blank" rel="noreferrer">Abrir factura</a> : null}
                                <label className="ghost-button compact-button invoice-upload-button">
                                  {invoiceUploadId === String(payment.num) ? 'Subiendo...' : payment.estado_factura === 'SUBIDA' ? 'Reemplazar PDF' : 'Subir factura PDF'}
                                  <input
                                    type="file"
                                    accept="application/pdf,.pdf"
                                    disabled={invoiceUploadId === String(payment.num)}
                                    onChange={(event) => {
                                      const file = event.target.files?.[0]
                                      event.target.value = ''
                                      uploadPaymentInvoice(payment, file)
                                    }}
                                  />
                                </label>
                              </>
                            )}
                          </td>
                          {isAdministrator ? (
                            <td>
                              {Number(payment.descuento_corregible || 0) === 1 ? (
                                <button type="button" className="ghost-button compact-button" onClick={() => openDiscountCorrection(payment)}>
                                  Corregir
                                </button>
                              ) : <span>No aplica</span>}
                            </td>
                          ) : null}
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={isAdministrator ? 9 : 8}>El usuario no tiene movimientos registrados.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}

