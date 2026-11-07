#Requires -Version 5.1
<#
.SYNOPSIS
  Import the supplied INM Phase 1 source ZIP into the private NetworkManager3 GitHub repository.
.DESCRIPTION
  Run on a trusted Windows workstation with Git installed and authorized GitHub access.
  Imports SOURCE CODE and deployment scripts (not screenshots, embedded Git bundle or local secrets).
  Preserves this repository's README and review documents.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\import_phase1_source.ps1 -ZipPath "C:\Downloads\INM-1.0.0-phase1.zip"
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$ZipPath,
  [string]$Repository = "git@github.com:nigelebrown/NetworkManager3.git",
  [string]$Branch = "main",
  [switch]$NoPush
)
$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $ZipPath).Path
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is required on this workstation." }
if ([IO.Path]::GetExtension($resolved).ToLowerInvariant() -ne ".zip") { throw "Expected a ZIP archive." }
$workspace = Join-Path ([IO.Path]::GetTempPath()) ("inm-import-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $workspace | Out-Null
try {
  $extracted = Join-Path $workspace "extracted"
  $repoPath = Join-Path $workspace "repo"
  Expand-Archive -LiteralPath $resolved -DestinationPath $extracted -Force
  $source = Join-Path $extracted "inm-1.0.0-phase1"
  if (-not (Test-Path (Join-Path $source "release.json"))) { throw "Archive must contain inm-1.0.0-phase1/release.json." }
  $release = Get-Content -Raw (Join-Path $source "release.json") | ConvertFrom-Json
  if ($release.version -ne "1.0.0-phase1") { throw "Unexpected release version: $($release.version)" }
  & git clone --branch $Branch $Repository $repoPath
  if ($LASTEXITCODE -ne 0) { throw "Cannot clone repository. Check GitHub credentials." }
  $copied = 0
  foreach ($file in (Get-ChildItem -LiteralPath $source -Recurse -File)) {
    $relative = $file.FullName.Substring($source.Length).TrimStart('\','/')
    $normalized = $relative.Replace('\','/')
    if ($normalized -match '(^|/)(evidence|node_modules|__pycache__|\.venv|\.git)(/|$)') { continue }
    if ($normalized -match '\.(gitbundle|pyc|db|sqlite|sqlite3|pem|key|p12|pfx|log)$') { continue }
    if ($normalized -match '(^|/)\.env($|\.)' -and $normalized -ne ".env.example") { continue }
    if ($normalized -eq "README.md") { $normalized = "docs/PHASE1_ORIGINAL_README.md" }
    $target = Join-Path $repoPath ($normalized.Replace('/','\'))
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    $copied++
  }
  Write-Host "Imported $copied reviewed source files into temporary checkout."
  & git -C $repoPath add -A
  if ($LASTEXITCODE -ne 0) { throw "git add failed." }
  & git -C $repoPath diff --cached --check
  if ($LASTEXITCODE -ne 0) { throw "Whitespace/errors in staged changes; review before publishing." }
  & git -C $repoPath diff --cached --stat
  $staged = & git -C $repoPath diff --cached --name-only
  if (-not $staged) { Write-Host "No changes to publish."; return }
  & git -C $repoPath commit -m "feat: import reviewed INM Phase 1 application source"
  if ($LASTEXITCODE -ne 0) { throw "git commit failed; configure a signed or named Git identity." }
  if ($NoPush) {
    Write-Warning "NoPush selected: changes exist only in $repoPath. Copy the repository somewhere persistent before exiting."
    return
  }
  & git -C $repoPath push origin "HEAD:refs/heads/$Branch"
  if ($LASTEXITCODE -ne 0) { throw "git push failed; check permissions and branch protection. No live server was changed." }
  Write-Host "Source committed to the private GitHub repository. Independently review the commit before tagging or deploying."
  Write-Host "No server deployment has occurred."
} finally {
  if ((Test-Path $workspace) -and (-not $NoPush)) { Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue }
}
