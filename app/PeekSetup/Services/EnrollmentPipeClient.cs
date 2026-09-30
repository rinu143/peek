using System;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Media.Imaging;
using PeekSetup.Models;

namespace PeekSetup.Services
{
    public class EnrollmentPipeClient : IEnrollmentPipeClient
    {
        private NamedPipeClientStream? _pipeStream;
        private StreamReader? _reader;
        private StreamWriter? _writer;
        private CancellationTokenSource? _cts;
        private Task? _readTask;
        private readonly SemaphoreSlim _writeLock = new(1, 1);
        private int _isDisposed;

        public bool IsConnected => _pipeStream?.IsConnected == true;

        public event EventHandler<PreviewFrameEventArgs>? PreviewFrameReceived;
        public event EventHandler<PoseCompletedEventArgs>? PoseCompleted;
        public event EventHandler<EnrollmentCompletedEventArgs>? EnrollmentCompleted;
        public event EventHandler<EnrollmentFailedEventArgs>? EnrollmentFailed;
        public event EventHandler<EnrollmentErrorEventArgs>? ErrorOccurred;
        public event EventHandler? ReadyReceived;
        public event EventHandler? Disconnected;

        public async Task ConnectAsync(string pipeName = "PeekEnrollment", int timeoutMs = 4000, CancellationToken ct = default)
        {
            await DisconnectAsync();

            _cts = new CancellationTokenSource();
            using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(_cts.Token, ct);

            try
            {
                _pipeStream = new NamedPipeClientStream(
                    ".",
                    pipeName,
                    PipeDirection.InOut,
                    PipeOptions.Asynchronous);

                await _pipeStream.ConnectAsync(timeoutMs, linkedCts.Token).ConfigureAwait(false);

                _reader = new StreamReader(_pipeStream, new UTF8Encoding(false));
                _writer = new StreamWriter(_pipeStream, new UTF8Encoding(false)) { AutoFlush = true };

                _readTask = Task.Run(() => ReadLoopAsync(_cts.Token), _cts.Token);
            }
            catch (Exception ex) when (ex is TimeoutException or IOException or UnauthorizedAccessException or OperationCanceledException)
            {
                await DisconnectAsync();
                throw new EngineUnavailableException("Peek engine isn't running — start it and try again.", ex);
            }
        }

        public async Task StartEnrollmentAsync(string displayName, CancellationToken ct = default)
        {
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            string message = EnrollmentProtocol.CreateStartEnrollment(displayName);
            await SendLineAsync(message, ct).ConfigureAwait(false);
        }

        public async Task CancelEnrollmentAsync(string reason = "USER_CANCELLED", CancellationToken ct = default)
        {
            if (!IsConnected || _writer == null)
            {
                return;
            }

            try
            {
                string message = EnrollmentProtocol.CreateCancelEnrollment(reason);
                await SendLineAsync(message, ct).ConfigureAwait(false);
            }
            catch
            {
                // Suppress errors during cancel
            }
        }

        private async Task SendLineAsync(string json, CancellationToken ct)
        {
            await _writeLock.WaitAsync(ct).ConfigureAwait(false);
            try
            {
                if (_writer != null)
                {
                    await _writer.WriteLineAsync(json.AsMemory(), ct).ConfigureAwait(false);
                }
            }
            finally
            {
                _writeLock.Release();
            }
        }

        private async Task ReadLoopAsync(CancellationToken ct)
        {
            try
            {
                while (!ct.IsCancellationRequested && _reader != null)
                {
                    string? line = await _reader.ReadLineAsync(ct).ConfigureAwait(false);
                    if (line == null)
                    {
                        // Pipe EOF / Disconnect
                        break;
                    }

                    if (string.IsNullOrWhiteSpace(line))
                    {
                        continue;
                    }

                    ProcessMessage(line);
                }
            }
            catch (OperationCanceledException)
            {
                // Normal cancellation
            }
            catch (Exception)
            {
                // Pipe broken or error
            }
            finally
            {
                Disconnected?.Invoke(this, EventArgs.Empty);
            }
        }

        private void ProcessMessage(string json)
        {
            try
            {
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;

                if (!root.TryGetProperty("type", out var typeProp))
                {
                    return;
                }

                string msgType = typeProp.GetString() ?? string.Empty;

                switch (msgType)
                {
                    case EnrollmentProtocol.MsgEnrollmentReady:
                        ReadyReceived?.Invoke(this, EventArgs.Empty);
                        break;

                    case EnrollmentProtocol.MsgPreviewFrame:
                        HandlePreviewFrame(root);
                        break;

                    case EnrollmentProtocol.MsgPoseComplete:
                        HandlePoseComplete(root);
                        break;

                    case EnrollmentProtocol.MsgEnrollmentComplete:
                        HandleEnrollmentComplete(root);
                        break;

                    case EnrollmentProtocol.MsgEnrollmentFailed:
                        HandleEnrollmentFailed(root);
                        break;

                    case EnrollmentProtocol.MsgError:
                        HandleError(root);
                        break;
                }
            }
            catch
            {
                // Malformed JSON ignored safely without crashing
            }
        }

