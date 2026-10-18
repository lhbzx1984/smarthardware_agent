# 只重启前端 :3000（worker 不动）
# 用法：PowerShell 里 .\restart-web.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$c = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($c) {
  $pids = $c.OwningProcess | Select-Object -Unique
  foreach ($p in $pids) {
    Write-Host "[web] 停止 PID $p" -ForegroundColor Yellow
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 2
}

if (Test-Path "$root\web\.next") {
  $bak = ".next.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
  Rename-Item "$root\web\.next" $bak
  Write-Host "[web] 旧 .next 已改名为 $bak" -ForegroundColor DarkGray
}

Write-Host "[web] 启动 :3000" -ForegroundColor Cyan
Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c", "npm run dev > ..\.web.log 2>&1" `
  -WorkingDirectory "$root\web" -WindowStyle Hidden

for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 3
  $c = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
  if ($c) {
    Write-Host "[web] 就绪 http://127.0.0.1:3000" -ForegroundColor Green
    exit 0
  }
}
Write-Host "[web] 未就绪，看 .web.log" -ForegroundColor Red
