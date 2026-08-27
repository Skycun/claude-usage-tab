#Requires -Version 5.1
<#
.SYNOPSIS
    Update Claude Usage Tab in place: git pull, refresh deps, restart.
.DESCRIPTION
    Invoked by the "Update available" row in the tray menu, or run by hand.
    The current autostart choice is preserved - this never turns it on or
    off behind your back.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
function Say ($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Die ($msg) { Write-Host "xx  $msg" -ForegroundColor Red; exit 1 }

$Dir     = $PSScriptRoot
$RunKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunName = 'ClaudeUsageTab'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die 'git not found - reinstall from a fresh clone, or install Git for Windows.'
}

Push-Location $Dir
try {
    Say 'Fetching the latest release...'
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        Die 'git pull failed (local changes?). Resolve it, then re-run this script.'
    }
} finally {
    Pop-Location
}

# Preserve whatever the user chose last time.
$hasAutostart = $null -ne (Get-ItemProperty -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue)
$flag = if ($hasAutostart) { '-Autostart' } else { '-NoAutostart' }
Say "Reinstalling (autostart: $(if ($hasAutostart) {'on'} else {'off'}))"
& (Join-Path $Dir 'install-windows.ps1') $flag
