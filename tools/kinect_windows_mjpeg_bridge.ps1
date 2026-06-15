param(
  [int]$Port = 8450,
  [string]$KinectAssemblyPath = "C:\Program Files\Microsoft SDKs\Kinect\v2.0_1409\Assemblies\Microsoft.Kinect.dll"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
[Reflection.Assembly]::LoadFrom($KinectAssemblyPath) | Out-Null

$script:LatestJpegs = [hashtable]::Synchronized(@{})
$script:State = [hashtable]::Synchronized(@{
  Frames = 0
  Note = "Starting Kinect PowerShell bridge"
  LastError = ""
  StartedAt = [DateTime]::UtcNow.ToString("o")
})

$script:Bones = @(
  @("Head", "Neck"),
  @("Neck", "SpineShoulder"),
  @("SpineShoulder", "SpineMid"),
  @("SpineShoulder", "ShoulderLeft"),
  @("SpineShoulder", "ShoulderRight"),
  @("SpineMid", "SpineBase"),
  @("SpineBase", "HipLeft"),
  @("SpineBase", "HipRight"),
  @("ShoulderLeft", "ElbowLeft"),
  @("ElbowLeft", "WristLeft"),
  @("WristLeft", "HandLeft"),
  @("ShoulderRight", "ElbowRight"),
  @("ElbowRight", "WristRight"),
  @("WristRight", "HandRight"),
  @("HipLeft", "KneeLeft"),
  @("KneeLeft", "AnkleLeft"),
  @("AnkleLeft", "FootLeft"),
  @("HipRight", "KneeRight"),
  @("KneeRight", "AnkleRight"),
  @("AnkleRight", "FootRight")
)

function Get-JpegEncoder {
  [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
    Where-Object { $_.MimeType -eq "image/jpeg" } |
    Select-Object -First 1
}

function ConvertTo-JpegBytes {
  param(
    [System.Drawing.Bitmap]$Bitmap,
    [long]$Quality = 75,
    [int]$MaxWidth = 960
  )
  $outputBitmap = $Bitmap
  $resized = $null
  if ($Bitmap.Width -gt $MaxWidth) {
    $height = [Math]::Max(1, [int]($Bitmap.Height * ($MaxWidth / [double]$Bitmap.Width)))
    $resized = New-Object System.Drawing.Bitmap $MaxWidth, $height
    $graphics = [System.Drawing.Graphics]::FromImage($resized)
    try {
      $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBilinear
      $graphics.DrawImage($Bitmap, 0, 0, $MaxWidth, $height)
      $outputBitmap = $resized
    } finally {
      $graphics.Dispose()
    }
  }

  $stream = New-Object System.IO.MemoryStream
  $encoderParams = New-Object System.Drawing.Imaging.EncoderParameters 1
  $encoderParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), $Quality
  try {
    $outputBitmap.Save($stream, (Get-JpegEncoder), $encoderParams)
    $stream.ToArray()
  } finally {
    $stream.Dispose()
    if ($resized -ne $null) { $resized.Dispose() }
  }
}

function Convert-BgraToJpeg {
  param([byte[]]$Bgra, [int]$Width, [int]$Height, [long]$Quality = 78)
  $bitmap = New-Object System.Drawing.Bitmap $Width, $Height, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $rect = New-Object System.Drawing.Rectangle 0, 0, $Width, $Height
  $data = $bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, $bitmap.PixelFormat)
  try {
    [Runtime.InteropServices.Marshal]::Copy($Bgra, 0, $data.Scan0, $Bgra.Length)
  } finally {
    $bitmap.UnlockBits($data)
  }
  try {
    ConvertTo-JpegBytes -Bitmap $bitmap -Quality $Quality
  } finally {
    $bitmap.Dispose()
  }
}

function New-BitmapFromBgra {
  param([byte[]]$Bgra, [int]$Width, [int]$Height)
  $bitmap = New-Object System.Drawing.Bitmap $Width, $Height, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $rect = New-Object System.Drawing.Rectangle 0, 0, $Width, $Height
  $data = $bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, $bitmap.PixelFormat)
  try {
    [Runtime.InteropServices.Marshal]::Copy($Bgra, 0, $data.Scan0, $Bgra.Length)
  } finally {
    $bitmap.UnlockBits($data)
  }
  $bitmap
}

function Convert-DepthToJpeg {
  param([UInt16[]]$Depth, [int]$Width, [int]$Height)
  $bgra = New-Object byte[] ($Width * $Height * 4)
  for ($i = 0; $i -lt $Depth.Length; $i++) {
    $mm = [int]$Depth[$i]
    $v = 0
    if ($mm -gt 0) {
      $v = [Math]::Max(0, [Math]::Min(255, 255 - [int]($mm / 18)))
    }
    $j = $i * 4
    $bgra[$j] = [byte]$v
    $bgra[$j + 1] = [byte][Math]::Min(255, $v + 22)
    $bgra[$j + 2] = [byte][Math]::Max(0, $v - 30)
    $bgra[$j + 3] = 255
  }
  Convert-BgraToJpeg -Bgra $bgra -Width $Width -Height $Height -Quality 72
}

