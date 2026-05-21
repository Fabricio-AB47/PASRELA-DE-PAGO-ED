$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$frontendDir = Join-Path $repoRoot 'frontend'
$distDir = Join-Path $frontendDir 'dist'
$targetDir = 'C:\inetpub\wwwroot\pasarela_pago'
$targetRoot = 'C:\inetpub\wwwroot\pasarela_pago'
$logDir = Join-Path $repoRoot 'backend\logs'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Step {
    param([string] $Message)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$timestamp] $Message"
}

Write-Step 'Instalando dependencias del frontend...'
Push-Location $frontendDir
try {
    npm install
    Write-Step 'Compilando frontend...'
    npm run build
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
