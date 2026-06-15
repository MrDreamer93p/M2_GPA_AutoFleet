using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Kinect;

public sealed class KinectWindowsMjpegBridge
{
    private readonly KinectSensor sensor;
    private readonly MultiSourceFrameReader reader;
    private readonly CoordinateMapper mapper;
    private readonly HttpListener listener;
    private readonly object sync = new object();
    private readonly Dictionary<string, byte[]> latestJpegs = new Dictionary<string, byte[]>();
    private readonly int port;
    private volatile bool running = true;
    private Body[] bodies;
    private ushort[] depthPixels;
    private byte[] bodyIndexPixels;
    private byte[] colorPixels;
    private FrameDescription colorDesc;
    private FrameDescription depthDesc;
    private int frameCount;
    private DateTime lastFrameWaitNoteAt = DateTime.MinValue;
    private string lastNote = "Starting Kinect bridge";
    private DateTime lastCaptureErrorAt = DateTime.MinValue;

    private static readonly Dictionary<JointType, JointType[]> Bones = new Dictionary<JointType, JointType[]>
    {
        { JointType.Head, new[] { JointType.Neck } },
        { JointType.Neck, new[] { JointType.SpineShoulder } },
        { JointType.SpineShoulder, new[] { JointType.SpineMid, JointType.ShoulderLeft, JointType.ShoulderRight } },
        { JointType.SpineMid, new[] { JointType.SpineBase } },
        { JointType.SpineBase, new[] { JointType.HipLeft, JointType.HipRight } },
        { JointType.ShoulderLeft, new[] { JointType.ElbowLeft } },
        { JointType.ElbowLeft, new[] { JointType.WristLeft } },
        { JointType.WristLeft, new[] { JointType.HandLeft } },
        { JointType.ShoulderRight, new[] { JointType.ElbowRight } },
        { JointType.ElbowRight, new[] { JointType.WristRight } },
        { JointType.WristRight, new[] { JointType.HandRight } },
        { JointType.HipLeft, new[] { JointType.KneeLeft } },
        { JointType.KneeLeft, new[] { JointType.AnkleLeft } },
        { JointType.AnkleLeft, new[] { JointType.FootLeft } },
        { JointType.HipRight, new[] { JointType.KneeRight } },
        { JointType.KneeRight, new[] { JointType.AnkleRight } },
        { JointType.AnkleRight, new[] { JointType.FootRight } }
    };

    public KinectWindowsMjpegBridge(int port)
    {
        this.port = port;
        sensor = KinectSensor.GetDefault();
        if (sensor == null)
        {
            throw new InvalidOperationException("No Kinect v2 sensor was found by Microsoft.Kinect.");
        }

        mapper = sensor.CoordinateMapper;
        colorDesc = sensor.ColorFrameSource.CreateFrameDescription(ColorImageFormat.Bgra);
        depthDesc = sensor.DepthFrameSource.FrameDescription;
        bodies = new Body[sensor.BodyFrameSource.BodyCount];
        colorPixels = new byte[colorDesc.Width * colorDesc.Height * 4];
        depthPixels = new ushort[depthDesc.Width * depthDesc.Height];
        bodyIndexPixels = new byte[depthDesc.Width * depthDesc.Height];

        sensor.Open();
        reader = sensor.OpenMultiSourceFrameReader(
            FrameSourceTypes.Color |
            FrameSourceTypes.Depth |
            FrameSourceTypes.BodyIndex |
            FrameSourceTypes.Body);

        listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
        listener.Prefixes.Add("http://localhost:" + port + "/");
    }

    public void Run()
    {
        listener.Start();
        Console.WriteLine("Kinect MJPEG bridge listening on http://127.0.0.1:" + port);
        var captureThread = new Thread(CaptureLoop);
        captureThread.IsBackground = true;
        captureThread.Start();

        while (running)
        {
            HttpListenerContext context = null;
            try
            {
                context = listener.GetContext();
                ThreadPool.QueueUserWorkItem(_ => Handle(context));
            }
            catch (HttpListenerException ex)
            {
                Console.Error.WriteLine("HTTP listener stopped: " + ex.ErrorCode + " " + ex.Message);
                running = false;
            }
            catch (ObjectDisposedException ex)
            {
                Console.Error.WriteLine("HTTP listener disposed: " + ex.Message);
                running = false;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("HTTP listener failed: " + ex);
                running = false;
            }
        }
        Console.Error.WriteLine("Kinect bridge stopped. Last note: " + lastNote);
    }

