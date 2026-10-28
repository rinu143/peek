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
        public const string MsgStatusRequest = "STATUS_REQUEST";

        // Server -> Client
        public const string MsgEnrollmentReady = "ENROLLMENT_READY";
        public const string MsgPreviewFrame = "PREVIEW_FRAME";
        public const string MsgPoseComplete = "POSE_COMPLETE";
        public const string MsgEnrollmentComplete = "ENROLLMENT_COMPLETE";
        public const string MsgEnrollmentFailed = "ENROLLMENT_FAILED";
        public const string MsgError = "ERROR";
        public const string MsgPong = "PONG";
        public const string MsgStatusResponse = "STATUS_RESPONSE";

        // Reason Codes
        public const string ReasonCancelled = "CANCELLED";
        public const string ReasonCameraError = "CAMERA_ERROR";
        public const string ReasonCameraUnavailable = "CAMERA_UNAVAILABLE";
        public const string ReasonSessionBusy = "SESSION_BUSY";
        public const string ReasonInvalidMessage = "INVALID_MESSAGE";
        public const string ReasonInternalError = "INTERNAL_ERROR";
        public const string ReasonQualityFailed = "QUALITY_FAILED";
        public const string ReasonTimeout = "TIMEOUT";
        public const string ReasonConfirmationRequired = "CONFIRMATION_REQUIRED";

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

        public static string GetPoseIconData(string? pose) => pose?.ToUpperInvariant() switch
        {
            PoseCenter => "M 12,9 A 3,3 0 1 1 12,15 A 3,3 0 1 1 12,9",
            PoseRight => "M 10,7 L 15,12 L 10,17",
            PoseLeft => "M 14,7 L 9,12 L 14,17",
            PoseUp => "M 7,14 L 12,9 L 17,14",
            PoseDown => "M 7,10 L 12,15 L 17,10",
            PoseUpperRight => "M 9,15 L 15,9 M 10,9 L 15,9 L 15,14",
            PoseUpperLeft => "M 15,15 L 9,9 M 9,14 L 9,9 L 14,9",
            PoseLowerRight => "M 9,9 L 15,15 M 10,15 L 15,15 L 15,10",
            PoseLowerLeft => "M 15,9 L 9,15 M 9,10 L 9,15 L 14,15",
            _ => "M 12,9 A 3,3 0 1 1 12,15 A 3,3 0 1 1 12,9"
        };

        private static readonly JsonSerializerOptions JsonOptions = new()
        {
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
            PropertyNamingPolicy = null
        };

        public static string CreateStartEnrollment(
            string displayName,
            bool confirmed = false,
            string? password = null,
            bool cleanPreview = true,
            bool keepExisting = true,
            string? profileId = null)
        {
            var msg = new
            {
                protocol_version = ProtocolVersion,
                type = MsgStartEnrollment,
                display_name = displayName,
                confirmed = confirmed ? (bool?)true : null,
                clean_preview = cleanPreview ? (bool?)true : null,
                keep_existing = keepExisting ? (bool?)true : null,
                profile_id = profileId,
                password = password,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds()
            };
            return JsonSerializer.Serialize(msg, JsonOptions);
        }

        public static EnrollmentOverlayData? ParseOverlay(JsonElement overlayElement)
        {
            if (overlayElement.ValueKind != JsonValueKind.Object)
            {
                return null;
            }

            try
            {
                FaceOverlayData? face = null;
                if (overlayElement.TryGetProperty("face", out var faceEl) && faceEl.ValueKind == JsonValueKind.Object)
                {
                    bool found = faceEl.TryGetProperty("found", out var foundEl) && foundEl.ValueKind == JsonValueKind.True;
                    
                    double[] bbox = new double[4];
                    if (faceEl.TryGetProperty("bbox", out var bboxEl) && bboxEl.ValueKind == JsonValueKind.Array)
                    {
                        int i = 0;
                        foreach (var item in bboxEl.EnumerateArray())
                        {
                            if (i < 4 && item.ValueKind == JsonValueKind.Number && item.TryGetDouble(out double val))
                            {
                                bbox[i++] = val;
                            }
                        }
                    }

                    var landmarksList = new System.Collections.Generic.List<double[]>();
                    if (faceEl.TryGetProperty("landmarks", out var lmEl) && lmEl.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var ptEl in lmEl.EnumerateArray())
                        {
                            if (ptEl.ValueKind == JsonValueKind.Array)
                            {
                                var pt = new double[2];
                                int pi = 0;
                                foreach (var c in ptEl.EnumerateArray())
                                {
                                    if (pi < 2 && c.ValueKind == JsonValueKind.Number && c.TryGetDouble(out double coord))
                                    {
                                        pt[pi++] = coord;
                                    }
                                }
                                landmarksList.Add(pt);
                            }
                        }
                    }

                    double yaw = faceEl.TryGetProperty("yaw", out var yawEl) && yawEl.ValueKind == JsonValueKind.Number && yawEl.TryGetDouble(out double yv) ? yv : 0.0;
                    double pitch = faceEl.TryGetProperty("pitch", out var pitchEl) && pitchEl.ValueKind == JsonValueKind.Number && pitchEl.TryGetDouble(out double pv) ? pv : 0.0;

                    face = new FaceOverlayData(found, bbox, landmarksList.ToArray(), yaw, pitch);
                }

                bool poseMatched = overlayElement.TryGetProperty("pose_matched", out var pmEl) && pmEl.ValueKind == JsonValueKind.True;
                bool qualityPassed = overlayElement.TryGetProperty("quality_passed", out var qpEl) && qpEl.ValueKind == JsonValueKind.True;

                TargetPoseData? target = null;
                if (overlayElement.TryGetProperty("target", out var tgtEl) && tgtEl.ValueKind == JsonValueKind.Object)
                {
                    string pose = tgtEl.TryGetProperty("pose", out var pEl) && pEl.ValueKind == JsonValueKind.String ? pEl.GetString() ?? "" : "";
                    double ty = tgtEl.TryGetProperty("yaw", out var tyEl) && tyEl.ValueKind == JsonValueKind.Number && tyEl.TryGetDouble(out double tyv) ? tyv : 0.0;
                    double tp = tgtEl.TryGetProperty("pitch", out var tpEl) && tpEl.ValueKind == JsonValueKind.Number && tpEl.TryGetDouble(out double tpv) ? tpv : 0.0;
                    target = new TargetPoseData(pose, ty, tp);
                }

                SampleProgressData? samples = null;
                if (overlayElement.TryGetProperty("samples", out var sampEl) && sampEl.ValueKind == JsonValueKind.Object)
                {
                    int collected = sampEl.TryGetProperty("collected", out var cEl) && cEl.ValueKind == JsonValueKind.Number && cEl.TryGetInt32(out int cv) ? cv : 0;
                    int targetCand = sampEl.TryGetProperty("target", out var tEl) && tEl.ValueKind == JsonValueKind.Number && tEl.TryGetInt32(out int tv) ? tv : 1;
                    samples = new SampleProgressData(collected, targetCand);
                }

                int posesCompleted = overlayElement.TryGetProperty("poses_completed", out var pcEl) && pcEl.ValueKind == JsonValueKind.Number && pcEl.TryGetInt32(out int pcv) ? pcv : 0;
                string hint = overlayElement.TryGetProperty("hint", out var hEl) && hEl.ValueKind == JsonValueKind.String ? hEl.GetString() ?? "none" : "none";

                return new EnrollmentOverlayData(face, poseMatched, qualityPassed, target, samples, posesCompleted, hint);
            }
            catch
            {
                // Tolerant parsing: corrupt or malformed payload falls back softly
                return null;
            }
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

        public static string CreateStatusRequest()
        {
            var msg = new
            {
                protocol_version = ProtocolVersion,
                type = MsgStatusRequest,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds()
            };
            return JsonSerializer.Serialize(msg, JsonOptions);
        }
    }

    public record FaceOverlayData(
        bool Found,
        double[] Bbox,
        double[][] Landmarks,
        double Yaw,
        double Pitch
    );

    public record TargetPoseData(
        string Pose,
        double Yaw,
        double Pitch
    );

    public record SampleProgressData(
        int Collected,
        int Target
    );

    public record EnrollmentOverlayData(
        FaceOverlayData? Face,
        bool PoseMatched,
        bool QualityPassed,
        TargetPoseData? Target,
        SampleProgressData? Samples,
        int PosesCompleted,
        string Hint = "none"
    );

    public record PreviewFrameData(
        string FrameJpegB64,
        string TargetPose,
        string QualityState,
        double ProgressPercentage,
        string GuidanceText,
        double Timestamp,
        EnrollmentOverlayData? Overlay = null,
        string? FrameKind = null
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

    public record StatusResponseData(
        bool HasEnrolledProfile,
        string DisplayName,
        string ProfileId
    );
}