function Convert-DistanceToJpeg {
  param([UInt16[]]$Depth, [int]$Width, [int]$Height)
  $bgra = New-Object byte[] ($Width * $Height * 4)
  $valid = 0
  $minMm = 0
  for ($i = 0; $i -lt $Depth.Length; $i++) {
    $mm = [int]$Depth[$i]
    if ($mm -gt 0) {
      $valid++
      if ($minMm -eq 0 -or $mm -lt $minMm) { $minMm = $mm }
    }
    $ratio = if ($mm -gt 0) { [Math]::Max(0, [Math]::Min(1, ($mm - 500) / 4000.0)) } else { 1 }
    $near = [int](255 * (1 - $ratio))
    $far = [int](255 * $ratio)
    $j = $i * 4
    $bgra[$j] = [byte]$far
    $bgra[$j + 1] = [byte][Math]::Min(255, 80 + [int]($near * 0.6))
    $bgra[$j + 2] = [byte]$near
    $bgra[$j + 3] = 255
  }
  $bitmap = New-BitmapFromBgra -Bgra $bgra -Width $Width -Height $Height
  $g = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $font = New-Object System.Drawing.Font "Consolas", 14, ([System.Drawing.FontStyle]::Bold)
    $brush = [System.Drawing.Brushes]::White
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::White), 2
    $centerIndex = [int](($Height / 2) * $Width + ($Width / 2))
    $centerM = if ($Depth[$centerIndex] -gt 0) { "{0:N2} m" -f ($Depth[$centerIndex] / 1000.0) } else { "n/a" }
    $nearest = if ($minMm -gt 0) { "{0:N2} m" -f ($minMm / 1000.0) } else { "n/a" }
    $validPct = "{0:N1}%" -f (100.0 * $valid / [Math]::Max(1, $Depth.Length))
    $g.DrawString("KINECT DISTANCE", $font, $brush, 12, 10)
    $g.DrawString("nearest $nearest   center $centerM   valid $validPct", $font, $brush, 12, 32)
    $g.DrawRectangle($pen, [int]($Width / 2 - 22), [int]($Height / 2 - 22), 44, 44)
    $g.DrawLine($pen, [int]($Width / 2 - 36), [int]($Height / 2), [int]($Width / 2 + 36), [int]($Height / 2))
    $g.DrawLine($pen, [int]($Width / 2), [int]($Height / 2 - 36), [int]($Width / 2), [int]($Height / 2 + 36))
  } finally {
    $g.Dispose()
  }
  try {
    ConvertTo-JpegBytes -Bitmap $bitmap -Quality 76
  } finally {
    $bitmap.Dispose()
  }
}

function Convert-BodyIndexToJpeg {
  param([byte[]]$BodyIndex, [int]$Width, [int]$Height)
  $palette = @(
    @(255, 70, 70), @(72, 255, 120), @(70, 160, 255),
    @(255, 230, 70), @(255, 70, 220), @(80, 255, 245)
  )
  $bgra = New-Object byte[] ($Width * $Height * 4)
  for ($i = 0; $i -lt $BodyIndex.Length; $i++) {
    $idx = [int]$BodyIndex[$i]
    $j = $i * 4
    if ($idx -ge 0 -and $idx -lt 6) {
      $c = $palette[$idx]
      $bgra[$j] = [byte]$c[2]
      $bgra[$j + 1] = [byte]$c[1]
      $bgra[$j + 2] = [byte]$c[0]
    } else {
      $bgra[$j] = 8
      $bgra[$j + 1] = 8
      $bgra[$j + 2] = 8
    }
    $bgra[$j + 3] = 255
  }
  Convert-BgraToJpeg -Bgra $bgra -Width $Width -Height $Height -Quality 72
}

