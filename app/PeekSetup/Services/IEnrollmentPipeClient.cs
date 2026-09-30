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
    public class EngineUnavailableException : Exception
    {
        public EngineUnavailableException(string message, Exception? innerException = null)
            : base(message, innerException)
        {
        }
    }

    public class PreviewFrameEventArgs : EventArgs
    {
        public BitmapSource PreviewImage { get; }
        public string TargetPose { get; }
        public string QualityState { get; }
        public double ProgressPercentage { get; }
        public string GuidanceText { get; }
        public double Timestamp { get; }

        public PreviewFrameEventArgs(
            BitmapSource previewImage,
            string targetPose,
            string qualityState,
            double progressPercentage,
            string guidanceText,
            double timestamp)
        {
            PreviewImage = previewImage;
            TargetPose = targetPose;
            QualityState = qualityState;
            ProgressPercentage = progressPercentage;
            GuidanceText = guidanceText;
            Timestamp = timestamp;
        }
    }

    public class PoseCompletedEventArgs : EventArgs
    {
        public string CompletedPose { get; }
        public int PoseIndex { get; }
        public int TotalPoses { get; }
        public string Detail { get; }

        public PoseCompletedEventArgs(string completedPose, int poseIndex, int totalPoses, string detail)
        {
            CompletedPose = completedPose;
            PoseIndex = poseIndex;
            TotalPoses = totalPoses;
            Detail = detail;
        }
    }

    public class EnrollmentCompletedEventArgs : EventArgs
    {
        public string ProfileId { get; }
        public string DisplayName { get; }
        public string Detail { get; }

        public EnrollmentCompletedEventArgs(string profileId, string displayName, string detail)
        {
            ProfileId = profileId;
            DisplayName = displayName;
            Detail = detail;
        }
    }

    public class EnrollmentFailedEventArgs : EventArgs
    {
        public string ReasonCode { get; }
        public string Detail { get; }

        public EnrollmentFailedEventArgs(string reasonCode, string detail)
        {
            ReasonCode = reasonCode;
            Detail = detail;
        }
    }

    public class EnrollmentErrorEventArgs : EventArgs
    {
        public string ReasonCode { get; }
        public string Detail { get; }

        public EnrollmentErrorEventArgs(string reasonCode, string detail)
        {
            ReasonCode = reasonCode;
            Detail = detail;
        }
    }

    public interface IEnrollmentPipeClient : IAsyncDisposable
    {
        bool IsConnected { get; }
        event EventHandler<PreviewFrameEventArgs>? PreviewFrameReceived;
        event EventHandler<PoseCompletedEventArgs>? PoseCompleted;
        event EventHandler<EnrollmentCompletedEventArgs>? EnrollmentCompleted;
        event EventHandler<EnrollmentFailedEventArgs>? EnrollmentFailed;
        event EventHandler<EnrollmentErrorEventArgs>? ErrorOccurred;
        event EventHandler? ReadyReceived;
        event EventHandler? Disconnected;

        Task ConnectAsync(string pipeName = "PeekEnrollment", int timeoutMs = 4000, CancellationToken ct = default);
        Task StartEnrollmentAsync(string displayName, CancellationToken ct = default);
        Task CancelEnrollmentAsync(string reason = "USER_CANCELLED", CancellationToken ct = default);
        Task DisconnectAsync();
    }
}
