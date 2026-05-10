# Register the daily data-folder watcher with Windows Task Scheduler.
# Run this ONCE in PowerShell (no admin needed) to install the daily job:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-watcher-task.ps1
#
# What it does:
#   - Creates a scheduled task "BidIQ Daily Ingest" that runs every day
#     at 03:00 local time.
#   - Action: cd to this repo, run scripts/watch-data-folder.py, redirect
#     stdout/stderr to data/.ingest-log/schtasks.log.
#   - Runs hidden (no console window pops up).
#   - Runs as the current user, only when the user is logged on (no
#     stored password). The watcher hits the Anthropic API and the Neon
#     DB so it needs network — running while logged on is fine.
#
# To remove later: schtasks /Delete /TN "BidIQ Daily Ingest" /F

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ScriptPath = Join-Path $RepoRoot "scripts\watch-data-folder.py"
$LogDir = Join-Path $RepoRoot "data\.ingest-log"
$LogFile = Join-Path $LogDir "schtasks.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Use the python.exe on PATH. If the user has multiple pythons, this picks
# whichever `python` resolves to. Override by editing $PythonExe below.
$PythonExe = "python"

# Build the action command. We cd into the repo so relative paths in the
# script (data/, .env, etc.) all work. PowerShell's `;` chains commands.
#
# --max-files 50 is a hard cap. Without it a stray timestamp shift (git
# stash pop, OneDrive sync, antivirus scan) can make thousands of files
# look "changed" and the watcher will re-pay Claude for every one. With
# the cap, the worst-case daily damage is ~50 files * ~$0.02 = ~$1, not
# the $40+ surprise we saw on 2026-05-10. Real new files just take a
# few extra days to drain through the queue.
$Cmd = "cd `"$RepoRoot`"; & `"$PythonExe`" `"$ScriptPath`" --max-files 50 *>> `"$LogFile`""

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"$Cmd`""
$Trigger = New-ScheduledTaskTrigger -Daily -At 3:00am
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$TaskName = "BidIQ Daily Ingest"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Task '$TaskName' already exists — replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Daily walk of bid-iq/data/ — auto-ingest new/changed files into the knowledge base." | Out-Null

Write-Host ""
Write-Host "Installed scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Next run:    $((Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"
Write-Host "  Log file:    $LogFile"
Write-Host "  Daily logs:  $LogDir\YYYY-MM-DD.md"
Write-Host ""
Write-Host "To run it once right now:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host ""
Write-Host "To remove it later:"
Write-Host "  Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
