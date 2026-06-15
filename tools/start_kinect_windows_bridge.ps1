param(
  [int]$Port = 8450,
  [string]$RobotId = "KINECT-WIN",
  [string]$MqttHost = "127.0.0.1",
  [int]$MqttPort = 3889,
  [string]$TopicPrefix = "fleet/v1",
  [string]$PublicBase = "",
  [switch]$Restart
)

$ErrorActionPreference = "Stop"

if ($PublicBase -and $PublicBase -ne "http://127.0.0.1:$Port") {
  Write-Warning "Python Kinect bridge listens on 127.0.0.1 only; ignoring PublicBase=$PublicBase."
}

& (Join-Path $PSScriptRoot "start_kinect_python_bridge.ps1") `
  -RobotId $RobotId `
  -BridgePort $Port `
  -MqttHost $MqttHost `
  -MqttPort $MqttPort `
  -TopicPrefix $TopicPrefix `
  -Restart:$Restart
