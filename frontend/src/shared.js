export const SESSION_STORAGE_KEY = 'intec-dashboard-session'

const PERSON_NAME_KEYS = new Set([
  'administrador',
  'administrativo',
  'apellidos',
  'apellidosnombre',
  'apellidosnombres',
  'docente',
  'docentenombre',
  'estudiante',
  'nombrecompleto',
  'nombredocente',
  'nombreestudiante',
  'nombreusuario',
  'nombres',
  'responsablenombre',
  'studentname',
  'teachername',
  'usuarionombre',
])

const PERSON_RECORD_KEYS = new Set([
  'category',
  'cedula',
  'ceduladoc',
  'cedulaest',
  'codigoestud',
  'codigoestudiante',
  'codigodoc',
  'codigodocente',
  'correointec',
  'correopersonal',
  'corteestudianteid',
  'docentecorteid',
  'email',
  'estudiantecorteid',
  'identificacion',
  'login',
  'movil',
  'role',
  'telefono',
  'tipousuario',
])

function normalizedKey(key) {
  return String(key).toLowerCase().replace(/[^a-z]/g, '')
}

export function formatPersonName(value) {
  return typeof value === 'string' ? value.trim().toLocaleUpperCase('es-EC') : value
}

export function normalizePersonNames(value) {
  if (Array.isArray(value)) {
    return value.map(normalizePersonNames)
  }
  if (!value || typeof value !== 'object') {
    return value
  }

  const recordKeys = Object.keys(value).map(normalizedKey)
  const isPersonRecord = recordKeys.some((key) => PERSON_RECORD_KEYS.has(key))

  return Object.fromEntries(Object.entries(value).map(([key, fieldValue]) => {
    const cleanKey = normalizedKey(key)
    const isPersonName = PERSON_NAME_KEYS.has(cleanKey) || (
      isPersonRecord && (cleanKey === 'nombre' || cleanKey === 'displayname')
    )
    if (isPersonName && typeof fieldValue === 'string') {
      return [key, formatPersonName(fieldValue)]
    }
    return [key, normalizePersonNames(fieldValue)]
  }))
}

export function getStoredSession() {
  try {
    const rawSession =
      window.sessionStorage.getItem(SESSION_STORAGE_KEY) ||
      window.localStorage.getItem(SESSION_STORAGE_KEY)
    return rawSession ? normalizePersonNames(JSON.parse(rawSession)) : null
  } catch {
    return null
  }
}

export function setStoredSession(session) {
  window.localStorage.removeItem(SESSION_STORAGE_KEY)
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(normalizePersonNames(session)))
}

export function clearStoredSession() {
  window.sessionStorage.removeItem(SESSION_STORAGE_KEY)
  window.localStorage.removeItem(SESSION_STORAGE_KEY)
}

export async function readResponsePayload(response) {
  const rawBody = await response.text()
  if (!rawBody) {
    return null
  }

  try {
    return normalizePersonNames(JSON.parse(rawBody))
  } catch {
    return {
      ok: false,
      invalid_response: true,
      message: `El servidor devolvió una respuesta inválida (${response.status}).`,
    }
  }
}

export function filenameFromContentDisposition(response, fallbackName) {
  const disposition = response.headers.get('Content-Disposition') || ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1].replace(/["']/g, ''))
  }

  const plainMatch = disposition.match(/filename="?([^";]+)"?/i)
  return plainMatch?.[1] || fallbackName
}

export async function downloadBlobResponse(response, fallbackName) {
  const blob = await response.blob()
  const downloadUrl = window.URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = downloadUrl
  anchor.download = filenameFromContentDisposition(response, fallbackName)
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.URL.revokeObjectURL(downloadUrl)
}
