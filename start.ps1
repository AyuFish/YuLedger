<#
.SYNOPSIS
    记账仪表盘启动器 —— Windows PowerShell 5.1 版

.DESCRIPTION
    与 start.bat 等价：切到脚本所在目录，找到 Python 3.7+，前台启动 ledger_server.py。
    启动时把控制台切到 UTF-8，中文提示不会乱码；Ctrl+C 停止服务。
    额外参数原样传给 ledger_server.py，例如 --port 9000 / --host 127.0.0.1 / --no-browser。

.PARAMETER NoPause
    服务退出后不等待回车，直接结束（计划任务、批处理调用时用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start.ps1 --port 9000

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start.ps1 --no-browser -NoPause

.NOTES
    系统执行策略为 Restricted 时，直接 .\start.ps1 会被拒绝，用上面的
    -ExecutionPolicy Bypass 方式运行；也可以只在当前窗口放开：
        Set-ExecutionPolicy -Scope Process Bypass -Force
#>
param(
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

# 未绑定到上面参数的，全部当服务器参数（已实测 --port 9000 会完整落到 $args）
$ServerArgs = @($args)

# ---------- 控制台 UTF-8：否则 Python 打印的中文会被按 GBK 解码成乱码 ----------
try {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8
    [Console]::InputEncoding = $utf8
} catch {
    # 老 conhost 偶尔拒绝改 InputEncoding，忽略即可
}
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

# ---------- 切到脚本所在目录（等价于 bat 的 cd /d "%~dp0"） ----------
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location -LiteralPath $root

function Write-Err([string]$Text) { Write-Host $Text -ForegroundColor Red }
function Write-Warn2([string]$Text) { Write-Host $Text -ForegroundColor Yellow }

# ---------- 端口：只用于启动前体检，真实监听由 Python 决定 ----------
$port = 8765
for ($i = 0; $i -lt $ServerArgs.Count; $i++) {
    $a = [string]$ServerArgs[$i]
    if ($a -eq '--port' -and ($i + 1) -lt $ServerArgs.Count) {
        $next = $ServerArgs[$i + 1]
        if ($next -match '^\d+$') { $port = [int]$next }
    } elseif ($a -like '--port=*') {
        $eq = $a.Substring(7)
        if ($eq -match '^\d+$') { $port = [int]$eq }
    }
}

function Test-PortBusy([int]$Port, [int]$TimeoutMs = 350) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# ---------- 找 Python：优先 py -3，跳过 WindowsApps 的应用执行别名桩 ----------
function Find-Python {
    $candidates = @(
        @{ Name = 'py';      Prefix = @('-3') },
        @{ Name = 'python';  Prefix = @() },
        @{ Name = 'python3'; Prefix = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Name -CommandType Application -ErrorAction SilentlyContinue |
               Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
               Select-Object -First 1
        if (-not $cmd) { continue }

        $verText = & $cmd.Source @($c.Prefix) -c "import sys;print('%d.%d.%d' % tuple(sys.version_info[:3]))" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $verText) { continue }

        & $cmd.Source @($c.Prefix) -c "import sys;raise SystemExit(0 if sys.version_info >= (3,7) else 1)" 2>$null
        if ($LASTEXITCODE -ne 0) { continue }

        return New-Object psobject -Property @{
            Exe     = $cmd.Source
            Prefix  = $c.Prefix
            Version = ([string]$verText).Trim()
        }
    }
    return $null
}

# ---------- 启动前检查 ----------
if (-not (Test-Path -LiteralPath 'ledger_server.py')) {
    Write-Err "ERROR: 当前目录没有 ledger_server.py"
    Write-Host "       目录：$root"
    if (-not $NoPause) { Read-Host '按回车退出' | Out-Null }
    exit 1
}
if (-not (Test-Path -LiteralPath 'index.html')) {
    Write-Warn2 'WARNING: 同目录没有 index.html，网页会 404。'
}

$py = Find-Python
if (-not $py) {
    Write-Err 'ERROR: 没找到 Python 3.7 或更高版本。'
    Write-Host '       安装：winget install -e --id Python.Python.3.12'
    Write-Host '       或用微软商店搜 “Python 3.12”，安装时勾选 Add python.exe to PATH。'
    if (-not $NoPause) { Read-Host '按回车退出' | Out-Null }
    exit 1
}

if (Test-PortBusy -Port $port) {
    Write-Warn2 "端口 $port 已经有程序在监听，服务多半已经在运行了。"
    Write-Host  "  · 直接用现有服务：http://localhost:$port/"
    Write-Host  "  · 想再开一个，换端口：.\start.ps1 --port 9000"
    if (-not $NoPause) { Read-Host '按回车退出' | Out-Null }
    exit 1
}

# ---------- 起服务 ----------
Write-Host ''
Write-Host '记账仪表盘 · SQLite 本地服务' -ForegroundColor Cyan
Write-Host ("  目录:   {0}" -f $root)
Write-Host ("  Python: {0}  ({1})" -f $py.Exe, $py.Version)
Write-Host ("  端口:   {0}" -f $port)
Write-Host '  打印全部局域网地址；日志含 时间/来源IP/设备(安卓·苹果·电脑·Linux)，固定区域原地刷新。Ctrl+C 停止。'
Write-Host ''

$cmdArgs = @()
$cmdArgs += $py.Prefix
$cmdArgs += 'ledger_server.py'
$cmdArgs += $ServerArgs

& $py.Exe @cmdArgs
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }

Write-Host ''
if ($code -ne 0) {
    Write-Warn2 ("服务已退出，退出码 {0}。" -f $code)
} else {
    Write-Host '服务已停止。'
}

if (-not $NoPause) { Read-Host '按回车关闭窗口' | Out-Null }
exit $code
