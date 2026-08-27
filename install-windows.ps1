#Requires -Version 5.1
<#
.SYNOPSIS
    Windows installer for Claude Usage Tab (system-tray build).
.DESCRIPTION
    Creates a local virtualenv, installs pystray + Pillow + requests,
    optionally registers an HKCU autostart entry, and launches the tray app.
    Safe to re-run. Nothing is written outside this folder, HKCU, and the
    per-user config/cache directories.
.PARAMETER Autostart
    Start automatically at every sign-in (skips the prompt).
.PARAMETER NoAutostart
    Don't start at sign-in (still launched once now).
.EXAMPLE
    .\install-windows.ps1
    .\install-windows.ps1 -Autostart
#>
[CmdletBinding()]
param(
    [switch]$Autostart,
    [switch]$NoAutostart
)

$ErrorActionPreference = 'Stop'

function Say ($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Die ($msg) { Write-Host "xx  $msg" -ForegroundColor Red; exit 1 }

$Dir     = $PSScriptRoot
$Venv    = Join-Path $Dir '.venv'
$Script  = Join-Path $Dir 'claude_usage_tray.py'
$RunKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunName = 'ClaudeUsageTab'

if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
    Die 'This installer is for Windows. On Linux use ./install.sh, on macOS ./install-macos.sh.'
}
if ($Autostart -and $NoAutostart) { Die 'Pick one of -Autostart / -NoAutostart.' }

# ------------------------------------------------------------------ python
# The py launcher knows about every installed version; fall back to whatever
# `python` resolves to (which on a bare system is the Store stub, hence the
# version check right after).
#
# The probe prints a sentinel rather than a bare version, and only a line
# matching it counts. A name on PATH is not proof of an interpreter: the
# launcher can print its own chatter, a shim can echo the arguments back, and
# a multi-line capture arrives in PowerShell as an array where -match
# silently filters instead of failing. Requiring our own marker means only
# something that really executed our code can satisfy the check.
$py = $null
$pyArgs = @()
# venv is imported too, not just sys: a Python that cannot create one is no
# use to us, and finding that out here gives a clear message instead of an
# opaque failure three steps later.
#
# The Python string is single-quoted INSIDE a double-quoted PowerShell string,
# and that order is not cosmetic. Windows PowerShell 5.1 drops embedded double
# quotes when it hands an argument to a native .exe, so the reversed form
# reaches python.exe as  print(CUSI-PY %d.%d ...  -- a SyntaxError, an empty
# capture, and a bogus "No usable Python 3.9+ found" on a machine that has a
# perfectly good one. PowerShell 7 fixed native argument quoting; 5.1 is what
# most users still get from the Start menu, and the #Requires above promises
# them this script runs.
$probeCode = "import sys, venv;print('CUSI-PY %d.%d' % sys.version_info[:2])"
foreach ($cand in @('py', 'python3', 'python')) {
    $found = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    $probe = if ($cand -eq 'py') { @('-3', '-c') } else { @('-c') }
    try {
        $out = & $found.Source @probe $probeCode 2>$null
    } catch {
        continue
    }
    $line = $out | Where-Object { $_ -match '^CUSI-PY \d+\.\d+$' } | Select-Object -First 1
    if (-not $line) { continue }
    if ($line -match '^CUSI-PY (\d+)\.(\d+)$') {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 9)) {
            $py = $found.Source
            $pyArgs = if ($cand -eq 'py') { @('-3') } else { @() }
            Say "Using Python $major.$minor ($($found.Source))"
            break
        }
        Say "Skipping $($found.Source): Python $major.$minor is older than 3.9"
    }
}
if (-not $py) {
    Die 'No usable Python 3.9+ found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then re-run this script.'
}

# -------------------------------------------------------------- venv + deps
Say "Setting up virtualenv at $Venv"
& $py @pyArgs -m venv $Venv
$VenvPy  = Join-Path $Venv 'Scripts\python.exe'
$VenvPyw = Join-Path $Venv 'Scripts\pythonw.exe'
if (-not (Test-Path $VenvPy)) { Die "virtualenv creation failed (no $VenvPy)" }

Say 'Installing dependencies (pystray, Pillow, requests)...'
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r (Join-Path $Dir 'requirements-windows.txt')
if ($LASTEXITCODE -ne 0) { Die 'pip install failed.' }

# ------------------------------------------------------------- ask autostart
if ($Autostart)        { $wantAutostart = $true }
elseif ($NoAutostart)  { $wantAutostart = $false }
else {
    $ans = Read-Host '==> Start Claude Usage Tab automatically at sign-in? [Y/n]'
    $wantAutostart = ($ans -notmatch '^(n|no)$')
}

# HKCU only: no elevation, no effect on other users of this PC.
if ($wantAutostart) {
    $cmd = "`"$VenvPyw`" `"$Script`""
    New-Item -Path $RunKey -Force | Out-Null
    Set-ItemProperty -Path $RunKey -Name $RunName -Value $cmd
    Say "Autostart registered ($RunKey\$RunName)"
} else {
    Remove-ItemProperty -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue
    Say 'Autostart is OFF (you can toggle it later from the tray menu: Options > Start with Windows)'
}

# -------------------------------------------------------------- (re)launch
Say 'Stopping any previous instance...'
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*claude_usage_tray.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Say 'Starting Claude Usage Tab...'
Start-Process -FilePath $VenvPyw -ArgumentList "`"$Script`"" -WorkingDirectory $Dir

Write-Host ''
Say 'Install complete - look for the usage badge in your notification area.'
Write-Host '    (Windows hides new tray icons by default: click the ^ chevron and'
Write-Host '     drag it onto the taskbar to keep it visible.)'
Write-Host ''
Write-Host "    Log:    $env:LOCALAPPDATA\claude-usage-indicator\tray.log"
Write-Host '    Quit:   right-click the icon > Quit'
Write-Host '    Update: .\update-windows.ps1'
