export const SESSION_STORAGE_KEY = 'intec-dashboard-session'

export function getStoredSession() {
  try {
    const rawSession = window.localStorage.getItem(SESSION_STORAGE_KEY)
    return rawSession ? JSON.parse(rawSession) : null
  } catch {
    return null
  }
}

export function setStoredSession(session) {
  window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
}

export function clearStoredSession() {
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
      message: `El servidor devolvio una respuesta invalida (${response.status}).`,
    }
  }
}
