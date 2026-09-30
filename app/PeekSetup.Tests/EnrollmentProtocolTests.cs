using System.Text.Json;
using PeekSetup.Models;
using Xunit;

namespace PeekSetup.Tests
{
    public class EnrollmentProtocolTests
    {
        [Fact]
        public void ProtocolVersion_IsConstant1_0()
        {
            Assert.Equal("1.0", EnrollmentProtocol.ProtocolVersion);
        }

        [Fact]
        public void CreateStartEnrollment_ProducesValidJsonWithRequiredFields()
        {
            string json = EnrollmentProtocol.CreateStartEnrollment("Alice Smith");
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            Assert.Equal("1.0", root.GetProperty("protocol_version").GetString());
            Assert.Equal(EnrollmentProtocol.MsgStartEnrollment, root.GetProperty("type").GetString());
            Assert.Equal("Alice Smith", root.GetProperty("display_name").GetString());
            Assert.True(root.TryGetProperty("timestamp", out _));
        }

        [Fact]
        public void CreateCancelEnrollment_ProducesValidJsonWithReason()
        {
            string json = EnrollmentProtocol.CreateCancelEnrollment("USER_ABORTED");
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            Assert.Equal("1.0", root.GetProperty("protocol_version").GetString());
            Assert.Equal(EnrollmentProtocol.MsgCancelEnrollment, root.GetProperty("type").GetString());
            Assert.Equal("USER_ABORTED", root.GetProperty("reason").GetString());
            Assert.True(root.TryGetProperty("timestamp", out _));
        }

        [Fact]
        public void CreatePing_ProducesValidJson()
        {
            string json = EnrollmentProtocol.CreatePing();
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            Assert.Equal("1.0", root.GetProperty("protocol_version").GetString());
            Assert.Equal(EnrollmentProtocol.MsgPing, root.GetProperty("type").GetString());
        }

        [Fact]
        public void OrderedPoses_ContainsExactlyNineDistinctPoses()
        {
            Assert.Equal(9, EnrollmentProtocol.OrderedPoses.Length);
            var set = new System.Collections.Generic.HashSet<string>(EnrollmentProtocol.OrderedPoses);
            Assert.Equal(9, set.Count);

            foreach (var pose in EnrollmentProtocol.OrderedPoses)
            {
                string friendly = EnrollmentProtocol.GetPoseFriendlyName(pose);
                Assert.False(string.IsNullOrWhiteSpace(friendly));
                Assert.NotEqual(pose, friendly); // All 9 poses have human-readable friendly names
            }
        }
    }
}
