param(
  [string]$PiHost = "192.168.110.249",
  [string]$PiUser = "agent",
  [string]$PiPassword = $env:AUTOFLEET_PI_PASSWORD,
  [string]$PiHostKey = "SHA256:fDODaxwJWquCprSClC8B/p/roJjFYnGvkGVZRVnyPRo",
  [string]$RobotId = "R1",
  [int]$FrontendPort = 3000,
  [int]$MqttPort = 3890,
  [int]$BackendPort = 8201,
  [int]$VideoPort = 8401,
  [string]$LocalMqttHost = "",
  [switch]$UseSshTunnel,
  [switch]$SkipPiRestart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Logs = Join-Path $Root "data\logs"
$Run = Join-Path $Root "data\run"
New-Item -ItemType Directory -Force -Path $Logs, $Run | Out-Null

function Stop-Port([int]$Port) {
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {
      try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Start-AFProcess([string]$Name, [string]$WorkingDirectory, [string]$Command) {
  $outLog = Join-Path $Logs "$Name.out.log"
  $errLog = Join-Path $Logs "$Name.err.log"
  $proc = Start-Process powershell `
    -WindowStyle Hidden `
    -WorkingDirectory $WorkingDirectory `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command) `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru
  Set-Content -Path (Join-Path $Run "$Name.pid") -Value $proc.Id
  return $proc
}

function Resolve-Tool([string]$Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $puttyPath = Join-Path ${env:ProgramFiles} "PuTTY\$Name"
  if (Test-Path $puttyPath) { return $puttyPath }
  throw "$Name not found. Install PuTTY or add it to PATH."
}

function Resolve-LocalMqttHost([string]$PiAddress, [string]$PreferredHost) {
  if ($PreferredHost) { return $PreferredHost }

  $parts = $PiAddress.Split(".")
  if ($parts.Count -eq 4) {
    $prefix = "$($parts[0]).$($parts[1]).$($parts[2])."
    $sameSubnet = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
      Where-Object {
        $_.IPAddress -like "$prefix*" -and
        $_.IPAddress -ne "127.0.0.1" -and
        $_.IPAddress -notlike "169.254.*"
      } |
      Sort-Object PrefixLength -Descending |
      Select-Object -First 1
    if ($sameSubnet) { return $sameSubnet.IPAddress }
  }

  $defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
    Sort-Object RouteMetric |
    Select-Object -First 1
  if ($defaultRoute) {
    $routeIp = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $defaultRoute.InterfaceIndex -ErrorAction SilentlyContinue |
      Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254.*" } |
      Select-Object -First 1
    if ($routeIp) { return $routeIp.IPAddress }
  }

  throw "Cannot determine local MQTT host. Pass -LocalMqttHost <your-laptop-ip>."
}

$plink = Resolve-Tool "plink.exe"
$pscp = Resolve-Tool "pscp.exe"
$resolvedLocalMqttHost = Resolve-LocalMqttHost $PiHost $LocalMqttHost
if (($UseSshTunnel -or -not $SkipPiRestart) -and -not $PiPassword) {
  throw "Pi password is required for Pi service control. Pass -PiPassword or set AUTOFLEET_PI_PASSWORD."
}
$ffmpeg = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) {
  $ffmpeg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
}

foreach ($port in @($FrontendPort, $MqttPort, $BackendPort, $VideoPort)) {
  Stop-Port $port
}
Start-Sleep -Seconds 1

Start-AFProcess "mqtt-broker-$MqttPort" $Root "`$env:AUTOFLEET_MQTT_PORT='$MqttPort'; python .\tools\local_mqtt_broker.py" | Out-Null
Start-Sleep -Seconds 1

Start-AFProcess "backend-$BackendPort" (Join-Path $Root "backend") `
  "`$env:AUTOFLEET_MQTT_HOST='127.0.0.1'; `$env:AUTOFLEET_MQTT_PORT='$MqttPort'; `$env:AUTOFLEET_TOPIC_PREFIX='fleet/v1'; `$env:AUTOFLEET_VIDEO_WORKER_BASE='http://127.0.0.1:$VideoPort'; python -m uvicorn main:app --host 0.0.0.0 --port $BackendPort" | Out-Null

$videoCommand = "`$env:AUTOFLEET_MQTT_HOST='127.0.0.1'; `$env:AUTOFLEET_MQTT_PORT='$MqttPort'; `$env:AUTOFLEET_TOPIC_PREFIX='fleet/v1'; `$env:AUTOFLEET_VIDEO_PUBLIC_BASE='http://127.0.0.1:$VideoPort'; `$env:AUTOFLEET_VIDEO_QUALITY_PRESET='balanced'; `$env:AUTOFLEET_FFMPEG_DIRECT_MJPEG='0'; "
if ($ffmpeg) {
  $videoCommand += "`$env:AUTOFLEET_FFMPEG_EXE='$ffmpeg'; "
}
$videoCommand += "python -m uvicorn main:app --host 0.0.0.0 --port $VideoPort"
Start-AFProcess "video-worker-$VideoPort" (Join-Path $Root "workers\video_worker") $videoCommand | Out-Null

Start-AFProcess "perception-worker" (Join-Path $Root "workers\perception_worker") `
  "`$env:AUTOFLEET_MQTT_HOST='127.0.0.1'; `$env:AUTOFLEET_MQTT_PORT='$MqttPort'; `$env:AUTOFLEET_VIDEO_BASE='http://127.0.0.1:$VideoPort'; python main.py" | Out-Null

Start-AFProcess "frontend-$FrontendPort" (Join-Path $Root "frontend") "python -m http.server $FrontendPort --bind 0.0.0.0" | Out-Null

Get-Process plink -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -eq $plink } |
  Stop-Process -Force -ErrorAction SilentlyContinue

