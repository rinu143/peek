using System;
using System.Text.Json;
using System.Threading.Tasks;
using PeekSetup.Models;
using PeekSetup.ViewModels;
using Xunit;

namespace PeekSetup.Tests
{
    public class NativeOverlayTests
    {
        [Fact]
        public void ParseOverlay_ValidJson_ParsesAllFieldsCorrectly()
        {
            string json = """
            {
                "face": {
                    "found": true,
                    "bbox": [0.1, 0.2, 0.8, 0.9],
                    "landmarks": [
                        [0.3, 0.4],
                        [0.7, 0.4],
                        [0.5, 0.6],
                        [0.35, 0.75],
                        [0.65, 0.75]
                    ],
                    "yaw": 0.05,
                    "pitch": -0.02
                },
                "pose_matched": true,
                "quality_passed": true,
                "target": {
                    "pose": "CENTER",
                    "yaw": 0.0,
                    "pitch": 0.0
                },
                "samples": {
                    "collected": 15,
                    "target": 30
                },
                "poses_completed": 3,
                "extra_unexpected_field": "ignore_me"
            }
            """;

            using var doc = JsonDocument.Parse(json);
            var overlay = EnrollmentProtocol.ParseOverlay(doc.RootElement);

            Assert.NotNull(overlay);
            Assert.True(overlay.PoseMatched);
            Assert.True(overlay.QualityPassed);
            Assert.Equal(3, overlay.PosesCompleted);

            Assert.NotNull(overlay.Face);
            Assert.True(overlay.Face.Found);
            Assert.Equal(4, overlay.Face.Bbox.Length);
            Assert.Equal(0.1, overlay.Face.Bbox[0]);
            Assert.Equal(0.2, overlay.Face.Bbox[1]);
            Assert.Equal(0.8, overlay.Face.Bbox[2]);
            Assert.Equal(0.9, overlay.Face.Bbox[3]);
            Assert.Equal(5, overlay.Face.Landmarks.Length);
            Assert.Equal(0.05, overlay.Face.Yaw);
            Assert.Equal(-0.02, overlay.Face.Pitch);

            Assert.NotNull(overlay.Target);
            Assert.Equal("CENTER", overlay.Target.Pose);
            Assert.Equal(0.0, overlay.Target.Yaw);
            Assert.Equal(0.0, overlay.Target.Pitch);

            Assert.NotNull(overlay.Samples);
            Assert.Equal(15, overlay.Samples.Collected);
            Assert.Equal(30, overlay.Samples.Target);
        }

        [Fact]
        public void ParseOverlay_MissingFaceOrEmptyJson_ToleratesGracefully()
        {
            string json = """
            {
                "pose_matched": false,
                "quality_passed": true
            }
            """;

            using var doc = JsonDocument.Parse(json);
            var overlay = EnrollmentProtocol.ParseOverlay(doc.RootElement);

            Assert.NotNull(overlay);
            Assert.False(overlay.PoseMatched);
            Assert.True(overlay.QualityPassed);
            Assert.Null(overlay.Face);
            Assert.Null(overlay.Target);
            Assert.Null(overlay.Samples);
        }

        [Fact]
        public void ParseOverlay_MalformedBbox_DoesNotThrowAndHandlesSafely()
        {
            string json = """
            {
                "face": {
                    "found": true,
                    "bbox": [0.1, "not_a_number"]
                }
            }
            """;

            using var doc = JsonDocument.Parse(json);
            var overlay = EnrollmentProtocol.ParseOverlay(doc.RootElement);

            Assert.NotNull(overlay);
            Assert.NotNull(overlay.Face);
            Assert.True(overlay.Face.Found);
            Assert.Equal(4, overlay.Face.Bbox.Length);
            Assert.Equal(0.1, overlay.Face.Bbox[0]);
            Assert.Equal(0.0, overlay.Face.Bbox[1]);
        }

        [Fact]
        public void ApplyOverlay_TransformsCoordinatesToApertureDimensions()
        {
            var vm = new MainViewModel();

            var overlay = new EnrollmentOverlayData(
                Face: new FaceOverlayData(
                    Found: true,
                    Bbox: new double[] { 0.25, 0.20, 0.75, 0.80 },
                    Landmarks: Array.Empty<double[]>(),
                    Yaw: 0.0,
                    Pitch: 0.0
                ),
                PoseMatched: true,
                QualityPassed: true,
                Target: new TargetPoseData("CENTER", 0.0, 0.0),
                Samples: new SampleProgressData(15, 30),
                PosesCompleted: 2
            );

            vm.ApplyOverlay(overlay);

            Assert.True(vm.HasOverlay);
            Assert.True(vm.IsFaceDetected);
            Assert.True(vm.IsPoseMatched);
            Assert.True(vm.IsBracketGreen);

            // 0.25 * 296 = 74.0, 0.20 * 296 = 59.2, 0.75 * 296 = 222.0, 0.80 * 296 = 236.8
            Assert.Equal(74.0, vm.FaceBboxLeft, 1);
            Assert.Equal(59.2, vm.FaceBboxTop, 1);
            Assert.Equal(148.0, vm.FaceBboxWidth, 1);
            Assert.Equal(177.6, vm.FaceBboxHeight, 1);

            // Corner brackets path string should be non-empty and well-formed
            Assert.False(string.IsNullOrWhiteSpace(vm.CornerBracketsData));
            Assert.StartsWith("M 0,", vm.CornerBracketsData);

            // Sample progress 15 / 30 = 0.5
            Assert.Equal(0.5, vm.SampleProgress, 3);
            Assert.False(string.IsNullOrWhiteSpace(vm.SampleProgressRingData));

            // Target dot centered on matched pose (155, 155)
            Assert.Equal(155.0, vm.TargetDotX, 1);
            Assert.Equal(155.0, vm.TargetDotY, 1);
            Assert.Equal(150.0, vm.TargetDotCanvasLeft, 1);
            Assert.Equal(150.0, vm.TargetDotCanvasTop, 1);
        }

