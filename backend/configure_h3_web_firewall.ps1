param([int]$Port = 8795)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid port.' }
$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$taskPrincipal = New-Object Security.Principal.WindowsPrincipal($taskIdentity)
if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an Administrator PowerShell window.'
}
$taskRuleName = "H3 Studio Web LAN $Port"
if (-not (Get-NetFirewallRule -DisplayName $taskRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $taskRuleName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $Port -RemoteAddress LocalSubnet -Profile Domain,Private | Out-Null
}
Write-Host "Studio Web TCP $Port enabled for the local subnet on Domain/Private networks."
