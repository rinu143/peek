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
        private readonly IEngineProcessManager? _engineManager;
        private NamedPipeClientStream? _pipeStream;
        private StreamReader? _reader;
        private StreamWriter? _writer;
        private CancellationTokenSource? _cts;
        private Task? _readTask;
        private readonly SemaphoreSlim _writeLock = new(1, 1);
        private TaskCompletionSource<StatusResponseData?>? _statusTcs;
        private TaskCompletionSource<ProfilesListResponseData?>? _listProfilesTcs;
        private TaskCompletionSource<SetEnabledResponseData?>? _setEnabledTcs;
        private TaskCompletionSource<DeleteResponseData?>? _deleteProfileTcs;
        private TaskCompletionSource<RenameResponseData?>? _renameProfileTcs;
        private TaskCompletionSource<QualityResponseData?>? _qualityTcs;
        private int _isDisposed;

        public bool IsConnected => _pipeStream?.IsConnected == true;

        public EnrollmentPipeClient(IEngineProcessManager? engineManager = null)
        {
            _engineManager = engineManager ?? new EngineProcessManager();
        }

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

            // 1. Initial attempt: check if pipe is already open and responding
            int probeTimeout = Math.Min(800, timeoutMs);
            if (await TryConnectInternalAsync(pipeName, probeTimeout, linkedCts.Token).ConfigureAwait(false))
            {
                return;
            }

            // 2. If not connected, attempt to start the engine process silently in background
            if (_engineManager != null && !linkedCts.Token.IsCancellationRequested)
            {
                bool launched = _engineManager.StartEngineProcess();
                if (launched)
                {
                    var startTime = DateTime.UtcNow;
                    int maxWaitMs = Math.Max(timeoutMs, 7000);
                    while ((DateTime.UtcNow - startTime).TotalMilliseconds < maxWaitMs && !linkedCts.Token.IsCancellationRequested)
                    {
                        await Task.Delay(250, linkedCts.Token).ConfigureAwait(false);
                        if (await TryConnectInternalAsync(pipeName, 500, linkedCts.Token).ConfigureAwait(false))
                        {
                            return;
                        }
                    }
                }
            }

            await DisconnectAsync();
            throw new EngineUnavailableException("Peek engine isn't running — start it and try again.");
        }

        private async Task<bool> TryConnectInternalAsync(string pipeName, int timeoutMs, CancellationToken ct)
        {
            NamedPipeClientStream? stream = null;
            try
            {
                stream = new NamedPipeClientStream(
                    ".",
                    pipeName,
                    PipeDirection.InOut,
                    PipeOptions.Asynchronous);

                await stream.ConnectAsync(timeoutMs, ct).ConfigureAwait(false);

                _pipeStream = stream;
                _reader = new StreamReader(_pipeStream, new UTF8Encoding(false));
                _writer = new StreamWriter(_pipeStream, new UTF8Encoding(false)) { AutoFlush = true };

                _readTask = Task.Run(() => ReadLoopAsync(_cts!.Token), _cts!.Token);
                return true;
            }
            catch (Exception)
            {
                if (stream != null)
                {
                    try { stream.Dispose(); } catch { }
                }
                return false;
            }
        }

        public async Task<StatusResponseData?> RequestStatusAsync(int timeoutMs = 2000, CancellationToken ct = default)
        {
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            var tcs = new TaskCompletionSource<StatusResponseData?>(TaskCreationOptions.RunContinuationsAsynchronously);
            _statusTcs = tcs;

            using var timeoutCts = new CancellationTokenSource(timeoutMs);
            using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(ct, timeoutCts.Token);
            using var reg = linkedCts.Token.Register(() => tcs.TrySetResult(null));

            try
            {
                string message = EnrollmentProtocol.CreateStatusRequest();
                await SendLineAsync(message, linkedCts.Token).ConfigureAwait(false);
                return await tcs.Task.ConfigureAwait(false);
            }
            finally
            {
                _statusTcs = null;
            }
        }

        public async Task StartEnrollmentAsync(
            string displayName,
            bool keepExisting = true,
            string? profileId = null,
            bool activate = false,
            CancellationToken ct = default)
        {
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            string message = EnrollmentProtocol.CreateStartEnrollment(
                displayName,
                cleanPreview: true,
                keepExisting: keepExisting,
                profileId: profileId,
                activate: activate);
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

        private async Task EnsureConnectedAsync(int timeoutMs, CancellationToken ct)
        {
            if (!IsConnected)
            {
                await ConnectAsync("PeekEnrollment", timeoutMs, ct).ConfigureAwait(false);
            }
        }

        public async Task<System.Collections.Generic.List<ProfileModel>> ListProfilesAsync(int timeoutMs = 3000, CancellationToken ct = default)
        {
            await EnsureConnectedAsync(timeoutMs, ct).ConfigureAwait(false);
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            var tcs = new TaskCompletionSource<ProfilesListResponseData?>(TaskCreationOptions.RunContinuationsAsynchronously);
            _listProfilesTcs = tcs;

            using var timeoutCts = new CancellationTokenSource(timeoutMs);
            using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(ct, timeoutCts.Token);
            using var reg = linkedCts.Token.Register(() => tcs.TrySetResult(null));

            try
            {
                string message = EnrollmentProtocol.CreateListProfiles();
                await SendLineAsync(message, linkedCts.Token).ConfigureAwait(false);
                var resp = await tcs.Task.ConfigureAwait(false);
                var result = new System.Collections.Generic.List<ProfileModel>();
                if (resp != null && resp.Success && resp.Profiles != null)
                {
                    foreach (var p in resp.Profiles)
                    {
                        result.Add(new ProfileModel
                        {
                            ProfileId = p.ProfileId,
                            DisplayName = p.DisplayName,
                            Enabled = p.Enabled,
                            CreatedAt = p.CreatedAt,
                            UserSid = p.UserSid,
                            QualityPercentage = p.QualityPercentage,
                            PoseQualities = p.PoseQualities ?? new System.Collections.Generic.List<double>(),
                            TemplateCount = p.TemplateCount
                        });
                    }
                }
                return result;
            }
            finally
            {
                _listProfilesTcs = null;
            }
        }

        public async Task<bool> SetProfileEnabledAsync(string profileId, bool enabled, int timeoutMs = 3000, CancellationToken ct = default)
        {
            await EnsureConnectedAsync(timeoutMs, ct).ConfigureAwait(false);
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            var tcs = new TaskCompletionSource<SetEnabledResponseData?>(TaskCreationOptions.RunContinuationsAsynchronously);
            _setEnabledTcs = tcs;

            using var timeoutCts = new CancellationTokenSource(timeoutMs);
            using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(ct, timeoutCts.Token);
            using var reg = linkedCts.Token.Register(() => tcs.TrySetResult(null));

            try
            {
                string message = EnrollmentProtocol.CreateSetEnabled(profileId, enabled);
                await SendLineAsync(message, linkedCts.Token).ConfigureAwait(false);
                var resp = await tcs.Task.ConfigureAwait(false);
                return resp?.Success == true;
            }
            finally
            {
                _setEnabledTcs = null;
            }
        }

        public async Task<bool> DeleteProfileAsync(string profileId, int timeoutMs = 3000, CancellationToken ct = default)
        {
            await EnsureConnectedAsync(timeoutMs, ct).ConfigureAwait(false);
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            var tcs = new TaskCompletionSource<DeleteResponseData?>(TaskCreationOptions.RunContinuationsAsynchronously);
            _deleteProfileTcs = tcs;

            using var timeoutCts = new CancellationTokenSource(timeoutMs);
            using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(ct, timeoutCts.Token);
            using var reg = linkedCts.Token.Register(() => tcs.TrySetResult(null));

            try
            {
                string message = EnrollmentProtocol.CreateDeleteProfile(profileId);
                await SendLineAsync(message, linkedCts.Token).ConfigureAwait(false);
                var resp = await tcs.Task.ConfigureAwait(false);
                return resp?.Success == true;
            }
            finally
            {
                _deleteProfileTcs = null;
            }
        }

        public async Task<bool> RenameProfileAsync(string profileId, string newName, int timeoutMs = 3000, CancellationToken ct = default)
        {
            await EnsureConnectedAsync(timeoutMs, ct).ConfigureAwait(false);
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            var tcs = new TaskCompletionSource<RenameResponseData?>(TaskCreationOptions.RunContinuationsAsynchronously);
            _renameProfileTcs = tcs;

            using var timeoutCts = new CancellationTokenSource(timeoutMs);
            using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(ct, timeoutCts.Token);
            using var reg = linkedCts.Token.Register(() => tcs.TrySetResult(null));

            try
            {
                string message = EnrollmentProtocol.CreateRenameProfile(profileId, newName);
                await SendLineAsync(message, linkedCts.Token).ConfigureAwait(false);
                var resp = await tcs.Task.ConfigureAwait(false);
                return resp?.Success == true;
            }
            finally
            {
                _renameProfileTcs = null;
            }
        }

        public async Task<QualityResponseData?> GetProfileQualityAsync(string profileId, int timeoutMs = 3000, CancellationToken ct = default)
        {
            await EnsureConnectedAsync(timeoutMs, ct).ConfigureAwait(false);
            if (!IsConnected || _writer == null)
            {
                throw new InvalidOperationException("Not connected to Peek Enrollment Named Pipe.");
            }

            var tcs = new TaskCompletionSource<QualityResponseData?>(TaskCreationOptions.RunContinuationsAsynchronously);
            _qualityTcs = tcs;

            using var timeoutCts = new CancellationTokenSource(timeoutMs);
            using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(ct, timeoutCts.Token);
            using var reg = linkedCts.Token.Register(() => tcs.TrySetResult(null));

            try
            {
                string message = EnrollmentProtocol.CreateGetQuality(profileId);
                await SendLineAsync(message, linkedCts.Token).ConfigureAwait(false);
                return await tcs.Task.ConfigureAwait(false);
            }
            finally
            {
                _qualityTcs = null;
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

                    case EnrollmentProtocol.MsgStatusResponse:
                        HandleStatusResponse(root);
                        break;

                    case EnrollmentProtocol.MsgProfilesList:
                        HandleProfilesList(root);
                        break;

                    case EnrollmentProtocol.MsgSetEnabledResponse:
                        HandleSetEnabledResponse(root);
                        break;

                    case EnrollmentProtocol.MsgDeleteResponse:
                        HandleDeleteResponse(root);
                        break;

                    case EnrollmentProtocol.MsgRenameResponse:
                        HandleRenameResponse(root);
                        break;

                    case EnrollmentProtocol.MsgQualityResponse:
                        HandleQualityResponse(root);
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

            EnrollmentOverlayData? overlay = null;
            if (root.TryGetProperty("overlay", out var overlayEl) && overlayEl.ValueKind == JsonValueKind.Object)
            {
                overlay = EnrollmentProtocol.ParseOverlay(overlayEl);
            }

            string? frameKind = root.TryGetProperty("frame_kind", out var fkEl) && fkEl.ValueKind == JsonValueKind.String
                ? fkEl.GetString()
                : null;

            // SECURITY REQUIREMENT: Never write preview frames to disk
            // Decode directly in transient memory into a frozen BitmapImage
            BitmapSource bmp = DecodeBase64Jpeg(b64);

            PreviewFrameReceived?.Invoke(this, new PreviewFrameEventArgs(
                bmp,
                targetPose,
                qualityState,
                progressPct,
                guidanceText,
                timestamp,
                overlay,
                frameKind
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
            string duplicateName = root.TryGetProperty("duplicate_name", out p) ? p.GetString() ?? "" : "";

            EnrollmentFailed?.Invoke(this, new EnrollmentFailedEventArgs(reasonCode, detail, duplicateName));
        }

        private void HandleError(JsonElement root)
        {
            string reasonCode = root.TryGetProperty("reason_code", out var p) ? p.GetString() ?? "" : "";
            string detail = root.TryGetProperty("detail", out p) ? p.GetString() ?? "" : "";
            string duplicateName = root.TryGetProperty("duplicate_name", out p) ? p.GetString() ?? "" : "";

            ErrorOccurred?.Invoke(this, new EnrollmentErrorEventArgs(reasonCode, detail, duplicateName));
        }

        private void HandleStatusResponse(JsonElement root)
        {
            bool hasEnrolled = root.TryGetProperty("has_enrolled_profile", out var p) && p.GetBoolean();
            string displayName = root.TryGetProperty("display_name", out p) ? p.GetString() ?? "" : "";
            string profileId = root.TryGetProperty("profile_id", out p) ? p.GetString() ?? "" : "";

            var data = new StatusResponseData(hasEnrolled, displayName, profileId);
            _statusTcs?.TrySetResult(data);
        }

        private void HandleProfilesList(JsonElement root)
        {
            bool success = root.TryGetProperty("success", out var sProp) && sProp.GetBoolean();
            string error = root.TryGetProperty("error", out var eProp) ? eProp.GetString() ?? "" : "";
            var list = new System.Collections.Generic.List<ProfileDto>();

            if (root.TryGetProperty("profiles", out var profProp) && profProp.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in profProp.EnumerateArray())
                {
                    string id = item.TryGetProperty("profile_id", out var idProp) ? idProp.GetString() ?? "" :
                                item.TryGetProperty("id", out var idProp2) ? idProp2.GetString() ?? "" : "";
                    string name = item.TryGetProperty("display_name", out var nProp) ? nProp.GetString() ?? "User" :
                                  item.TryGetProperty("name", out var nProp2) ? nProp2.GetString() ?? "User" : "User";
                    bool enabled = !item.TryGetProperty("enabled", out var enProp) || enProp.GetBoolean();
                    double createdAt = item.TryGetProperty("created_at", out var catProp) ? catProp.GetDouble() : 0.0;
                    string? userSid = item.TryGetProperty("user_sid", out var sidProp) ? sidProp.GetString() : null;
                    int? qualityPct = item.TryGetProperty("quality_percentage", out var qpProp) && qpProp.ValueKind == JsonValueKind.Number ? qpProp.GetInt32() : null;
                    int templateCount = item.TryGetProperty("template_count", out var tcProp) ? tcProp.GetInt32() :
                                        item.TryGetProperty("vector_count", out var vcProp) ? vcProp.GetInt32() : 0;

                    var qualities = new System.Collections.Generic.List<double>();
                    if (item.TryGetProperty("pose_qualities", out var pqProp) && pqProp.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var q in pqProp.EnumerateArray())
                        {
                            if (q.ValueKind == JsonValueKind.Number)
                            {
                                qualities.Add(q.GetDouble());
                            }
                        }
                    }

                    list.Add(new ProfileDto(id, name, enabled, createdAt, userSid, qualityPct, qualities, templateCount));
                }
            }

            var data = new ProfilesListResponseData(success, list, error);
            _listProfilesTcs?.TrySetResult(data);
        }

        private void HandleSetEnabledResponse(JsonElement root)
        {
            string id = root.TryGetProperty("profile_id", out var p) ? p.GetString() ?? "" : "";
            bool enabled = root.TryGetProperty("enabled", out var ep) && ep.GetBoolean();
            bool success = root.TryGetProperty("success", out var sp) && sp.GetBoolean();
            string err = root.TryGetProperty("error", out var erp) ? erp.GetString() ?? "" : "";
            string reason = root.TryGetProperty("reason", out var rp) ? rp.GetString() ?? "" : "";

            var data = new SetEnabledResponseData(id, enabled, success, err, reason);
            _setEnabledTcs?.TrySetResult(data);
        }

        private void HandleDeleteResponse(JsonElement root)
        {
            string id = root.TryGetProperty("profile_id", out var p) ? p.GetString() ?? "" : "";
            bool success = root.TryGetProperty("success", out var sp) && sp.GetBoolean();
            string err = root.TryGetProperty("error", out var erp) ? erp.GetString() ?? "" : "";
            string reason = root.TryGetProperty("reason", out var rp) ? rp.GetString() ?? "" : "";

            var data = new DeleteResponseData(id, success, err, reason);
            _deleteProfileTcs?.TrySetResult(data);
        }

        private void HandleRenameResponse(JsonElement root)
        {
            string id = root.TryGetProperty("profile_id", out var p) ? p.GetString() ?? "" : "";
            string newName = root.TryGetProperty("new_name", out var np) ? np.GetString() ?? "" : "";
            bool success = root.TryGetProperty("success", out var sp) && sp.GetBoolean();
            string err = root.TryGetProperty("error", out var erp) ? erp.GetString() ?? "" : "";
            string reason = root.TryGetProperty("reason", out var rp) ? rp.GetString() ?? "" : "";

            var data = new RenameResponseData(id, newName, success, err, reason);
            _renameProfileTcs?.TrySetResult(data);
        }

        private void HandleQualityResponse(JsonElement root)
        {
            string id = root.TryGetProperty("profile_id", out var p) ? p.GetString() ?? "" : "";
            double qs = root.TryGetProperty("quality_score", out var qp) ? qp.GetDouble() : 0.0;
            bool isValid = root.TryGetProperty("is_valid", out var ivp) && ivp.GetBoolean();
            bool success = root.TryGetProperty("success", out var sp) && sp.GetBoolean();
            string err = root.TryGetProperty("error", out var erp) ? erp.GetString() ?? "" : "";
            string reason = root.TryGetProperty("reason", out var rp) ? rp.GetString() ?? "" : "";

            var data = new QualityResponseData(id, qs, isValid, success, err, reason);
            _qualityTcs?.TrySetResult(data);
        }

        public async Task DisconnectAsync()
        {
            _statusTcs?.TrySetResult(null);
            _listProfilesTcs?.TrySetResult(null);
            _setEnabledTcs?.TrySetResult(null);
            _deleteProfileTcs?.TrySetResult(null);
            _renameProfileTcs?.TrySetResult(null);
            _qualityTcs?.TrySetResult(null);
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