function Convert-SkeletonToJpeg {
  param($Bodies, $Mapper, [int]$Width, [int]$Height)
  $bitmap = New-Object System.Drawing.Bitmap $Width, $Height
  $g = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $g.Clear([System.Drawing.Color]::Black)
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::Lime), 4
    $jointBrush = [System.Drawing.Brushes]::Cyan
    $font = New-Object System.Drawing.Font "Consolas", 14, ([System.Drawing.FontStyle]::Bold)
    $tracked = 0
    foreach ($body in $Bodies) {
      if ($null -eq $body -or -not $body.IsTracked) { continue }
      $tracked++
      foreach ($bone in $script:Bones) {
        $aType = [Enum]::Parse([Microsoft.Kinect.JointType], $bone[0])
        $bType = [Enum]::Parse([Microsoft.Kinect.JointType], $bone[1])
        $a = $body.Joints[$aType]
        $b = $body.Joints[$bType]
        if ($a.TrackingState -eq [Microsoft.Kinect.TrackingState]::NotTracked -or $b.TrackingState -eq [Microsoft.Kinect.TrackingState]::NotTracked) { continue }
        $ap = $Mapper.MapCameraPointToDepthSpace($a.Position)
        $bp = $Mapper.MapCameraPointToDepthSpace($b.Position)
        if ([double]::IsInfinity($ap.X) -or [double]::IsInfinity($bp.X)) { continue }
        $g.DrawLine($pen, [int]$ap.X, [int]$ap.Y, [int]$bp.X, [int]$bp.Y)
        $g.FillEllipse($jointBrush, [int]$ap.X - 4, [int]$ap.Y - 4, 8, 8)
        $g.FillEllipse($jointBrush, [int]$bp.X - 4, [int]$bp.Y - 4, 8, 8)
      }
    }
    $g.DrawString("tracked bodies: $tracked", $font, [System.Drawing.Brushes]::White, 12, 10)
  } finally {
    $g.Dispose()
  }
  try {
    ConvertTo-JpegBytes -Bitmap $bitmap -Quality 75
  } finally {
    $bitmap.Dispose()
  }
}

function New-PlaceholderJpeg {
  param([string]$Name)
  $bitmap = New-Object System.Drawing.Bitmap 640, 360
  $g = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $g.Clear([System.Drawing.Color]::Black)
    $font = New-Object System.Drawing.Font "Consolas", 18, ([System.Drawing.FontStyle]::Bold)
    $g.DrawString("KINECT $($Name.ToUpperInvariant())", $font, [System.Drawing.Brushes]::White, 24, 24)
    $g.DrawString($script:State.Note, $font, [System.Drawing.Brushes]::Gray, 24, 58)
  } finally {
    $g.Dispose()
  }
  try {
    ConvertTo-JpegBytes -Bitmap $bitmap -Quality 70
  } finally {
    $bitmap.Dispose()
  }
}

function Set-Jpeg {
  param([string]$Key, [byte[]]$Bytes)
  if ($Bytes -and $Bytes.Length -gt 0) {
    $script:LatestJpegs[$Key] = $Bytes
  }
}

function Write-Bytes {
  param($Context, [byte[]]$Bytes, [string]$ContentType)
  $Context.Response.StatusCode = 200
  $Context.Response.ContentType = $ContentType
  $Context.Response.Headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
  $Context.Response.ContentLength64 = $Bytes.Length
  $Context.Response.OutputStream.Write($Bytes, 0, $Bytes.Length)
  $Context.Response.Close()
}

function Write-Json {
  param($Context, [string]$Json)
  $bytes = [Text.Encoding]::UTF8.GetBytes($Json)
  Write-Bytes -Context $Context -Bytes $bytes -ContentType "application/json"
}

function Write-MjpegFrame {
  param($Context, [string]$Name)
  $jpeg = $script:LatestJpegs[$Name]
  if ($null -eq $jpeg) {
    $jpeg = New-PlaceholderJpeg -Name $Name
  }
  $header = [Text.Encoding]::ASCII.GetBytes("--frame`r`nContent-Type: image/jpeg`r`nCache-Control: no-store`r`nContent-Length: $($jpeg.Length)`r`n`r`n")
  $tail = [Text.Encoding]::ASCII.GetBytes("`r`n--frame--`r`n")
  $bytes = New-Object byte[] ($header.Length + $jpeg.Length + $tail.Length)
  [Array]::Copy($header, 0, $bytes, 0, $header.Length)
  [Array]::Copy($jpeg, 0, $bytes, $header.Length, $jpeg.Length)
  [Array]::Copy($tail, 0, $bytes, $header.Length + $jpeg.Length, $tail.Length)
  Write-Bytes -Context $Context -Bytes $bytes -ContentType "multipart/x-mixed-replace; boundary=frame"
}

$script:sensor = [Microsoft.Kinect.KinectSensor]::GetDefault()
if ($null -eq $script:sensor) {
  throw "No Kinect v2 sensor was found by Microsoft.Kinect."
}

$script:mapper = $script:sensor.CoordinateMapper
$script:colorDesc = $script:sensor.ColorFrameSource.CreateFrameDescription([Microsoft.Kinect.ColorImageFormat]::Bgra)
$script:depthDesc = $script:sensor.DepthFrameSource.FrameDescription
$script:colorPixels = New-Object byte[] ($script:colorDesc.Width * $script:colorDesc.Height * 4)
$script:depthPixels = New-Object UInt16[] ($script:depthDesc.Width * $script:depthDesc.Height)
$script:bodyIndexPixels = New-Object byte[] ($script:depthDesc.Width * $script:depthDesc.Height)
$script:bodies = New-Object Microsoft.Kinect.Body[] ($script:sensor.BodyFrameSource.BodyCount)

