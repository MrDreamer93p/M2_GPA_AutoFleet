$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir = Join-Path $Root "data\run"

function Stop-PortOwner([int]$Port) {
  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($conn in $connections) {
    if ($conn.OwningProcess -and $conn.OwningProcess -ne 0) {
      Stop-Process -Id $conn.OwningProcess -Force
      Write-Host "stopped port $Port owner pid=$($conn.OwningProcess)"
    }
  }
}

function Stop-WorkspacePython {
  $escapedRoot = $Root.Replace("\", "\\")
  $procs = Get-CimInstance Win32_Process |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine.Contains($Root) -and
      (
        $_.CommandLine.Contains("local_mqtt_broker.py") -or
        $_.CommandLine.Contains("register_real_rtsp.py") -or
        $_.CommandLine.Contains("uvicorn main:app") -or
        $_.CommandLine.Contains("http.server 3000") -or
        $_.CommandLine.Contains("workers\perception_worker") -or
        $_.CommandLine.Contains("workers/video_worker") -or
        $_.CommandLine.Contains("workers\video_worker")
      )
    }
  foreach ($proc in $procs) {
    Stop-Process -Id $proc.ProcessId -Force
    Write-Host "stopped workspace process pid=$($proc.ProcessId)"
  }
}

foreach ($name in @("register-real-rtsp", "frontend", "perception-worker", "video-worker", "backend", "mqtt-broker")) {
  $pidFile = Join-Path $RunDir "$name.pid"
  if (Test-Path $pidFile) {
    $procId = [int](Get-Content $pidFile -Raw)
    Stop-Process -Id $procId -Force
    Remove-Item $pidFile -Force
    Write-Host "stopped $name pid=$procId"
  }
}

Stop-WorkspacePython
foreach ($port in @(3000, 3889, 8200, 8400)) {
  Stop-PortOwner $port
}

Write-Host "AutoFleet local stack stopped."
