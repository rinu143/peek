using System;
using System.Text.Json.Serialization;

namespace PeekUpdate.Models
{
    public class UpdateManifest
    {
        [JsonPropertyName("schema")]
        public int Schema { get; set; } = 1;

        [JsonPropertyName("channel")]
        public string Channel { get; set; } = "stable";

        [JsonPropertyName("version")]
        public string Version { get; set; } = string.Empty;

        [JsonPropertyName("released")]
        public string Released { get; set; } = string.Empty;

        [JsonPropertyName("min_supported_version")]
        public string MinSupportedVersion { get; set; } = "0.1.0";

        [JsonPropertyName("critical")]
        public bool Critical { get; set; }

        [JsonPropertyName("notes_url")]
        public string NotesUrl { get; set; } = string.Empty;

        [JsonPropertyName("url")]
        public string Url { get; set; } = string.Empty;

        [JsonPropertyName("sha256")]
        public string Sha256 { get; set; } = string.Empty;

        [JsonPropertyName("size")]
        public long Size { get; set; }

        [JsonPropertyName("publisher_thumbprint")]
        public string PublisherThumbprint { get; set; } = string.Empty;

        [JsonPropertyName("expires")]
        public string Expires { get; set; } = string.Empty;

        [JsonPropertyName("signature")]
        public string Signature { get; set; } = string.Empty;
    }
}
