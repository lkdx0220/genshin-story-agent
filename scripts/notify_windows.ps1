param(
    [Parameter(Mandatory = $true)]
    [string]$Title,
    [Parameter(Mandatory = $true)]
    [string]$Message
)

# 用系统托盘气泡通知，不依赖第三方模块；失败时静默退出，不影响主流程。
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.BalloonTipTitle = $Title
    $notify.BalloonTipText = $Message
    $notify.Visible = $true
    $notify.ShowBalloonTip(8000)
    Start-Sleep -Seconds 2
    $notify.Dispose()
}
catch {
    Write-Error "通知失败: $($_.Exception.Message)"
    exit 1
}
