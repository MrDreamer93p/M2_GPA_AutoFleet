param(
  [string]$RtspUrl = "",
  [string]$RobotId = "R1",
  [string]$PublicHost = "auto",
  [switch]$AutoDiscoverRtsp,
  [string]$RtspSeedHosts = "192.168.1.24",
  [string]$RtspScanSubnet = "",
  [string]$RtspPorts = "8554,554",
  [string]$RtspPaths = "camera,stream,live,cam,video",
  [string]$RtspUsername = "",
  [string]$RtspPassword = "",
  [string]$VideoStreams = "",
  [switch]$EnableKinect,
  [string]$KinectRobotId = "KINECT-WIN",
  [int]$KinectPort = 8450,
  [switch]$NoStop,
  [switch]$KeepDocker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir = Join-Path $Root "data\run"
$LogDir = Join-Path $Root "data\logs"
$SnapshotDir = Join-Path $Root "data\artifacts\snapshots"

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir, $SnapshotDir | Out-Null

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

function Stop-ByPidFile([string]$Name) {
  $pidFile = Join-Path $RunDir "$Name.pid"
  if (Test-Path $pidFile) {
    $procId = [int](Get-Content $pidFile -Raw)
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
    if (Test-AutoFleetProcess $proc) {
      Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      Write-Host "stopped $Name pid=$procId"
    } elseif ($proc) {
      Write-Host "pid file $Name pointed to non-AutoFleet process pid=$procId; leaving process alive"
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }
}

function Stop-WorkspaceProcess {
  $procs = Get-CimInstance Win32_Process |
    Where-Object { Test-AutoFleetProcess $_ }
  foreach ($proc in $procs) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "stopped stale workspace process pid=$($proc.ProcessId)"
  }
}

function Stop-PortOwnerIfWorkspace([int]$Port) {
  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($conn in $connections) {
    if (-not $conn.OwningProcess) {
      continue
    }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
    if (Test-AutoFleetProcess $proc) {
      Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
      Write-Host "stopped stale workspace port $Port owner pid=$($conn.OwningProcess)"
    }
  }
}

function Start-LocalProcess(
  [string]$Name,
  [string]$WorkingDirectory,
  [string]$Command,
  [hashtable]$Env = @{}
) {
  if (-not $NoStop) {
    Stop-ByPidFile $Name
  }
  $envScript = ($Env.GetEnumerator() | ForEach-Object {
    "`$env:$($_.Key) = '$($_.Value -replace "'", "''")'"
  }) -join "; "
  $fullCommand = if ($envScript) { "$envScript; $Command" } else { $Command }
  $outLog = Join-Path $LogDir "$Name.out.log"
  $errLog = Join-Path $LogDir "$Name.err.log"
  $proc = Start-Process powershell `
    -WindowStyle Hidden `
    -WorkingDirectory $WorkingDirectory `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $fullCommand) `
    -PassThru
  Set-Content -Path (Join-Path $RunDir "$Name.pid") -Value $proc.Id
  Write-Host "started $Name pid=$($proc.Id)"
}

function Test-Port([int]$Port, [int]$TimeoutSeconds = 20) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $ok = Test-NetConnection 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($ok) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Escape-PowerShellArg([string]$Value) {
  return "'$($Value -replace "'", "''")'"
}

function Add-UrlCredentials([string]$Url, [string]$Username, [string]$Password) {
  if (-not $Username.Trim()) {
    return $Url
  }
  $uri = [System.Uri]$Url
  if ($uri.UserInfo) {
    return $Url
  }
  $builder = New-Object System.UriBuilder($uri)
  $builder.UserName = $Username
  $builder.Password = $Password
  return $builder.Uri.AbsoluteUri
}

function Resolve-PublicHost {
  if ($PublicHost.Trim() -and $PublicHost.Trim().ToLowerInvariant() -ne "auto") {
    return $PublicHost.Trim()
  }

  $candidates = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.IPv4Address } |
    ForEach-Object {
      [pscustomobject]@{
        Alias = [string]$_.InterfaceAlias
        Description = [string]$_.InterfaceDescription
        Gateway = [bool]$_.IPv4DefaultGateway
        Ip = [string]$_.IPv4Address.IPAddress
      }
    } |
    Where-Object {
      $_.Ip -and
      $_.Ip -notlike "127.*" -and
      $_.Ip -notlike "169.254.*" -and
      $_.Alias -notmatch "Tailscale|vEthernet|Hyper-V|VirtualBox|VMware|WSL|Loopback|Bluetooth|NGNClient" -and
      $_.Description -notmatch "Tailscale|VirtualBox|VMware|Hyper-V|WSL|Loopback|Bluetooth|NGNClient"
    }

  $primary = $candidates |
    Sort-Object -Property `
      @{ Expression = { if ($_.Gateway) { 0 } else { 1 } } }, `
      @{ Expression = { if ($_.Alias -match "WLAN|Wi-Fi|Wireless") { 0 } elseif ($_.Alias -match "Ethernet") { 1 } else { 2 } } }, `
      @{ Expression = { if ($_.Ip -like "192.168.*") { 0 } elseif ($_.Ip -like "10.*") { 1 } elseif ($_.Ip -like "172.*") { 2 } else { 3 } } } |
    Select-Object -First 1

  if ($primary -and $primary.Ip) {
    return [string]$primary.Ip
  }

  $fallback = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1
  if ($fallback -and $fallback.IPAddress) {
    return [string]$fallback.IPAddress
  }

  return "127.0.0.1"
}

function Get-DefaultScanSubnet([string]$HostIp) {
  $seed = ($RtspSeedHosts -split ",")[0].Trim()
  if ($seed -match "^(\d+)\.(\d+)\.(\d+)\.\d+$" -and $seed -notlike "127.*" -and $seed -notlike "169.254.*") {
    return "$($Matches[1]).$($Matches[2]).$($Matches[3]).0/24"
  }
  if ($HostIp -match "^(\d+)\.(\d+)\.(\d+)\.\d+$" -and $HostIp -notlike "127.*" -and $HostIp -notlike "169.254.*") {
    return "$($Matches[1]).$($Matches[2]).$($Matches[3]).0/24"
  }
  return ""
}

function Resolve-RtspUrl {
  if ($RtspUrl.Trim()) {
    return $RtspUrl.Trim()
  }
  if (-not $AutoDiscoverRtsp) {
    return ""
  }

  $discover = Join-Path $Root "tools\discover_rtsp.py"
  $args = @(
    $discover,
    "--seed-hosts", $RtspSeedHosts,
    "--ports", $RtspPorts,
    "--paths", $RtspPaths,
    "--json"
  )
  $scanSubnet = $RtspScanSubnet.Trim()
  if (-not $scanSubnet) {
    $scanSubnet = Get-DefaultScanSubnet $ResolvedPublicHost
  }
  if ($scanSubnet) {
    $args += @("--subnet", $scanSubnet)
  }

  Write-Host "Discovering RTSP camera..."
  if ($scanSubnet) {
    Write-Host "Scanning subnet: $scanSubnet"
  }
  $output = & python @args
  if ($LASTEXITCODE -ne 0 -or -not $output) {
    throw "RTSP auto-discovery did not find a camera. Try -RtspUrl rtsp://192.168.1.24:8554/camera or pass -RtspScanSubnet 192.168.1.0/24."
  }
  $result = $output | ConvertFrom-Json
  if (-not $result.selected_url) {
    throw "RTSP auto-discovery did not return a selected URL."
  }
  $selectedUrl = [string]$result.selected_url
  if ($result.selected_status -eq "rtsp_auth_required") {
    if (-not $RtspUsername.Trim()) {
      throw "RTSP auto-discovery found an auth-protected camera at $selectedUrl. Rerun with -RtspUsername user [-RtspPassword pass] or pass -RtspUrl directly."
    }
    $selectedUrl = Add-UrlCredentials $selectedUrl $RtspUsername.Trim() $RtspPassword
  } elseif ($result.selected_status -ne "rtsp") {
    throw "RTSP auto-discovery found an open TCP candidate but could not verify RTSP: $($result.selected_url). Try -RtspUrl directly if this camera does not answer RTSP OPTIONS."
  }
  Write-Host "Discovered RTSP: $selectedUrl [$($result.selected_status)]"
  return $selectedUrl
}

function Ensure-FirewallRules {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (-not $isAdmin) {
    Write-Host "Not running as Administrator; Windows Firewall rules were not changed."
    Write-Host "If Raspberry cannot connect to MQTT, rerun this script from an Administrator PowerShell."
    return
  }

  foreach ($port in @(3889, 3000, 8200, 8400)) {
    $ruleName = "AutoFleet Local TCP $port"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) {
      New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $port | Out-Null
      Write-Host "firewall allowed TCP $port"
    }
  }
}

Write-Host "AutoFleet local stack starting from $Root"
Ensure-FirewallRules
$ResolvedPublicHost = Resolve-PublicHost
Write-Host "Public host: $ResolvedPublicHost"
$ResolvedRtspUrl = Resolve-RtspUrl

if ((-not $KeepDocker) -and (-not $NoStop)) {
  $compose = Join-Path $Root "infra\compose.yml"
  if (Get-Command docker -ErrorAction SilentlyContinue) {
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      & docker compose -f $compose stop robot-sim mosquitto backend video-worker perception-worker frontend 2>$null | Out-Null
    } catch {
      Write-Host "Docker services were not fully stopped; continuing with local stack."
    } finally {
      $ErrorActionPreference = $oldErrorActionPreference
    }
  }
}

if (-not $NoStop) {
  foreach ($name in @("register-real-rtsp", "frontend", "perception-worker", "video-worker", "backend", "mqtt-broker")) {
    Stop-ByPidFile $name
  }
  Stop-WorkspaceProcess
  foreach ($port in @(3000, 3889, 3890, 8200, 8201, 8400, 8401, $KinectPort)) {
    Stop-PortOwnerIfWorkspace $port
  }
} else {
  Write-Host "NoStop mode: leaving existing processes untouched."
}

Start-LocalProcess `
  -Name "mqtt-broker" `
  -WorkingDirectory $Root `
  -Command "python tools/local_mqtt_broker.py"

if (-not (Test-Port 3889 20)) {
  throw "MQTT broker did not open port 3889. Check data\logs\mqtt-broker.err.log"
}

Start-LocalProcess `
  -Name "backend" `
  -WorkingDirectory (Join-Path $Root "backend") `
  -Command "python -m uvicorn main:app --host 0.0.0.0 --port 8200" `
  -Env @{
    AUTOFLEET_MQTT_HOST = "127.0.0.1"
    AUTOFLEET_MQTT_PORT = "3889"
    AUTOFLEET_TOPIC_PREFIX = "fleet/v1"
    AUTOFLEET_LOG_DIR = (Join-Path $Root "data\logs")
    AUTOFLEET_RESULT_DIR = (Join-Path $Root "data\results")
    AUTOFLEET_ARTIFACT_DIR = (Join-Path $Root "data\artifacts")
    AUTOFLEET_VIDEO_PUBLIC_BASE = "http://${ResolvedPublicHost}:8400"
    AUTOFLEET_VIDEO_WORKER_BASE = "http://127.0.0.1:8400"
    AUTOFLEET_VIDEO_SNAPSHOT_DIR = $SnapshotDir
    AUTOFLEET_ALERT_SNAPSHOT_DIR = (Join-Path $Root "data\artifacts\alerts")
    AUTOFLEET_DATABASE_DSN = ""
  }

Start-LocalProcess `
  -Name "video-worker" `
  -WorkingDirectory (Join-Path $Root "workers\video_worker") `
  -Command "python -m uvicorn main:app --host 0.0.0.0 --port 8400" `
  -Env @{
    AUTOFLEET_MQTT_HOST = "127.0.0.1"
    AUTOFLEET_MQTT_PORT = "3889"
    AUTOFLEET_TOPIC_PREFIX = "fleet/v1"
    AUTOFLEET_VIDEO_PUBLIC_BASE = "http://${ResolvedPublicHost}:8400"
    AUTOFLEET_VIDEO_SNAPSHOT_DIR = $SnapshotDir
    AUTOFLEET_HEARTBEAT_INTERVAL_MS = "2000"
    AUTOFLEET_VIDEO_CAPTURE_INTERVAL_MS = "20"
    AUTOFLEET_MJPEG_INTERVAL_MS = "10"
    AUTOFLEET_MJPEG_JPEG_QUALITY = "62"
    OPENCV_FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;udp|stimeout;3000000|max_delay;250000|buffer_size;102400"
    AUTOFLEET_FFMPEG_DIRECT_MJPEG = "1"
    AUTOFLEET_FFMPEG_RTSP_TRANSPORT = "udp"
    AUTOFLEET_FFMPEG_MJPEG_FPS = "15"
    AUTOFLEET_FFMPEG_MJPEG_WIDTH = "480"
  }

Start-LocalProcess `
  -Name "perception-worker" `
  -WorkingDirectory (Join-Path $Root "workers\perception_worker") `
  -Command "python main.py" `
  -Env @{
    AUTOFLEET_MQTT_HOST = "127.0.0.1"
    AUTOFLEET_MQTT_PORT = "3889"
    AUTOFLEET_TOPIC_PREFIX = "fleet/v1"
    AUTOFLEET_VIDEO_WORKER_BASE = "http://127.0.0.1:8400"
    AUTOFLEET_ALERT_SNAPSHOT_DIR = (Join-Path $Root "data\artifacts\alerts")
    AUTOFLEET_HEARTBEAT_INTERVAL_MS = "2000"
    AUTOFLEET_PERCEPTION_INTERVAL_MS = "2500"
  }

Start-LocalProcess `
  -Name "frontend" `
  -WorkingDirectory (Join-Path $Root "frontend") `
  -Command "python -m http.server 3000 --bind 0.0.0.0"

if ($ResolvedRtspUrl.Trim()) {
  $registerCommand = "python tools/register_real_rtsp.py --host 127.0.0.1 --port 3889 --robot-id $(Escape-PowerShellArg $RobotId) --rtsp-url $(Escape-PowerShellArg $ResolvedRtspUrl)"
  if ($VideoStreams.Trim()) {
    $registerCommand += " --video-streams $(Escape-PowerShellArg $VideoStreams)"
  }
  Start-LocalProcess `
    -Name "register-real-rtsp" `
    -WorkingDirectory $Root `
    -Command $registerCommand
}

