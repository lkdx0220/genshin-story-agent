param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "GenshinKB-DailyWikiUpdate"

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "已删除任务计划: $TaskName"
    }
    else {
        Write-Output "任务计划不存在: $TaskName"
    }
    exit 0
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $ProjectRoot "scripts\run_daily_update.ps1"

if (-not (Test-Path -LiteralPath $Runner)) {
    Write-Error "找不到执行脚本: $Runner"
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""

$Trigger = New-ScheduledTaskTrigger -Daily -At "18:00"
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "每天 18:00 扫描 B站/米游社 Wiki，检查知识库更新；Phase 1 只 dry-run 报告。" `
    -Force | Out-Null

Write-Output "已注册任务计划: $TaskName (每天 18:00)"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
