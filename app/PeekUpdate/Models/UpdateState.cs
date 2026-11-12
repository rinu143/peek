using System.Text.Json.Serialization;

namespace PeekUpdate.Models
{
    public class UpdateState
    {
        [JsonPropertyName("last_checked")]
        public string LastChecked { get; set; } = string.Empty;

        [JsonPropertyName("status")]
        public string Status { get; set; } = "up_to_date";

        [JsonPropertyName("installed_version")]
        public string InstalledVersion { get; set; } = string.Empty;

        [JsonPropertyName("available_version")]
        public string? AvailableVersion { get; set; }

        [JsonPropertyName("critical")]
        public bool Critical { get; set; }

        [JsonPropertyName("release_date")]
        public string? ReleaseDate { get; set; }

        [JsonPropertyName("notes_url")]
        public string? NotesUrl { get; set; }

        [JsonPropertyName("download_url")]
        public string? DownloadUrl { get; set; }

        [JsonPropertyName("sha256")]
        public string? Sha256 { get; set; }

        [JsonPropertyName("size")]
        public long Size { get; set; }

        [JsonPropertyName("publisher_thumbprint")]
        public string? PublisherThumbprint { get; set; }

        [JsonPropertyName("local_installer_path")]
        public string? LocalInstallerPath { get; set; }

        [JsonPropertyName("error_message")]
        public string? ErrorMessage { get; set; }

        [JsonPropertyName("channel")]
        public string Channel { get; set; } = "stable";
    }
}
