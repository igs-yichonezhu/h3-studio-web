param([Parameter(Mandatory = $true)][string]$StudioRoot)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath $StudioRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'H3Studio/app.py'))) {
    throw 'StudioRoot must be the installed movieeasymake project root.'
}
$taskPatches = @('company-backend.patch', 'progress-monitor.patch')
foreach ($taskPatchName in $taskPatches) {
    $taskPatch = Join-Path $PSScriptRoot $taskPatchName
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
}
foreach ($taskName in @('web_server.py', 'web_worker.py', 'WEB_DEPLOYMENT.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "H3Studio/$taskName") -Destination (Join-Path $taskRoot "H3Studio/$taskName") -Force
}
foreach ($taskName in @('start_h3_web_server.bat', 'configure_h3_web_firewall.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $taskName) -Destination (Join-Path $taskRoot $taskName) -Force
}
$taskTestDestination = Join-Path $taskRoot 'H3Studio/tests'
New-Item -ItemType Directory -Path $taskTestDestination -Force | Out-Null
foreach ($taskName in @('test_web_server.py', 'test_web_remote_auth.py', 'test_web_workers.py', 'test_comfy_client.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "H3Studio/tests/$taskName") -Destination (Join-Path $taskTestDestination $taskName) -Force
}
Write-Host 'Company backend installed. Keep the host Studio/Gateway running, then start start_h3_web_server.bat.'
