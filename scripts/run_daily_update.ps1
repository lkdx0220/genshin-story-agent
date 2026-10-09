param(
    [string]$Mode = "dry-run",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonScript = Join-Path $ProjectRoot "scripts\daily_wiki_update.py"
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $ProjectRoot "config\daily_update.json"
}

Set-Location $ProjectRoot

$Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    Write-Error "未找到 python.exe，请确认 Python 已加入 PATH。"
    exit 30
}

Write-Output "开始知识库自动更新检测: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
& $Python $PythonScript --mode $Mode --config $ConfigPath
$ExitCode = $LASTEXITCODE
Write-Output "检测结束，退出码: $ExitCode"
exit $ExitCode
