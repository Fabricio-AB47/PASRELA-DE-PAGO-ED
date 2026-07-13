import { clearStoredSession, getStoredSession } from '../shared.js'

export function adminFetch(url, options = {}) {
  const sessionToken = getStoredSession()?.session_token

  if (!sessionToken) {
    clearStoredSession()
    window.location.replace('/login/')
    throw new Error('Debes iniciar sesión para acceder al dashboard.')
  }

  const headers = new Headers(options.headers || {})
  headers.set('Authorization', `Bearer ${sessionToken}`)

  return fetch(url, {
    cache: 'no-store',
    ...options,
    headers,
  }).then((response) => {
    if (response.status === 401 || response.status === 403) {
      clearStoredSession()
      window.location.replace('/login/')
    }
    return response
  })
}