    private void CaptureLoop()
    {
        try
        {
            while (running)
            {
                try
                {
                    var frame = reader.AcquireLatestFrame();
                    if (frame == null)
                    {
                        if ((DateTime.UtcNow - lastFrameWaitNoteAt).TotalSeconds >= 2)
                        {
                            lastFrameWaitNoteAt = DateTime.UtcNow;
                            lastNote = "Waiting for Kinect frames; sensor_available=" + sensor.IsAvailable;
                        }
                        Thread.Sleep(10);
                        continue;
                    }

                    using (var color = frame.ColorFrameReference.AcquireFrame())
                    {
                        if (color != null)
                        {
                            color.CopyConvertedFrameDataToArray(colorPixels, ColorImageFormat.Bgra);
                            SetJpeg("color", BgraToJpeg(colorPixels, colorDesc.Width, colorDesc.Height, 80));
                        }
                    }

                    using (var depth = frame.DepthFrameReference.AcquireFrame())
                    {
                        if (depth != null)
                        {
                            depth.CopyFrameDataToArray(depthPixels);
                            SetJpeg("depth", DepthToJpeg(depthPixels, depthDesc.Width, depthDesc.Height));
                            SetJpeg("distance", DistanceToJpeg(depthPixels, depthDesc.Width, depthDesc.Height));
                        }
                    }

                    using (var bodyIndex = frame.BodyIndexFrameReference.AcquireFrame())
                    {
                        if (bodyIndex != null)
                        {
                            bodyIndex.CopyFrameDataToArray(bodyIndexPixels);
                            SetJpeg("body_index", BodyIndexToJpeg(bodyIndexPixels, depthDesc.Width, depthDesc.Height));
                        }
                    }

                    using (var body = frame.BodyFrameReference.AcquireFrame())
                    {
                        if (body != null)
                        {
                            body.GetAndRefreshBodyData(bodies);
                            SetJpeg("skeleton", SkeletonToJpeg(bodies, depthDesc.Width, depthDesc.Height));
                            SetJpeg("pose", SkeletonToJpeg(bodies, depthDesc.Width, depthDesc.Height));
                        }
                    }

                    frameCount++;
                    lastNote = "Kinect frames online";
                }
                catch (Exception ex)
                {
                    lastNote = ex.Message;
                    if ((DateTime.UtcNow - lastCaptureErrorAt).TotalSeconds >= 2)
                    {
                        lastCaptureErrorAt = DateTime.UtcNow;
                        Console.Error.WriteLine("Kinect capture failed: " + ex);
                    }
                    Thread.Sleep(250);
                }
            }
        }
        catch (Exception ex)
        {
            lastNote = ex.Message;
            Console.Error.WriteLine("Kinect capture thread crashed: " + ex);
        }
    }

    private void Handle(HttpListenerContext context)
    {
        var path = context.Request.Url.AbsolutePath.Trim('/').ToLowerInvariant();
        if (path == "health")
        {
            WriteJson(context, "{\"status\":\"ok\",\"source\":\"windows-kinect\",\"frames\":" + frameCount + ",\"sensor_available\":" + (sensor.IsAvailable ? "true" : "false") + ",\"note\":\"" + JsonEscape(lastNote) + "\"}");
            return;
        }

        if (path == "streams")
        {
            WriteJson(context, "{\"items\":[\"color\",\"depth\",\"distance\",\"body_index\",\"skeleton\",\"pose\"]}");
            return;
        }

        if (path.StartsWith("streams/") && path.EndsWith(".mjpeg"))
        {
            var name = path.Substring("streams/".Length);
            name = name.Substring(0, name.Length - ".mjpeg".Length).Replace("-", "_");
            StreamMjpeg(context, name);
            return;
        }

        context.Response.StatusCode = 404;
        context.Response.Close();
    }

    private void StreamMjpeg(HttpListenerContext context, string name)
    {
        context.Response.StatusCode = 200;
        context.Response.ContentType = "multipart/x-mixed-replace; boundary=frame";
        context.Response.Headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0";
        context.Response.SendChunked = true;
        while (running && context.Response.OutputStream.CanWrite)
        {
            var jpeg = GetJpeg(name);
            if (jpeg != null)
            {
                var header = System.Text.Encoding.ASCII.GetBytes(
                    "--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\nContent-Length: " + jpeg.Length + "\r\n\r\n");
                try
                {
                    context.Response.OutputStream.Write(header, 0, header.Length);
                    context.Response.OutputStream.Write(jpeg, 0, jpeg.Length);
                    context.Response.OutputStream.Write(new byte[] { 13, 10 }, 0, 2);
                    context.Response.OutputStream.Flush();
                }
                catch
                {
                    break;
                }
            }
            Thread.Sleep(33);
        }
        try { context.Response.Close(); } catch { }
    }