if ($EnableKinect) {
  Write-Host "Starting Kinect bridge and registering $KinectRobotId..."
  & (Join-Path $Root "tools\start_kinect_windows_bridge.ps1") `
    -Port $KinectPort `
    -RobotId $KinectRobotId `
    -MqttHost "127.0.0.1" `
    -MqttPort 3889 `
    -TopicPrefix "fleet/v1" `
    -PublicBase "http://127.0.0.1:$KinectPort" | Out-Host
}

Start-Sleep -Seconds 4

Write-Host ""
Write-Host "AutoFleet local stack is up."
Write-Host "Frontend:      http://127.0.0.1:3000"
Write-Host "Backend:       http://127.0.0.1:8200/api/v1/health"
Write-Host "Video streams: http://127.0.0.1:8400/streams"
Write-Host "MQTT broker:   0.0.0.0:3889"
if ($ResolvedRtspUrl.Trim()) {
  Write-Host "Registered:    $RobotId -> $ResolvedRtspUrl"
  if ($VideoStreams.Trim()) {
    Write-Host "Streams:       $VideoStreams"
  }
} else {
  Write-Host "Waiting for Raspberry MQTT telemetry on fleet/v1/telemetry/<robot_id>"
}
if ($EnableKinect) {
  Write-Host "Kinect:        $KinectRobotId -> http://127.0.0.1:$KinectPort/streams"
}
Write-Host ""
Write-Host "Logs are in data\logs\*.log"
Write-Host "Stop with: .\stop_local_stack.ps1"
