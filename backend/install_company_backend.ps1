param([Parameter(Mandatory = $true)][string]$StudioRoot)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath $StudioRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'H3Studio/app.py'))) {
    throw 'StudioRoot must be the installed movieeasymake project root.'
}
$taskPatch = Join-Path $PSScriptRoot 'company-backend.patch'
& git -C $taskRoot apply --check $taskPatch 2>$null
if ($LASTEXITCODE -eq 0) {
    & git -C $taskRoot apply $taskPatch
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply backend patch.' }
} else {
    & git -C $taskRoot apply --reverse --check $taskPatch 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Installed Studio version differs. Preserve local changes and update it before applying this package.'
    }
}
foreach ($taskName in @('web_server.py', 'web_worker.py', 'WEB_DEPLOYMENT.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "H3Studio/$taskName") -Destination (Join-Path $taskRoot "H3Studio/$taskName") -Force
}
foreach ($taskName in @('start_h3_web_server.bat', 'configure_h3_web_firewall.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $taskName) -Destination (Join-Path $taskRoot $taskName) -Force
}
Write-Host 'Company backend installed. Keep the host Studio/Gateway running, then start start_h3_web_server.bat.'
