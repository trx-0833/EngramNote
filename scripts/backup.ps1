# EngramNote 定时备份（应用外触发）
#
# 背景：app/services/backup_service.py 由 celery-beat 触发，若不用 start.sh 起服务就不会备份。
#       本脚本把触发移出应用，改由系统计划任务驱动。
#
# 用法：
#   powershell -NoProfile -File scripts/backup.ps1
#   powershell -NoProfile -File scripts/backup.ps1 -KeepDaily 7 -KeepWeekly 4 -KeepMonthly 3
#
# 注册计划任务（每日 03:00）：
#   schtasks /create /tn "EngramNote-Backup" ^
#     /tr "powershell -NoProfile -File D:\engramnote\scripts\backup.ps1" /sc daily /st 03:00
#
# 回滚：
#   schtasks /delete /tn "EngramNote-Backup" /f
#
# 注意：
#   - 不备份 .env（与现有 _backup/ 的行为一致；密钥另行管理）
#   - 复制后立即做 PRAGMA integrity_check，失败时保留损坏副本并报错，不静默删除
#
# 保留策略（2026-11-07 决策：7 日 + 4 周 + 3 月）：
#   - 最近 7 天：全部保留
#   - 7–35 天：每 7 天窗口保留最早一份（最多 4 份周备）
#   - 35–125 天：每 30 天窗口保留最早一份（最多 3 份月备）
#   - 其余删除
#   单库约 38MB，稳态占用约 38 × 14 ≈ 530MB。

[CmdletBinding()]
param(
    [string]$Root,
    [int]$KeepDaily = 7,
    [int]$KeepWeekly = 4,
    [int]$KeepMonthly = 3,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}

$dbPath   = Join-Path $Root 'backend\data\db\engramnote.db'
$destRoot = Join-Path $Root '_backup\scheduled'
$stamp    = Get-Date -Format 'yyyyMMdd-HHmmss'
$dest     = Join-Path $destRoot $stamp

if (-not (Test-Path $dbPath)) {
    Write-Error "找不到数据库：$dbPath"
    exit 1
}

# ---- 1. 备份 ---------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$target = Join-Path $dest 'engramnote.db'

Write-Host "[backup] 源：$dbPath"
Write-Host "[backup] 目标：$target"

Copy-Item -LiteralPath $dbPath -Destination $target -Force
Write-Host "[backup] 复制完成，大小 $([math]::Round((Get-Item $target).Length/1MB,1)) MB"

# ---- 2. 完整性校验 ---------------------------------------------------------
$python = $null
foreach ($cand in @(
    'D:\conda\envs\aiforlearn\python.exe',
    'D:\miniforge3\python.exe'
)) {
    if (Test-Path $cand) { $python = $cand; break }
}
if (-not $python) { $python = 'python' }

# 用不插值的 here-string 写临时脚本，路径通过 argv 传入，避免 $ 插值与引号嵌套问题
$checkPy = Join-Path $env:TEMP 'engramnote_integrity_check.py'
$pySource = @'
import sqlite3, sys
db = sys.argv[1]
try:
    con = sqlite3.connect(db)
    row = con.execute('PRAGMA integrity_check').fetchone()[0]
    n = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    con.close()
except Exception as e:
    print('ERROR', e)
    sys.exit(2)
print('integrity:', row, '| tables:', n)
sys.exit(0 if row == 'ok' else 3)
'@
Set-Content -LiteralPath $checkPy -Value $pySource -Encoding UTF8

& $python $checkPy $target
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Error "完整性校验失败（exit=$code），保留损坏副本用于取证：$dest"
    exit $code
}
Write-Host "[backup] 完整性校验通过"


# ---- 3. 三档保留策略（2026-11-07 决策：7 日 + 4 周 + 3 月）------------------
# 实现说明：不用嵌套管道，逐条 foreach，避免解析器在长管道上给出漂移的错误行号。
$entries = @()
foreach ($d in $dirs) {
    $entries += [pscustomobject]@{
        Dir     = $d
        AgeDays = ($now - $d.LastWriteTime).TotalDays
    }
}

# 3.1 日备：最近 KeepDaily 天全留
foreach ($e in $entries) {
    if ($e.AgeDays -le $KeepDaily) {
        $keep[$e.Dir.FullName] = 'daily'
    }
}

# 3.2 周备：7 天窗口，每窗留最新一份
$weeklyCount = 0
$weeklySeen = @{}
foreach ($e in $entries) {
    if ($e.AgeDays -le $KeepDaily) { continue }
    if ($weeklyCount -ge $KeepWeekly) { continue }
    $w = [math]::Floor(($e.AgeDays - $KeepDaily) / 7)
    if ($weeklySeen.ContainsKey($w)) { continue }
    $keep[$e.Dir.FullName] = 'weekly'
    $weeklySeen[$w] = $true
    $weeklyCount = $weeklyCount + 1
}

# 3.3 月备：30 天窗口，每窗留最新一份
$monthlyStart = $KeepDaily + ($KeepWeekly * 7)
$monthlyCount = 0
$monthlySeen = @{}
foreach ($e in $entries) {
    if ($e.AgeDays -le $monthlyStart) { continue }
    if ($monthlyCount -ge $KeepMonthly) { continue }
    $m = [math]::Floor(($e.AgeDays - $monthlyStart) / 30)
    if ($monthlySeen.ContainsKey($m)) { continue }
    $keep[$e.Dir.FullName] = 'monthly'
    $monthlySeen[$m] = $true
    $monthlyCount = $monthlyCount + 1
}

# 3.4 删除未保留的
$deleted = 0
foreach ($e in $entries) {
    if ($keep.ContainsKey($e.Dir.FullName)) { continue }
    $ageText = [math]::Round($e.AgeDays, 1)
    if ($DryRun) {
        Write-Host "[prune][dry-run] 将删除 $($e.Dir.Name)（$ageText 天前）"
    } else {
        Remove-Item $e.Dir.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[prune] 删除 $($e.Dir.Name)"
    }
    $deleted = $deleted + 1
}

# ---- 4. 汇总 ---------------------------------------------------------------
$byReason = $keep.Values | Group-Object | ForEach-Object { "$($_.Name)=$($_.Count)" }
$nowCount = (Get-ChildItem -Path $destRoot -Directory -ErrorAction SilentlyContinue).Count
Write-Host "[prune] 保留：$($byReason -join ' ') ｜ 删除：$deleted ｜ 现存：$nowCount"
Write-Host "[backup] 完成"
