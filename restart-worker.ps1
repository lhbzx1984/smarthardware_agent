# 只重启智能体 worker (:8001)，前端不动
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "C:\Users\Dell\.workbuddy\binaries\python\envs\agent-sdk\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$c = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($c) {
  $c.OwningProcess | Select-Object -Unique | ForEach-Object {
    Write-Host "[worker] 停掉 PID $_"
    Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 2
}

Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c", "$py -u agent\server.py > .worker.log 2>&1" `
  -WorkingDirectory $root -WindowStyle Hidden

for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try {
    $h = Invoke-RestMethod "http://127.0.0.1:8001/health" -TimeoutSec 3
    if ($h.ok) {
      Write-Host "[worker] 就绪 model=$($h.model)"
      Write-Host "[worker] 数据库 会话=$($h.store.runs) 事件=$($h.store.events) 产物=$($h.store.artifacts)"
      exit 0
    }
  } catch {}
}
Write-Host "[worker] 未就绪，看 .worker.log" -ForegroundColor Red
