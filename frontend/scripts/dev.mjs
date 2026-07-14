import { existsSync } from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import { dirname, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(scriptDir, '..')
const repoRoot = resolve(frontendDir, '..')
const backendDir = resolve(repoRoot, 'backend')
const managePy = resolve(backendDir, 'manage.py')
const venvPython = resolve(repoRoot, '.venv', 'Scripts', 'python.exe')
const python = existsSync(venvPython) ? venvPython : 'python'
const viteBin = resolve(frontendDir, 'node_modules', 'vite', 'bin', 'vite.js')
const minBackendPort = 8000
const maxBackendPort = 8020
const minFrontendPort = 5174
const maxFrontendPort = 5194

let backendProcess = null
let viteProcess = null
let backendPort = null
let frontendPort = null

function requestedBackendPort() {
  const inlineArgument = process.argv.find((argument) => argument.startsWith('--backend-port='))
  const argumentIndex = process.argv.indexOf('--backend-port')
  const rawPort =
    inlineArgument?.split('=', 2)[1] ||
    (argumentIndex >= 0 ? process.argv[argumentIndex + 1] : '') ||
    process.env.BACKEND_PORT ||
    ''

  if (!rawPort || rawPort.toLowerCase() === 'auto') {
    return null
  }

  const port = Number(rawPort)
  if (!Number.isInteger(port) || port < minBackendPort || port > maxBackendPort) {
    throw new Error(
      `El puerto del backend debe ser un entero entre ${minBackendPort} y ${maxBackendPort}.`,
    )
  }
  return port
}

function backendHealthUrl(port) {
  return `http://127.0.0.1:${port}/api/auth/health/`
}

function frontendUrl(port) {
  return `http://127.0.0.1:${port}/`
}

function requestedFrontendPort() {
  const inlineArgument = process.argv.find((argument) => argument.startsWith('--frontend-port='))
  const argumentIndex = process.argv.indexOf('--frontend-port')
  const rawPort =
    inlineArgument?.split('=', 2)[1] ||
    (argumentIndex >= 0 ? process.argv[argumentIndex + 1] : '') ||
    process.env.FRONTEND_PORT ||
    ''

  if (!rawPort || rawPort.toLowerCase() === 'auto') {
    return null
  }

  const port = Number(rawPort)
  if (!Number.isInteger(port) || port < minFrontendPort || port > maxFrontendPort) {
    throw new Error(
      `El puerto del frontend debe ser un entero entre ${minFrontendPort} y ${maxFrontendPort}.`,
    )
  }
  return port
}

function isPortAvailable(port) {
  return new Promise((resolveCheck) => {
    const server = net.createServer()
    server.unref()
    server.once('error', () => resolveCheck(false))
    server.listen({ host: '127.0.0.1', port }, () => {
      server.close(() => resolveCheck(true))
    })
  })
}

function checkBackend(port) {
  return new Promise((resolveCheck) => {
    const request = http.get(backendHealthUrl(port), (response) => {
      let rawBody = ''
      response.setEncoding('utf8')
      response.on('data', (chunk) => {
        rawBody += chunk
      })
      response.on('end', () => {
        try {
          const payload = JSON.parse(rawBody)
          resolveCheck(
            response.statusCode === 200 &&
              payload?.ok === true &&
              payload?.service === 'dashboard-auth',
          )
        } catch {
          resolveCheck(false)
        }
      })
    })

    request.setTimeout(1000, () => {
      request.destroy()
      resolveCheck(false)
    })
    request.on('error', () => resolveCheck(false))
  })
}

async function waitForBackend() {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (await checkBackend(backendPort)) {
      return true
    }
    await new Promise((resolveWait) => {
      setTimeout(resolveWait, 1000)
    })
  }
  return false
}

async function selectBackendPort() {
  const requestedPort = requestedBackendPort()
  if (requestedPort !== null) {
    if ((await checkBackend(requestedPort)) || (await isPortAvailable(requestedPort))) {
      return requestedPort
    }
    throw new Error(
      `El puerto ${requestedPort} está ocupado por otro servicio. Elige otro entre ${minBackendPort} y ${maxBackendPort}.`,
    )
  }

  for (let port = minBackendPort; port <= maxBackendPort; port += 1) {
    if (await isPortAvailable(port)) {
      return port
    }
  }
  throw new Error(`No hay puertos disponibles entre ${minBackendPort} y ${maxBackendPort}.`)
}

async function selectFrontendPort() {
  const requestedPort = requestedFrontendPort()
  if (requestedPort !== null) {
    if (await isPortAvailable(requestedPort)) {
      return requestedPort
    }
    throw new Error(
      `El puerto frontend ${requestedPort} está ocupado. Elige otro entre ${minFrontendPort} y ${maxFrontendPort}.`,
    )
  }

  for (let port = minFrontendPort; port <= maxFrontendPort; port += 1) {
    if (await isPortAvailable(port)) {
      return port
    }
  }
  throw new Error(`No hay puertos frontend disponibles entre ${minFrontendPort} y ${maxFrontendPort}.`)
}

function startBackend() {
  backendProcess = spawn(
    python,
    [managePy, 'runserver', '--noreload', `127.0.0.1:${backendPort}`],
    {
      cwd: backendDir,
      stdio: 'inherit',
      env: {
        ...process.env,
        BACKEND_PORT: String(backendPort),
        FRONTEND_PORT: String(frontendPort),
        PYTHONUNBUFFERED: '1',
        // runserver usa HTTP. Estas excepciones se aplican solo al proceso
        // local iniciado por npm run dev; producción conserva HTTPS estricto.
        DJANGO_SECURE_SSL_REDIRECT: '0',
        DJANGO_SESSION_COOKIE_SECURE: '0',
        DJANGO_CSRF_COOKIE_SECURE: '0',
        DJANGO_SECURE_HSTS_SECONDS: '0',
      },
    },
  )

  backendProcess.on('exit', (code) => {
    if (viteProcess && !viteProcess.killed) {
      viteProcess.kill()
    }
    if (code && code !== 0) {
      process.exitCode = code
    }
  })
}

function startVite() {
  viteProcess = spawn(
    process.execPath,
    [viteBin, '--host', '127.0.0.1', '--port', String(frontendPort), '--strictPort'],
    {
      cwd: frontendDir,
      stdio: 'inherit',
      env: {
        ...process.env,
        BACKEND_PORT: String(backendPort),
        FRONTEND_PORT: String(frontendPort),
      },
    },
  )

  viteProcess.on('exit', (code) => {
    if (backendProcess && !backendProcess.killed) {
      backendProcess.kill()
    }
    process.exit(code || 0)
  })
}

function shutdown() {
  if (viteProcess && !viteProcess.killed) {
    viteProcess.kill()
  }
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill()
  }
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)

try {
  backendPort = await selectBackendPort()
  frontendPort = await selectFrontendPort()
} catch (error) {
  console.error(error.message)
  process.exit(1)
}

if (!(await checkBackend(backendPort))) {
  console.log(`Iniciando backend Django en http://127.0.0.1:${backendPort} ...`)
  startBackend()
  const isBackendReady = await waitForBackend()
  if (!isBackendReady) {
    shutdown()
    console.error(`No fue posible iniciar el backend en http://127.0.0.1:${backendPort}.`)
    process.exit(1)
  }
} else {
  console.log(`Backend Django disponible en http://127.0.0.1:${backendPort}.`)
}

console.log(`Iniciando frontend Vite en ${frontendUrl(frontendPort)} ...`)
startVite()