        [Fact]
        public void ApplyOverlay_AmberBrackets_WhenPoseNotMatchedOrQualityNotPassed()
        {
            var vm = new MainViewModel();

            var overlayNotMatched = new EnrollmentOverlayData(
                Face: new FaceOverlayData(
                    Found: true,
                    Bbox: new double[] { 0.2, 0.2, 0.6, 0.6 },
                    Landmarks: Array.Empty<double[]>(),
                    Yaw: 0.1,
                    Pitch: 0.0
                ),
                PoseMatched: false,
                QualityPassed: true,
                Target: new TargetPoseData("YAW_LEFT", -0.30, 0.0),
                Samples: new SampleProgressData(0, 30),
                PosesCompleted: 0
            );

            vm.ApplyOverlay(overlayNotMatched);
            Assert.False(vm.IsBracketGreen); // Amber when not matched

            var overlayPoorQuality = new EnrollmentOverlayData(
                Face: new FaceOverlayData(
                    Found: true,
                    Bbox: new double[] { 0.2, 0.2, 0.6, 0.6 },
                    Landmarks: Array.Empty<double[]>(),
                    Yaw: -0.30,
                    Pitch: 0.0
                ),
                PoseMatched: true,
                QualityPassed: false,
                Target: new TargetPoseData("YAW_LEFT", -0.30, 0.0),
                Samples: new SampleProgressData(5, 30),
                PosesCompleted: 0
            );

            vm.ApplyOverlay(overlayPoorQuality);
            Assert.False(vm.IsBracketGreen); // Amber when quality failed
        }

        [Fact]
        public void DirectionDot_ComputesAngleOnOuterRing_ForDirectionalPoses()
        {
            var vm = new MainViewModel();

            // Target: Yaw Right (positive yaw: dx > 0, dy = 0 -> angle = 0)
            var overlay = new EnrollmentOverlayData(
                Face: new FaceOverlayData(
                    Found: true,
                    Bbox: new double[] { 0.2, 0.2, 0.6, 0.6 },
                    Landmarks: Array.Empty<double[]>(),
                    Yaw: 0.0,
                    Pitch: 0.0
                ),
                PoseMatched: false,
                QualityPassed: true,
                Target: new TargetPoseData("YAW_RIGHT", 0.35, 0.0),
                Samples: new SampleProgressData(0, 30),
                PosesCompleted: 1
            );

            vm.ApplyOverlay(overlay);

            // Ring center is (155, 155), radius is 148. For angle 0: X ~ 155 + 148 = 303 (clamped to 295), Y ~ 155
            Assert.True(vm.TargetDotX > 250.0);
            Assert.InRange(vm.TargetDotY, 140.0, 170.0);
        }

        [Fact]
        public void ClearOverlay_ResetsOverlayProperties()
        {
            var vm = new MainViewModel();

            var overlay = new EnrollmentOverlayData(
                Face: new FaceOverlayData(
                    Found: true,
                    Bbox: new double[] { 0.2, 0.2, 0.6, 0.6 },
                    Landmarks: Array.Empty<double[]>(),
                    Yaw: 0.0,
                    Pitch: 0.0
                ),
                PoseMatched: true,
                QualityPassed: true,
                Target: new TargetPoseData("CENTER", 0.0, 0.0),
                Samples: new SampleProgressData(10, 30),
                PosesCompleted: 1
            );

            vm.ApplyOverlay(overlay);
            Assert.True(vm.HasOverlay);
            Assert.True(vm.IsFaceDetected);

            vm.ClearOverlay();
            Assert.False(vm.HasOverlay);
            Assert.False(vm.IsFaceDetected);
            Assert.Equal(string.Empty, vm.CornerBracketsData);
        }

        [Fact]
        public void DetailsToggle_ExpandsAndCollapsesTechnicalDetails()
        {
            var vm = new MainViewModel();
            vm.RawTechnicalDetails = "Exception: Pipe server disconnected with error code 0x5.";

            Assert.True(vm.HasTechnicalDetails);
            Assert.False(vm.IsDetailsExpanded);
            Assert.Equal("Show Details", vm.DetailsToggleText);

            vm.ToggleDetailsCommand.Execute(null);
            Assert.True(vm.IsDetailsExpanded);
            Assert.Equal("Hide Details", vm.DetailsToggleText);

            vm.ToggleDetailsCommand.Execute(null);
            Assert.False(vm.IsDetailsExpanded);
            Assert.Equal("Show Details", vm.DetailsToggleText);
        }

        [Fact]
        public void ReducedMotion_PropertyAccessibleWithoutException()
        {
            var vm = new MainViewModel();
            // Should evaluate without throwing, matching system setting
            bool isReduced = vm.IsReducedMotion;
            Assert.True(isReduced == true || isReduced == false);
        }

        [Fact]
        public void ParseOverlay_CompletelyInvalidJson_ReturnsNullGracefully()
        {
            using var doc = JsonDocument.Parse("\"not an object\"");
            var overlay = EnrollmentProtocol.ParseOverlay(doc.RootElement);
            Assert.Null(overlay);
        }
    }
}
