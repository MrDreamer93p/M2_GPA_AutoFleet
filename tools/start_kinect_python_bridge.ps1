param(
  [string]$RobotId = "KINECT-WIN",
  [int]$BridgePort = 8450,
  [string]$MqttHost = "127.0.0.1",
  [int]$MqttPort = 3889,
  [string]$TopicPrefix = "fleet/v1",
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = (Get-Command python -ErrorAction Stop).Source

function Test-ProtectedProcess($Proc) {
  if (-not $Proc) { return $true }
  if ($Proc.ProcessId -eq $PID) { return $true }
  $name = [string]$Proc.Name
  $cmd = [string]$Proc.CommandLine
  if ($name -match '^(Code|Code - Insiders|Codex|codex|Cursor)\.exe$') { return $true }
  if ($cmd.Contains("\Microsoft VS Code\") -or $cmd.Contains("\.vscode\extensions\") -or $cmd.Contains("--type=extensionHost")) { return $true }
  return $false
}

function Stop-ExactPythonCommand([string]$Needle, [string]$Label) {
  if (-not $Restart) { return }
  $procs = Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq "python.exe" -and
      $_.CommandLine -and
      $_.CommandLine.Contains($Needle) -and
      -not (Test-ProtectedProcess $_)
    }
  foreach ($proc in $procs) {
    Stop-Process -Id $proc.ProcessId -Force
    Write-Host "stopped $Label pid=$($proc.ProcessId)"
  }
}

function Test-ExactPythonCommand([string]$Needle) {
  @(Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq "python.exe" -and
      $_.CommandLine -and
      $_.CommandLine.Contains($Needle)
    }).Count -gt 0
}

function Start-HiddenProcess([string]$Exe, [string]$Arguments) {
  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $Exe
  $psi.Arguments = $Arguments
  $psi.WorkingDirectory = $Root
  $psi.UseShellExecute = $true
  $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
  [System.Diagnostics.Process]::Start($psi)
}

$bridgeNeedle = "tools\kinect_python_mjpeg_bridge.py"
$registerNeedle = "tools\register_real_rtsp.py --host $MqttHost --port $MqttPort"
$registerRobotNeedle = "--robot-id $RobotId"

Stop-ExactPythonCommand $bridgeNeedle "kinect-python-bridge"
if ($Restart) {
  $registerProcs = Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq "python.exe" -and
      $_.CommandLine -and
      $_.CommandLine.Contains($registerNeedle) -and
      $_.CommandLine.Contains($registerRobotNeedle) -and
      -not (Test-ProtectedProcess $_)
    }
  foreach ($proc in $registerProcs) {
    Stop-Process -Id $proc.ProcessId -Force
    Write-Host "stopped kinect-register pid=$($proc.ProcessId)"
  }
}

if (-not (Test-ExactPythonCommand $bridgeNeedle)) {
  $bridge = Start-HiddenProcess $Python "tools\kinect_python_mjpeg_bridge.py --host 127.0.0.1 --port $BridgePort"
  Write-Host "started kinect python bridge pid=$($bridge.Id)"
} else {
  Write-Host "kinect python bridge already running"
}

$base = "http://127.0.0.1:$BridgePort"
$streams = @(
  "color=$base/streams/color.mjpeg",
  "depth=$base/streams/depth.mjpeg",
  "distance=$base/streams/distance.mjpeg",
  "infrared=$base/streams/infrared.mjpeg",
  "body_index=$base/streams/body_index.mjpeg",
  "skeleton=$base/streams/skeleton.mjpeg"
) -join ","

$registerRunning = @(Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq "python.exe" -and
    $_.CommandLine -and
    $_.CommandLine.Contains($registerNeedle) -and
    $_.CommandLine.Contains($registerRobotNeedle)
  }).Count -gt 0

if (-not $registerRunning) {
  $args = "tools\register_real_rtsp.py --host $MqttHost --port $MqttPort --prefix $TopicPrefix --robot-id $RobotId --rtsp-url $base/streams/color.mjpeg --view-profile kinect_multichannel --video-streams `"$streams`" --state KINECT --battery 1 --interval 0.5"
  $register = Start-HiddenProcess $Python $args
  Write-Host "started kinect telemetry register pid=$($register.Id)"
} else {
  Write-Host "kinect telemetry register already running"
}

for ($i = 0; $i -lt 12; $i++) {
  try {
    $health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 2
    Write-Host ("bridge health: frames={0} note={1}" -f $health.frames, $health.note)
    if ($health.diagnostics) {
      Write-Host ("sensor_available={0}" -f $health.diagnostics.sensor_available)
    }
    break
  } catch {
    Start-Sleep -Milliseconds 500
  }
}

Write-Host "Kinect stream endpoints:"
Write-Host "Kinect bridge profile: low-latency MJPEG (adaptive; check /health streams for actual FPS)"
Write-Host "$base/streams"
foreach ($name in @("color", "depth", "distance", "infrared", "body_index", "skeleton")) {
  Write-Host "$base/streams/$name.mjpeg"
}
