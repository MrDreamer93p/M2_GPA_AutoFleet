# Kinect Python Bridge

Use `tools/start_kinect_python_bridge.ps1` for local Kinect video. This path does not build or launch a custom `.exe`; it runs source code with the installed Python interpreter and registers six Kinect channels with AutoFleet over MQTT.

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_kinect_python_bridge.ps1 -Restart
```

Streams:

- `color`
- `depth`
- `distance`
- `body_index`
- `skeleton`
- `pose`

Health:

```powershell
curl.exe http://127.0.0.1:8450/health
```

If `sensor_available` is `false`, the frontend and backend are connected, but Windows Kinect SDK is not receiving frames from the device. Replug Kinect power/USB 3.0 or restart `KinectMonitor` from an elevated shell.

The legacy `tools/start_kinect_windows_bridge.ps1` now delegates to the Python bridge so existing commands do not recreate `KinectWindowsMjpegBridge.exe`.