    private void WriteJson(HttpListenerContext context, string json)
    {
        var bytes = System.Text.Encoding.UTF8.GetBytes(json);
        context.Response.ContentType = "application/json";
        context.Response.OutputStream.Write(bytes, 0, bytes.Length);
        context.Response.Close();
    }

    private void SetJpeg(string key, byte[] jpeg)
    {
        lock (sync)
        {
            latestJpegs[key] = jpeg;
        }
    }

    private byte[] GetJpeg(string key)
    {
        lock (sync)
        {
            byte[] jpeg;
            return latestJpegs.TryGetValue(key, out jpeg) ? jpeg : null;
        }
    }

    private static byte[] BgraToJpeg(byte[] bgra, int width, int height, long quality)
    {
        using (var bitmap = new Bitmap(width, height, PixelFormat.Format32bppArgb))
        {
            var data = bitmap.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.WriteOnly, bitmap.PixelFormat);
            Marshal.Copy(bgra, 0, data.Scan0, bgra.Length);
            bitmap.UnlockBits(data);
            return BitmapToJpeg(bitmap, quality);
        }
    }

    private static byte[] DepthToJpeg(ushort[] depth, int width, int height)
    {
        var bgra = new byte[width * height * 4];
        for (int i = 0; i < depth.Length; i++)
        {
            int mm = depth[i];
            byte v = (byte)Math.Max(0, Math.Min(255, 255 - (mm / 18)));
            int o = i * 4;
            bgra[o] = v;
            bgra[o + 1] = (byte)Math.Min(255, v + 35);
            bgra[o + 2] = (byte)Math.Max(0, v - 20);
            bgra[o + 3] = 255;
        }
        return BgraToJpeg(bgra, width, height, 78);
    }

    private static byte[] DistanceToJpeg(ushort[] depth, int width, int height)
    {
        int minValid = 500;
        int maxValid = 4500;
        int nearest = int.MaxValue;
        long centerSum = 0;
        int centerCount = 0;
        int validCount = 0;
        int cx0 = width * 3 / 8;
        int cx1 = width * 5 / 8;
        int cy0 = height * 3 / 8;
        int cy1 = height * 5 / 8;

        using (var bitmap = new Bitmap(width, height, PixelFormat.Format24bppRgb))
        using (var g = Graphics.FromImage(bitmap))
        {
            var data = bitmap.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.WriteOnly, bitmap.PixelFormat);
            int stride = data.Stride;
            var rgb = new byte[stride * height];

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    int i = y * width + x;
                    int mm = depth[i];
                    int o = y * stride + x * 3;
                    if (mm < minValid || mm > maxValid)
                    {
                        rgb[o] = 10;
                        rgb[o + 1] = 10;
                        rgb[o + 2] = 10;
                        continue;
                    }

                    validCount++;
                    if (mm < nearest) nearest = mm;
                    if (x >= cx0 && x <= cx1 && y >= cy0 && y <= cy1)
                    {
                        centerSum += mm;
                        centerCount++;
                    }

                    double t = Math.Max(0.0, Math.Min(1.0, (mm - minValid) / (double)(maxValid - minValid)));
                    byte r = (byte)(255 * (1.0 - t));
                    byte gch = (byte)(210 * (1.0 - Math.Abs(t - 0.45) * 1.8));
                    byte b = (byte)(255 * t);
                    rgb[o] = b;
                    rgb[o + 1] = gch;
                    rgb[o + 2] = r;
                }
            }

            Marshal.Copy(rgb, 0, data.Scan0, rgb.Length);
            bitmap.UnlockBits(data);

            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using (var boxBrush = new SolidBrush(Color.FromArgb(185, 0, 0, 0)))
            using (var linePen = new Pen(Color.Lime, 2))
            using (var hotPen = new Pen(Color.DeepPink, 3))
            using (var font = new Font("Consolas", 16, FontStyle.Bold))
            using (var smallFont = new Font("Consolas", 11, FontStyle.Bold))
            using (var textBrush = new SolidBrush(Color.White))
            using (var okBrush = new SolidBrush(Color.Lime))
            {
                g.FillRectangle(boxBrush, 10, 10, 330, 98);
                double nearestM = nearest == int.MaxValue ? 0.0 : nearest / 1000.0;
                double centerM = centerCount == 0 ? 0.0 : (centerSum / (double)centerCount) / 1000.0;
                double validPct = validCount * 100.0 / Math.Max(1, depth.Length);
                g.DrawString("KINECT DISTANCE", font, okBrush, 20, 18);
                g.DrawString("nearest: " + nearestM.ToString("0.00") + " m", smallFont, textBrush, 20, 48);
                g.DrawString("center:  " + centerM.ToString("0.00") + " m", smallFont, textBrush, 20, 68);
                g.DrawString("valid:   " + validPct.ToString("0.0") + "%", smallFont, textBrush, 20, 88);

                g.DrawRectangle(linePen, cx0, cy0, cx1 - cx0, cy1 - cy0);
                g.DrawLine(hotPen, width / 2 - 18, height / 2, width / 2 + 18, height / 2);
                g.DrawLine(hotPen, width / 2, height / 2 - 18, width / 2, height / 2 + 18);
            }

            return BitmapToJpeg(bitmap, 86);
        }
    }

    private static byte[] BodyIndexToJpeg(byte[] bodyIndex, int width, int height)
    {
        Color[] palette = { Color.DeepPink, Color.Cyan, Color.Lime, Color.Gold, Color.OrangeRed, Color.Violet };
        var bgra = new byte[width * height * 4];
        for (int i = 0; i < bodyIndex.Length; i++)
        {
            byte p = bodyIndex[i];
            Color c = p < palette.Length ? palette[p] : Color.Black;
            int o = i * 4;
            bgra[o] = c.B;
            bgra[o + 1] = c.G;
            bgra[o + 2] = c.R;
            bgra[o + 3] = 255;
        }
        return BgraToJpeg(bgra, width, height, 82);
    }

    private byte[] SkeletonToJpeg(Body[] currentBodies, int width, int height)
    {
        using (var bitmap = new Bitmap(width, height, PixelFormat.Format24bppRgb))
        using (var g = Graphics.FromImage(bitmap))
        using (var pen = new Pen(Color.DeepPink, 4))
        using (var jointBrush = new SolidBrush(Color.Cyan))
        using (var trackedBrush = new SolidBrush(Color.Lime))
        {
            g.Clear(Color.Black);
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            foreach (var body in currentBodies)
            {
                if (body == null || !body.IsTracked) continue;
                foreach (var pair in Bones)
                {
                    foreach (var child in pair.Value)
                    {
                        DrawBone(g, pen, body, pair.Key, child);
                    }
                }
                foreach (var joint in body.Joints.Values)
                {
                    var point = mapper.MapCameraPointToDepthSpace(joint.Position);
                    if (float.IsInfinity(point.X) || float.IsInfinity(point.Y)) continue;
                    var brush = joint.TrackingState == TrackingState.Tracked ? trackedBrush : jointBrush;
                    g.FillEllipse(brush, point.X - 5, point.Y - 5, 10, 10);
                }
            }
            return BitmapToJpeg(bitmap, 84);
        }
    }

    private void DrawBone(Graphics g, Pen pen, Body body, JointType a, JointType b)
    {
        var ja = body.Joints[a];
        var jb = body.Joints[b];
        if (ja.TrackingState == TrackingState.NotTracked || jb.TrackingState == TrackingState.NotTracked) return;
        var pa = mapper.MapCameraPointToDepthSpace(ja.Position);
        var pb = mapper.MapCameraPointToDepthSpace(jb.Position);
        if (float.IsInfinity(pa.X) || float.IsInfinity(pa.Y) || float.IsInfinity(pb.X) || float.IsInfinity(pb.Y)) return;
        g.DrawLine(pen, pa.X, pa.Y, pb.X, pb.Y);
    }

    private static byte[] BitmapToJpeg(Bitmap bitmap, long quality)
    {
        using (var ms = new MemoryStream())
        {
            var encoder = GetJpegEncoder();
            if (encoder == null)
            {
                bitmap.Save(ms, ImageFormat.Jpeg);
            }
            else
            {
                var parameters = new EncoderParameters(1);
                parameters.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, quality);
                bitmap.Save(ms, encoder, parameters);
            }
            return ms.ToArray();
        }
    }

    private static ImageCodecInfo GetJpegEncoder()
    {
        foreach (var codec in ImageCodecInfo.GetImageEncoders())
        {
            if (codec.MimeType == "image/jpeg") return codec;
        }
        return null;
    }

    private static string JsonEscape(string value)
    {
        return (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    public static int Main(string[] args)
    {
        AppDomain.CurrentDomain.UnhandledException += delegate(object sender, UnhandledExceptionEventArgs eventArgs)
        {
            Console.Error.WriteLine("Unhandled bridge exception: " + eventArgs.ExceptionObject);
        };
        int port = 8450;
        if (args.Length > 0) int.TryParse(args[0], out port);
        try
        {
            new KinectWindowsMjpegBridge(port).Run();
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.ToString());
            return 1;
        }
    }
}
