export const SESSION_STORAGE_KEY = 'intec-dashboard-session'

export function getStoredSession() {
  try {
    const rawSession =
      window.sessionStorage.getItem(SESSION_STORAGE_KEY) ||
      window.localStorage.getItem(SESSION_STORAGE_KEY)
    return rawSession ? JSON.parse(rawSession) : null
  } catch {
    return null
  }
}

export function setStoredSession(session) {
  window.localStorage.removeItem(SESSION_STORAGE_KEY)
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
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
    return JSON.parse(rawBody)
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
