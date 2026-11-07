<#
  Start SIMS Wi-Fi Manager (serves API + built web UI on http://127.0.0.1:8000)
  Usage:  .\run.ps1            normal
          .\run.ps1 -Dev       also start the Vite dev server on http://localhost:5173 (hot reload)
          .\run.ps1 -Lan       listen on all network interfaces. Needed ONLY for firmware staging / certificate upload, because
                              the APs must reach this computer. Also set SIMS_PUBLIC_BASE_URL in .env (see README).
#>
param([switch]$Dev, [switch]$Lan)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run .\setup.ps1 first." }

if ($Dev) {
    Start-Process powershell -ArgumentList "-NoExit","-Command","Set-Location '$PSScriptRoot\frontend'; npm run dev"
}
# Bound to localhost on purpose. Put a reverse proxy with TLS in front for any shared use.
$bind = "127.0.0.1"
if ($Lan) {
    $bind = "0.0.0.0"
    Write-Warning "Listening on ALL interfaces. Allow TCP 8000 only from your AP network in the Windows firewall."
}
& $py -m uvicorn backend.main:app --host $bind --port 8000
