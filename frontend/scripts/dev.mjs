import { existsSync } from 'node:fs'
import http from 'node:http'
import { dirname, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(scriptDir, '..')
const repoRoot = resolve(frontendDir, '..')
const backendDir = resolve(repoRoot, 'backend')
const managePy = resolve(backendDir, 'E_Cont', 'manage.py')
const venvPython = resolve(repoRoot, '.venv', 'Scripts', 'python.exe')
const python = existsSync(venvPython) ? venvPython : 'python'
const viteBin = resolve(frontendDir, 'node_modules', 'vite', 'bin', 'vite.js')
const backendHealthUrl = 'http://127.0.0.1:8000/api/auth/health/'
const frontendUrl = 'http://127.0.0.1:5174/'

let backendProcess = null
let viteProcess = null

function checkUrl(url) {
  return new Promise((resolveCheck) => {
    const request = http.get(url, (response) => {
      response.resume()
      resolveCheck(response.statusCode >= 200 && response.statusCode < 500)
    })

    request.setTimeout(1000, () => {
      request.destroy()
      resolveCheck(false)
    })

    request.on('error', () => resolveCheck(false))
  })
}

function checkBackend() {
  return checkUrl(backendHealthUrl)
}

async function waitForBackend() {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (await checkBackend()) {
      return true
    }
    await new Promise((resolveWait) => {
      setTimeout(resolveWait, 1000)
    })
  }
  return false
}

function startBackend() {
  backendProcess = spawn(
    python,
    [managePy, 'runserver', '--noreload', '127.0.0.1:8000'],
    {
      cwd: backendDir,
      stdio: 'inherit',
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
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
    [viteBin, '--host', '127.0.0.1', '--port', '5174', '--strictPort'],
    {
      cwd: frontendDir,
      stdio: 'inherit',
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

const frontendAlreadyRunning = await checkUrl(frontendUrl)

if (!(await checkBackend())) {
  console.log('Iniciando backend Django en http://127.0.0.1:8000 ...')
  startBackend()
  const isBackendReady = await waitForBackend()
  if (!isBackendReady) {
    shutdown()
    console.error('No fue posible iniciar el backend en http://127.0.0.1:8000.')
    process.exit(1)
  }
} else {
  console.log('Backend Django disponible en http://127.0.0.1:8000.')
}

if (frontendAlreadyRunning) {
  console.log('Frontend Vite ya está disponible en http://127.0.0.1:5174/.')
  if (!backendProcess) {
    process.exit(0)
  }
} else {
  startVite()
}
