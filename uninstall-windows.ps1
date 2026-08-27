#Requires -Version 5.1
<#
.SYNOPSIS
    Remove Claude Usage Tab from Windows.
.DESCRIPTION
    Stops the tray app, removes the autostart entry and the local virtualenv.
    Your settings, history and stored accounts are KEPT unless you pass
    -Purge. Nothing under ~/.claude (Claude Code's own credentials) is ever
    touched.
.PARAMETER Purge
    Also delete settings, cached history, and the stored account credentials.
#>
[CmdletBinding()]
param([switch]$Purge)

$ErrorActionPreference = 'Stop'
function Say ($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

$Dir     = $PSScriptRoot
$RunKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunName = 'ClaudeUsageTab'
$AppId   = 'claude-usage-indicator'

Say 'Stopping the tray app...'
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*claude_usage_tray.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Say 'Removing the autostart entry...'
Remove-ItemProperty -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue

$Venv = Join-Path $Dir '.venv'
if (Test-Path $Venv) {
    Say 'Removing the virtualenv...'
    Remove-Item -Recurse -Force $Venv
}

$log = Join-Path $env:LOCALAPPDATA "$AppId\tray.log"
if (Test-Path $log) { Remove-Item -Force $log }

if ($Purge) {
    Say 'Purging settings, history and stored accounts...'
    foreach ($p in @(
        (Join-Path $HOME ".config\$AppId"),
        (Join-Path $HOME ".cache\$AppId"),
        (Join-Path $env:LOCALAPPDATA $AppId)
    )) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Host "    removed $p" }
    }
    Write-Host '    (~/.claude is Claude Code''s own - left untouched.)'
} else {
    Write-Host ''
    Write-Host "Settings kept in $HOME\.config\$AppId - re-run with -Purge to delete them too."
}

Write-Host ''
Say 'Uninstalled. The repo folder itself is still here; delete it if you want it gone.'
