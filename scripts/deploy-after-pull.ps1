$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$frontendDir = Join-Path $repoRoot 'frontend'
$distDir = Join-Path $frontendDir 'dist'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$requirementsFile = Join-Path $repoRoot 'backend\requirements.txt'
$targetDir = 'C:\inetpub\wwwroot\pasarela_pago'
$targetRoot = 'C:\inetpub\wwwroot\pasarela_pago'
$logDir = Join-Path $repoRoot 'backend\logs'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Step {
    param([string] $Message)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$timestamp] $Message"
}

function Invoke-Checked {
    param(
        [string] $FilePath,
        [string[]] $Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Comando fallo con codigo ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Test-VenvPython {
    if (-not (Test-Path $venvPython)) {
        return $false
    }

    try {
        & $venvPython --version | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

if (-not (Test-VenvPython)) {
    Write-Step 'Entorno virtual invalido o ausente. Recreando .venv...'
    py -3.14 -m venv --clear (Join-Path $repoRoot '.venv')
}

Write-Step 'Instalando dependencias del backend...'
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', $requirementsFile)
Write-Step 'Validando backend Django...'
Invoke-Checked $venvPython @((Join-Path $repoRoot 'backend\E_Cont\manage.py'), 'check')
Write-Step 'Validando conexion a SQL Server...'
$dbCheckPath = Join-Path $logDir 'check-db-connection.py'
@"
import os
import sys

sys.path.insert(0, r'$($repoRoot.Path)\backend\E_Cont')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'E_Cont.settings')

import django

django.setup()

from django.conf import settings
from django.db import connection

database = settings.DATABASES['default']
print('SQL Server: {host}:{port} / {name} / user={user}'.format(
    host=database['HOST'],
    port=database['PORT'],
    name=database['NAME'],
    user=database['USER'],
))
with connection.cursor() as cursor:
    cursor.execute('SELECT 1')
    print('Conexion SQL OK: {0}'.format(cursor.fetchone()[0]))
"@ | Set-Content -Path $dbCheckPath -Encoding UTF8
try {
    Invoke-Checked $venvPython @($dbCheckPath)
}
finally {
    Remove-Item -LiteralPath $dbCheckPath -Force -ErrorAction SilentlyContinue
}

Write-Step 'Instalando dependencias del frontend...'
Push-Location $frontendDir
try {
    npm install
    if ($LASTEXITCODE -ne 0) {
        throw "npm install fallo con codigo $LASTEXITCODE"
    }
    Write-Step 'Compilando frontend...'
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build fallo con codigo $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path $distDir)) {
    throw "No se encontro el directorio de build: $distDir"
}

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$resolvedTarget = (Resolve-Path $targetDir).Path.TrimEnd('\')
$expectedTarget = (Resolve-Path $targetRoot).Path.TrimEnd('\')
if ($resolvedTarget -ne $expectedTarget) {
    throw "Ruta de despliegue inesperada: $resolvedTarget"
}

Write-Step "Actualizando archivos en $targetDir..."
Get-ChildItem -LiteralPath $targetDir -Force | Remove-Item -Recurse -Force
Copy-Item -Path (Join-Path $distDir '*') -Destination $targetDir -Recurse -Force
Copy-Item -Path (Join-Path $frontendDir 'web.config') -Destination (Join-Path $targetDir 'web.config') -Force

try {
    Import-Module WebAdministration -ErrorAction Stop
    $poolName = (Get-Item 'IIS:\Sites\ed_continua').applicationPool
    Restart-WebAppPool -Name $poolName
    Write-Step "Application pool reiniciado: $poolName"
}
catch {
    Write-Step "No se pudo reiniciar IIS automaticamente: $($_.Exception.Message)"
}

Write-Step 'Despliegue terminado.'
