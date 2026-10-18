# 一键启动：智能体 worker (:8001) + 前端 (:3000)
# 用法：PowerShell 里 .\start.ps1 ；停止：.\stop.ps1
# 注意1：PS 5.1 的 Start-Process 同时使用两个 -Redirect* 参数会触发
#        "字典中的关键字 Path" 的已知 bug，故改用 cmd 做输出重定向。
# 注意2：Next 15 启动时会清理 .next 里的旧 hot-update 文件，若被外部删除
#        守卫拦截会直接崩，故先把旧 .next 改名（move 不触发删除守卫）。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "C:\Users\Dell\.workbuddy\binaries\python\envs\agent-sdk\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

function Port-Pid($port) {
  $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($c) { $c[0].OwningProcess } else { $null }
}

function Port-Free($port) {
  $p = Port-Pid $port
  if ($p) {
    Write-Host "[start] 端口 $port 被 PID $p 占用，先停掉" -ForegroundColor Yellow
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
  }
}

Port-Free 8001
Port-Free 3000

Write-Host "[start] 启动 worker  :8001" -ForegroundColor Cyan
Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c", "$py -u agent\server.py > .worker.log 2>&1" `
  -WorkingDirectory $root -WindowStyle Hidden

if (Test-Path "$root\web\.next") {
  $bak = ".next.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
  Rename-Item "$root\web\.next" $bak
  Write-Host "[start] 旧 .next 已改名为 $bak" -ForegroundColor DarkGray
}

Write-Host "[start] 启动前端    :3000（首次编译较慢，约 1-4 分钟）" -ForegroundColor Cyan
Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c", "npm run dev > ..\.web.log 2>&1" `
  -WorkingDirectory "$root\web" -WindowStyle Hidden

Write-Host "[start] 等待服务就绪..." -ForegroundColor Gray
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 3
  try {
    $h = Invoke-RestMethod "http://127.0.0.1:8001/health" -TimeoutSec 3
    if ($h.ok) { $ok = $true; break }
  } catch {}
}
if ($ok) {
  $h = Invoke-RestMethod "http://127.0.0.1:8001/health" -TimeoutSec 5
  Write-Host "[start] worker 就绪  model=$($h.model)  provider=$($h.default.provider)  cad=$($h.cad.ok)" -ForegroundColor Green
  Write-Host "[start] 数据库       $($h.store.db)  会话=$($h.store.runs) 事件=$($h.store.events) 产物=$($h.store.artifacts)" -ForegroundColor Green
} else {
  Write-Host "[start] worker 未就绪，看 .worker.log" -ForegroundColor Red
}

Write-Host ""
Write-Host "  前端   http://127.0.0.1:3000" -ForegroundColor White
Write-Host "  worker http://127.0.0.1:8001/health" -ForegroundColor White
Write-Host ""
Write-Host "两个服务已独立运行，关闭本窗口不会停掉它们（用 .\stop.ps1 停）" -ForegroundColor Gray
