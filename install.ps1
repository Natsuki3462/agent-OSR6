param(
    [switch]$Clean,
    [switch]$Force,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$HERE = $PSScriptRoot

function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "   $t" -ForegroundColor Green }
function Warn($t) { Write-Host "   $t" -ForegroundColor Yellow }
function Bad($t)  { Write-Host "   $t" -ForegroundColor Red }

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  OSR-Control 环境安装" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

if ($Clean) {
    Step "清理运行时产物"
    $n = 0
    foreach ($p in @("daemon.log", "daemon.log.1",
                     "webui-daemon.log", "webui-daemon.log.1")) {
        $f = Join-Path $HERE $p
        if (Test-Path -LiteralPath $f) {
            Remove-Item -LiteralPath $f -Force
            Ok "删除 $p"
            $n++
        }
    }
    $cache = Join-Path $HERE "__pycache__"
    if (Test-Path -LiteralPath $cache) {
        Remove-Item -LiteralPath $cache -Recurse -Force
        Ok "删除 __pycache__/"
        $n++
    }
    if ($n -eq 0) { Ok "没有需要清理的东西" }
}

Step "找 Python"
$py = $null
foreach ($cand in @("python", "py", "python3")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) {
    Bad "没找到 Python。请先装 Python 3.10+（安装时勾选 Add Python to PATH）"
    exit 1
}
Ok "使用 $py"
& $py --version
& $py -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if ($LASTEXITCODE -ne 0) {
    Bad "Python 版本太老，需要 3.10 以上"
    exit 1
}

Step "安装 Python 依赖（pyserial / bleak）"
& $py -m pip install --upgrade pip
& $py -m pip install -r (Join-Path $HERE "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Bad "依赖安装失败 —— 检查网络，或加国内镜像："
    Bad "  $py -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 1
}
Ok "依赖就绪"

Step "检查 tkinter（急停按钮窗口要用）"
& $py -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "没有 tkinter。estop.py 会退化成命令行模式：python estop.py --press"
    Warn "想要图形按钮：重装 Python 时勾选 'tcl/tk and IDLE'"
} else {
    Ok "tkinter 可用"
}

Step "检查 3D 模型库（vendor/osr-emu.dist.min.js，8MB）"
& $py (Join-Path $HERE "fetch_vendor.py") --status
Write-Host ""
$fetchArgs = @()
if ($Force) { $fetchArgs += "--force" }
& $py (Join-Path $HERE "fetch_vendor.py") @fetchArgs
if ($LASTEXITCODE -ne 0) {
    Warn "下载没完成。进度已经存下来了 —— 重新运行本脚本会从断点继续。"
    Warn "也可以手动下载：https://github.com/ayvasoftware/osr-emu"
    Warn "  → dist/osr-emu.dist.min.js → 放进 vendor/ 目录"
    Warn "没有它也能用，界面会自动退回简版视图。"
}

if (-not $SkipVerify) {
    Step "自测（不需要设备，跑模拟器）"
    & $py (Join-Path $HERE "selftest.py")
    if ($LASTEXITCODE -ne 0) {
        Warn "自测有失败项，看上面的红字。"
    }
}

Write-Host "`n===============================================" -ForegroundColor Green
Write-Host "  完成。下一步：" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host "  双击 启动.bat                最省事：自动开界面（左边控制台、右边模拟视图）"
Write-Host "  python estop.py              打开急停按钮（热键 F12）"
Write-Host "  python ctl.py --preset orbit 让它跑起来"
Write-Host ""
Write-Host "  默认是模拟器，每次启动都回到模拟器（不会记住上次）。"
Write-Host "  要动真机：界面上点「切到真机」，或 python daemon.py --real --port COM5"
Write-Host ""
Write-Host "  清理日志：界面上「日志 → 清空」，或 powershell -File .\install.ps1 -Clean"
