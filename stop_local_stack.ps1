$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir = Join-Path $Root "data\run"

function Stop-PortOwner([int]$Port) {
  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($conn in $connections) {
    if ($conn.OwningProcess -and $conn.OwningProcess -ne 0) {
      $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
      if (Test-AutoFleetProcess $proc) {
        Stop-Process -Id $conn.OwningProcess -Force
        Write-Host "stopped port $Port owner pid=$($conn.OwningProcess)"
      }
    }
  }
}

function Test-AutoFleetProcess($Proc) {
  if (-not $Proc -or -not $Proc.CommandLine) {
    return $false
  }
  if ($Proc.ProcessId -eq $PID) {
    return $false
  }
  $name = [string]$Proc.Name
  if ($name -match '^(Code|Code - Insiders|Codex|codex|Cursor)\.exe$') {
    return $false
  }
  $cmd = [string]$Proc.CommandLine
  if ($cmd.Contains("\Microsoft VS Code\") -or $cmd.Contains("\.vscode\extensions\") -or $cmd.Contains("--type=extensionHost")) {
    return $false
  }
  if ($cmd -notlike "*$Root*") {
    return $false
  }
  return (
    (($name -match '^python(\.exe)?$') -and ($cmd.Contains("tools/local_mqtt_broker.py") -or $cmd.Contains("tools\local_mqtt_broker.py"))) -or
    (($name -match '^python(\.exe)?$') -and $cmd.Contains("register_real_rtsp.py")) -or
    (($name -eq "KinectWindowsMjpegBridge.exe") -and $cmd.Contains("KinectWindowsMjpegBridge.exe")) -or
    (($name -match '^python(\.exe)?$') -and $cmd.Contains("uvicorn main:app") -and ($cmd.Contains("backend") -or $cmd.Contains("workers\video_worker") -or $cmd.Contains("workers/video_worker"))) -or
    (($name -match '^python(\.exe)?$') -and $cmd.Contains("http.server 3000") -and $cmd.Contains("frontend")) -or
    (($name -match '^python(\.exe)?$') -and ($cmd.Contains("workers\perception_worker") -or $cmd.Contains("workers/perception_worker")))
  )
}

function Stop-WorkspacePython {
  $procs = Get-CimInstance Win32_Process |
    Where-Object { Test-AutoFleetProcess $_ }
  foreach ($proc in $procs) {
    Stop-Process -Id $proc.ProcessId -Force
    Write-Host "stopped workspace process pid=$($proc.ProcessId)"
  }
}

foreach ($name in @("register-real-rtsp", "frontend", "perception-worker", "video-worker", "backend", "mqtt-broker")) {
  $pidFile = Join-Path $RunDir "$name.pid"
if (Test-Path $pidFile) {
    $procId = [int](Get-Content $pidFile -Raw)
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
    if (Test-AutoFleetProcess $proc) {
      Stop-Process -Id $procId -Force
      Write-Host "stopped $name pid=$procId"
    } elseif ($proc) {
      Write-Host "pid file $name pointed to non-AutoFleet process pid=$procId; leaving process alive"
    }
    Remove-Item $pidFile -Force
  }
}

Stop-WorkspacePython
foreach ($port in @(3000, 3889, 3890, 8200, 8201, 8400, 8401, 8450)) {
  Stop-PortOwner $port
}

Write-Host "AutoFleet local stack stopped."
