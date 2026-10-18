# 停止 worker (:8001) 与前端 (:3000)
# 用法：PowerShell 里 .\stop.ps1

foreach ($port in 8001, 3000) {
  $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($c) {
    $pids = $c.OwningProcess | Select-Object -Unique
    foreach ($p in $pids) {
      Write-Host "[stop] 端口 $port -> 停止 PID $p"
      Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
  } else {
    Write-Host "[stop] 端口 $port 没有监听"
  }
}
Write-Host "[stop] 完成" -ForegroundColor Green
