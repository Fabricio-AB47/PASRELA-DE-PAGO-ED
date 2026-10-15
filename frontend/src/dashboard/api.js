import { clearStoredSession, getStoredSession } from '../shared.js'

const TRANSIENT_GET_STATUSES = new Set([500, 502, 503, 504])

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

export async function adminFetch(url, options = {}) {
  const sessionToken = getStoredSession()?.session_token

  if (!sessionToken) {
    clearStoredSession()
    window.location.replace('/login/')
    throw new Error('Debes iniciar sesión para acceder al dashboard.')
  }

  const headers = new Headers(options.headers || {})
  headers.set('Authorization', `Bearer ${sessionToken}`)

  const requestOptions = {
    cache: 'no-store',
    ...options,
    headers,
  }
  const method = String(requestOptions.method || 'GET').toUpperCase()
  const attempts = method === 'GET' ? 2 : 1
  let lastError = null

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let response
    try {
      response = await fetch(url, requestOptions)
    } catch (error) {
      lastError = error
      if (attempt + 1 < attempts) {
        await wait(750)
        continue
      }
      throw error
    }

    if (response.status === 401) {
      clearStoredSession()
      window.location.replace('/login/')
    }
    if (attempt + 1 < attempts && TRANSIENT_GET_STATUSES.has(response.status)) {
      await wait(750)
      continue
    }
    return response
  }

  throw lastError || new Error('No fue posible conectar con el servidor.')
}
