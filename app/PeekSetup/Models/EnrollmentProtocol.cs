using System;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace PeekSetup.Models
{
    public static class EnrollmentProtocol
    {
        public const string ProtocolVersion = "1.0";

        // Client -> Server
        public const string MsgStartEnrollment = "START_ENROLLMENT";
        public const string MsgCancelEnrollment = "CANCEL_ENROLLMENT";
        public const string MsgPing = "PING";

        // Server -> Client
        public const string MsgEnrollmentReady = "ENROLLMENT_READY";
        public const string MsgPreviewFrame = "PREVIEW_FRAME";
        public const string MsgPoseComplete = "POSE_COMPLETE";
        public const string MsgEnrollmentComplete = "ENROLLMENT_COMPLETE";
        public const string MsgEnrollmentFailed = "ENROLLMENT_FAILED";
        public const string MsgError = "ERROR";
        public const string MsgPong = "PONG";

        // Reason Codes
        public const string ReasonCancelled = "CANCELLED";
        public const string ReasonCameraError = "CAMERA_ERROR";
        public const string ReasonCameraUnavailable = "CAMERA_UNAVAILABLE";
        public const string ReasonSessionBusy = "SESSION_BUSY";
        public const string ReasonInvalidMessage = "INVALID_MESSAGE";
        public const string ReasonInternalError = "INTERNAL_ERROR";
        public const string ReasonQualityFailed = "QUALITY_FAILED";
        public const string ReasonTimeout = "TIMEOUT";

        // Poses
        public const string PoseCenter = "CENTER";
        public const string PoseLeft = "LEFT";
        public const string PoseRight = "RIGHT";
        public const string PoseUp = "UP";
        public const string PoseDown = "DOWN";
        public const string PoseUpperLeft = "UPPER_LEFT";
        public const string PoseUpperRight = "UPPER_RIGHT";
        public const string PoseLowerLeft = "LOWER_LEFT";
        public const string PoseLowerRight = "LOWER_RIGHT";

        public static readonly string[] OrderedPoses = new[]
        {
            PoseCenter,
            PoseLeft,
            PoseRight,
            PoseUp,
            PoseDown,
            PoseUpperLeft,
            PoseUpperRight,
            PoseLowerLeft,
            PoseLowerRight
        };

        public static string GetPoseFriendlyName(string pose) => pose switch
        {
            PoseCenter => "Look Straight",
            PoseLeft => "Turn Head Left",
            PoseRight => "Turn Head Right",
            PoseUp => "Tilt Head Up",
            PoseDown => "Tilt Head Down",
            PoseUpperLeft => "Look Up-Left",
            PoseUpperRight => "Look Up-Right",
            PoseLowerLeft => "Look Down-Left",
            PoseLowerRight => "Look Down-Right",
            _ => pose
        };

        private static readonly JsonSerializerOptions JsonOptions = new()
        {
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
            PropertyNamingPolicy = null
        };

        public static string CreateStartEnrollment(string displayName)
        {
            var msg = new
            {
                protocol_version = ProtocolVersion,
                type = MsgStartEnrollment,
                display_name = displayName,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds()
            };
            return JsonSerializer.Serialize(msg, JsonOptions);
        }

        public static string CreateCancelEnrollment(string reason = "USER_CANCELLED")
        {
            var msg = new
            {
                protocol_version = ProtocolVersion,
                type = MsgCancelEnrollment,
                reason = reason,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds()
            };
            return JsonSerializer.Serialize(msg, JsonOptions);
        }

        public static string CreatePing()
        {
            var msg = new
            {
                protocol_version = ProtocolVersion,
                type = MsgPing,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds()
            };
            return JsonSerializer.Serialize(msg, JsonOptions);
        }
    }

    public record PreviewFrameData(
        string FrameJpegB64,
        string TargetPose,
        string QualityState,
        double ProgressPercentage,
        string GuidanceText,
        double Timestamp
    );

    public record PoseCompleteData(
        string CompletedPose,
        int PoseIndex,
        int TotalPoses,
        string Detail
    );

    public record EnrollmentCompleteData(
        string ProfileId,
        string DisplayName,
        string Detail
    );

    public record EnrollmentFailedData(
        string ReasonCode,
        string Detail
    );

    public record EnrollmentErrorData(
        string ReasonCode,
        string Detail
    );
}
