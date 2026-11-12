using System.Text.Json.Serialization;

namespace PeekUpdate.Models
{
    public class UpdateSettings
    {
        [JsonPropertyName("auto_check")]
        public bool? AutoCheck { get; set; } = true;

        [JsonPropertyName("snooze_until")]
        public string? SnoozeUntil { get; set; }

        [JsonPropertyName("channel")]
        public string? Channel { get; set; } = "stable";

        [JsonPropertyName("skipped_version")]
        public string? SkippedVersion { get; set; }

        [JsonPropertyName("last_etag")]
        public string? LastETag { get; set; }
    }
}
