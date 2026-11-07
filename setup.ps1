<#
  SIMS Wi-Fi Manager - Windows setup (Phase 1)
  Run from the project root:   powershell -ExecutionPolicy Bypass -File .\setup.ps1
  Safe to re-run: it never overwrites an existing .env.
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found. $hint" }
}
Need python "Install Python 3.13 from python.org (tick 'Add to PATH')."
Need node   "Install Node.js 20+ LTS from nodejs.org (needed for the web UI)."

Write-Host "==> Creating virtual environment (.venv)"
if (-not (Test-Path .venv)) { python -m venv .venv }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "==> Installing Python dependencies"
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Write-Host "==> Creating .env with freshly generated secrets"
    $jwt    = & $py -c "import secrets; print(secrets.token_urlsafe(64))"
    $fernet = & $py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    $sec = Read-Host "Choose the admin password for the web UI (12+ characters)" -AsSecureString
    $pw  = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    if ($pw.Length -lt 12) { throw "Admin password must be at least 12 characters." }
    $env_text = Get-Content .env.example -Raw
    $env_text = $env_text -replace "(?m)^SIMS_JWT_SECRET=.*$",     "SIMS_JWT_SECRET=$jwt"
    $env_text = $env_text -replace "(?m)^SIMS_FERNET_KEY=.*$",     "SIMS_FERNET_KEY=$fernet"
    $env_text = $env_text -replace "(?m)^SIMS_ADMIN_PASSWORD=.*$", "SIMS_ADMIN_PASSWORD=$pw"
    Set-Content -Path .env -Value $env_text -Encoding utf8
    $pw = $null
    Write-Host "    .env created. Keep it private - it holds your encryption key. Back it up:"
    Write-Host "    losing SIMS_FERNET_KEY makes stored AP credentials unreadable."
} else {
    Write-Host "==> .env already exists - left unchanged"
}

Write-Host "==> Installing and building the web UI"
Push-Location frontend
npm install
npm run build
Pop-Location

Write-Host "==> Running automated tests (mock Aruba responses, no hardware touched)"
& $py -m pytest -q

Write-Host ""
Write-Host "Setup complete. Start the app with:   .\run.ps1"
Write-Host "Then open http://127.0.0.1:8000  (user: admin, password: the one you just chose)"