$script:sensor.Open()
$script:colorReader = $script:sensor.ColorFrameSource.OpenReader()
$script:depthReader = $script:sensor.DepthFrameSource.OpenReader()
$script:bodyIndexReader = $script:sensor.BodyIndexFrameSource.OpenReader()
$script:bodyReader = $script:sensor.BodyFrameSource.OpenReader()

function Update-KinectFrame {
  $updated = $false
  try {
    $color = $script:colorReader.AcquireLatestFrame()
    if ($null -ne $color) {
      try {
        $color.CopyConvertedFrameDataToArray($script:colorPixels, [Microsoft.Kinect.ColorImageFormat]::Bgra)
        Set-Jpeg -Key "color" -Bytes (Convert-BgraToJpeg -Bgra $script:colorPixels -Width $script:colorDesc.Width -Height $script:colorDesc.Height -Quality 78)
        $updated = $true
      } finally { $color.Dispose() }
    }

    $depth = $script:depthReader.AcquireLatestFrame()
    if ($null -ne $depth) {
      try {
        $depth.CopyFrameDataToArray($script:depthPixels)
        Set-Jpeg -Key "depth" -Bytes (Convert-DepthToJpeg -Depth $script:depthPixels -Width $script:depthDesc.Width -Height $script:depthDesc.Height)
        Set-Jpeg -Key "distance" -Bytes (Convert-DistanceToJpeg -Depth $script:depthPixels -Width $script:depthDesc.Width -Height $script:depthDesc.Height)
        $updated = $true
      } finally { $depth.Dispose() }
    }

    $bodyIndex = $script:bodyIndexReader.AcquireLatestFrame()
    if ($null -ne $bodyIndex) {
      try {
        $bodyIndex.CopyFrameDataToArray($script:bodyIndexPixels)
        Set-Jpeg -Key "body_index" -Bytes (Convert-BodyIndexToJpeg -BodyIndex $script:bodyIndexPixels -Width $script:depthDesc.Width -Height $script:depthDesc.Height)
        $updated = $true
      } finally { $bodyIndex.Dispose() }
    }

    $body = $script:bodyReader.AcquireLatestFrame()
    if ($null -ne $body) {
      try {
        $body.GetAndRefreshBodyData($script:bodies)
        $skeleton = Convert-SkeletonToJpeg -Bodies $script:bodies -Mapper $script:mapper -Width $script:depthDesc.Width -Height $script:depthDesc.Height
        Set-Jpeg -Key "skeleton" -Bytes $skeleton
        Set-Jpeg -Key "pose" -Bytes $skeleton
        $updated = $true
      } finally { $body.Dispose() }
    }

    if ($updated) {
      $script:State.Frames = [int]$script:State.Frames + 1
      $script:State.Note = "Kinect frames online"
      return $true
    }
    return $false
  } catch {
    $script:State.LastError = [string]$_.Exception.Message
    $script:State.Note = "Capture error: $($script:State.LastError)"
    return $false
  }
}

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()
Write-Host "Kinect PowerShell MJPEG bridge listening on http://127.0.0.1:$Port"

try {
  $pendingContext = $listener.GetContextAsync()
  while ($listener.IsListening) {
    Update-KinectFrame | Out-Null
    if (-not $pendingContext.Wait(20)) {
      continue
    }
    $context = $pendingContext.Result
    $pendingContext = $listener.GetContextAsync()
    $path = $context.Request.Url.AbsolutePath.Trim("/").ToLowerInvariant()
    try {
      if ($path -eq "health") {
        $json = '{"status":"ok","source":"windows-kinect-powershell","frames":' + $script:State.Frames + ',"note":"' + (($script:State.Note -replace '\\','\\') -replace '"','\"') + '"}'
        Write-Json -Context $context -Json $json
      } elseif ($path -eq "streams") {
        Write-Json -Context $context -Json '{"items":["color","depth","distance","body_index","skeleton","pose"]}'
      } elseif ($path.StartsWith("streams/") -and $path.EndsWith(".mjpeg")) {
        $name = $path.Substring("streams/".Length)
        $name = $name.Substring(0, $name.Length - ".mjpeg".Length).Replace("-", "_")
        Write-MjpegFrame -Context $context -Name $name
      } else {
        $context.Response.StatusCode = 404
        $context.Response.Close()
      }
    } catch {
      try {
        $context.Response.StatusCode = 500
        $context.Response.Close()
      } catch {}
    }
  }
} finally {
  $script:colorReader.Dispose()
  $script:depthReader.Dispose()
  $script:bodyIndexReader.Dispose()
  $script:bodyReader.Dispose()
  $script:sensor.Close()
  $listener.Close()
}
