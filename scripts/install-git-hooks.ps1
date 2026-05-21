$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$deployScript = Join-Path $repoRoot 'scripts\deploy-after-pull.ps1'
$logFile = Join-Path $repoRoot 'backend\logs\deploy-after-pull.log'

$hook = @"
#!/bin/sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$deployScript" >> "$logFile" 2>&1
"@

foreach ($hookName in @('post-merge', 'post-rewrite')) {
    $hookPath = Join-Path $repoRoot ".git\hooks\$hookName"
    Set-Content -Path $hookPath -Value $hook -Encoding ASCII
    Write-Host "Hook instalado: $hookPath"
}