        private void HandlePreviewFrame(JsonElement root)
        {
            // Frame is passed as base64 JPEG under "frame_jpeg_b64", "frame", or "image"
            string? b64 = null;
            if (root.TryGetProperty("frame_jpeg_b64", out var prop) && prop.ValueKind == JsonValueKind.String)
                b64 = prop.GetString();
            else if (root.TryGetProperty("frame", out prop) && prop.ValueKind == JsonValueKind.String)
                b64 = prop.GetString();
            else if (root.TryGetProperty("image", out prop) && prop.ValueKind == JsonValueKind.String)
                b64 = prop.GetString();

            if (string.IsNullOrEmpty(b64))
            {
                return;
            }

            string targetPose = root.TryGetProperty("target_pose", out var p) ? p.GetString() ?? "" : "";
            string qualityState = root.TryGetProperty("quality_state", out p) ? p.GetString() ?? "" : "";
            double progressPct = root.TryGetProperty("progress_pct", out p) ? p.GetDouble() : 0.0;
            string guidanceText = root.TryGetProperty("guidance_text", out p) ? p.GetString() ?? "" : "";
            double timestamp = root.TryGetProperty("timestamp", out p) ? p.GetDouble() : 0.0;

            // SECURITY REQUIREMENT: Never write preview frames to disk
            // Decode directly in transient memory into a frozen BitmapImage
            BitmapSource bmp = DecodeBase64Jpeg(b64);

            PreviewFrameReceived?.Invoke(this, new PreviewFrameEventArgs(
                bmp,
                targetPose,
                qualityState,
                progressPct,
                guidanceText,
                timestamp
            ));
        }

        private static BitmapSource DecodeBase64Jpeg(string base64)
        {
            byte[] bytes = Convert.FromBase64String(base64);
            using var ms = new MemoryStream(bytes);

            var bitmap = new BitmapImage();
            bitmap.BeginInit();
            bitmap.CacheOption = BitmapCacheOption.OnLoad;
            bitmap.StreamSource = ms;
            bitmap.EndInit();
            bitmap.Freeze(); // Enables cross-thread UI access and prevents leak

            return bitmap;
        }

        private void HandlePoseComplete(JsonElement root)
        {
            string completedPose = root.TryGetProperty("completed_pose", out var p) ? p.GetString() ?? "" : "";
            int poseIndex = root.TryGetProperty("pose_index", out p) ? p.GetInt32() : 0;
            int totalPoses = root.TryGetProperty("total_poses", out p) ? p.GetInt32() : 9;
            string detail = root.TryGetProperty("detail", out p) ? p.GetString() ?? "" : "";

            PoseCompleted?.Invoke(this, new PoseCompletedEventArgs(completedPose, poseIndex, totalPoses, detail));
        }

        private void HandleEnrollmentComplete(JsonElement root)
        {
            string profileId = root.TryGetProperty("profile_id", out var p) ? p.GetString() ?? "" : "";
            string displayName = root.TryGetProperty("display_name", out p) ? p.GetString() ?? "" : "";
            string detail = root.TryGetProperty("detail", out p) ? p.GetString() ?? "" : "";

            EnrollmentCompleted?.Invoke(this, new EnrollmentCompletedEventArgs(profileId, displayName, detail));
        }

        private void HandleEnrollmentFailed(JsonElement root)
        {
            string reasonCode = root.TryGetProperty("reason_code", out var p) ? p.GetString() ?? "" : "";
            string detail = root.TryGetProperty("detail", out p) ? p.GetString() ?? "" : "";

            EnrollmentFailed?.Invoke(this, new EnrollmentFailedEventArgs(reasonCode, detail));
        }

        private void HandleError(JsonElement root)
        {
            string reasonCode = root.TryGetProperty("reason_code", out var p) ? p.GetString() ?? "" : "";
            string detail = root.TryGetProperty("detail", out p) ? p.GetString() ?? "" : "";

            ErrorOccurred?.Invoke(this, new EnrollmentErrorEventArgs(reasonCode, detail));
        }

        public async Task DisconnectAsync()
        {
            if (_cts != null)
            {
                _cts.Cancel();
                _cts.Dispose();
                _cts = null;
            }

            if (_readTask != null)
            {
                try
                {
                    await _readTask.ConfigureAwait(false);
                }
                catch
                {
                    // Ignore background read errors on disconnect
                }
                _readTask = null;
            }

            _writer?.Dispose();
            _writer = null;

            _reader?.Dispose();
            _reader = null;

            if (_pipeStream != null)
            {
                await _pipeStream.DisposeAsync().ConfigureAwait(false);
                _pipeStream = null;
            }
        }

        public async ValueTask DisposeAsync()
        {
            if (Interlocked.Exchange(ref _isDisposed, 1) == 0)
            {
                await DisconnectAsync().ConfigureAwait(false);
                _writeLock.Dispose();
            }
            GC.SuppressFinalize(this);
        }
    }
}
