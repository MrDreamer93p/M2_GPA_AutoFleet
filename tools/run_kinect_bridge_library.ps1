param(
  [int]$Port = 8450,
  [string]$BridgeDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $BridgeDir) {
  $root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
  $BridgeDir = Join-Path $root "data\artifacts\kinect_bridge_lib"
}

$dll = Join-Path $BridgeDir "AutoFleet.KinectBridge.dll"
if (-not (Test-Path $dll)) {
  throw "Kinect bridge library not found: $dll"
}

[Reflection.Assembly]::LoadFrom($dll) | Out-Null
$bridge = [KinectWindowsMjpegBridge]::new($Port)
$bridge.Run()