$piMqttHost = $resolvedLocalMqttHost
if ($UseSshTunnel) {
  Start-Process -FilePath $plink `
    -ArgumentList @("-batch", "-hostkey", $PiHostKey, "-ssh", "$PiUser@$PiHost", "-pw", $PiPassword, "-N", "-R", "$MqttPort`:127.0.0.1`:$MqttPort") `
    -WindowStyle Hidden | Out-Null
  $piMqttHost = "127.0.0.1"
}

if (-not $SkipPiRestart) {
  $agentEnvPath = Join-Path $Run "autofleet-agent.env"
  Set-Content -Path $agentEnvPath -Value "ROBOT_ID=$RobotId`nMQTT_HOST=$piMqttHost`nMQTT_PORT=$MqttPort`nRTSP_URL=auto`n" -NoNewline
  & $pscp -batch -hostkey $PiHostKey -pw $PiPassword $agentEnvPath "$PiUser@$PiHost`:/tmp/autofleet-agent.env"
  & $plink -batch -hostkey $PiHostKey -ssh "$PiUser@$PiHost" -pw $PiPassword `
    "printf '$PiPassword\n' | sudo -S -p '' mv /tmp/autofleet-agent.env /etc/default/autofleet-agent && printf '$PiPassword\n' | sudo -S -p '' chown root:root /etc/default/autofleet-agent && printf '$PiPassword\n' | sudo -S -p '' systemctl daemon-reload && printf '$PiPassword\n' | sudo -S -p '' systemctl restart autofleet-mediamtx.service autofleet-stream.service autofleet-agent.service 2>/dev/null || tmux new-session -d -s robot -n mediamtx 'mediamtx /etc/mediamtx.yml' \\; new-window -n stream 'sleep 2; cd /home/agent/robot-agent && python3 -u stream_rtsp.py' \\; new-window -n agent 'sleep 4; cd /home/agent/robot-agent && python3 -u raspi_autofleet_agent.py --mqtt-host $piMqttHost --mqtt-port $MqttPort --rtsp-url auto --robot-id $RobotId --interval 0.5'"
}

Start-Sleep -Seconds 5

Write-Host "AutoFleet real R1 stack is starting."
Write-Host "Frontend:      http://127.0.0.1:$FrontendPort"
Write-Host "Backend:       http://127.0.0.1:$BackendPort/api/v1/health"
Write-Host "Video worker:  http://127.0.0.1:$VideoPort/streams"
Write-Host "Pi RTSP:       rtsp://$PiHost:8554/camera"
Write-Host "MQTT path:     Pi -> $piMqttHost`:$MqttPort $(if ($UseSshTunnel) { '(SSH reverse tunnel)' } else { '(direct LAN)' })"
Write-Host "Pi services:   ssh $PiUser@$PiHost 'systemctl status autofleet-mediamtx autofleet-stream autofleet-agent'"
