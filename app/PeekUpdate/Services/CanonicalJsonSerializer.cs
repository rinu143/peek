using System.Text;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public static class CanonicalJsonSerializer
    {
        public static string Serialize(UpdateManifest manifest)
        {
            var sb = new StringBuilder();
            sb.Append('{');
            sb.Append("\"channel\":\"").Append(manifest.Channel ?? string.Empty).Append("\",");
            sb.Append("\"critical\":").Append(manifest.Critical ? "true" : "false").Append(',');
            sb.Append("\"expires\":\"").Append(manifest.Expires ?? string.Empty).Append("\",");
            sb.Append("\"min_supported_version\":\"").Append(manifest.MinSupportedVersion ?? string.Empty).Append("\",");
            sb.Append("\"notes_url\":\"").Append(manifest.NotesUrl ?? string.Empty).Append("\",");
            sb.Append("\"publisher_thumbprint\":\"").Append(manifest.PublisherThumbprint ?? string.Empty).Append("\",");
            sb.Append("\"released\":\"").Append(manifest.Released ?? string.Empty).Append("\",");
            sb.Append("\"schema\":").Append(manifest.Schema).Append(',');
            sb.Append("\"sha256\":\"").Append(manifest.Sha256 ?? string.Empty).Append("\",");
            sb.Append("\"size\":").Append(manifest.Size).Append(',');
            sb.Append("\"url\":\"").Append(manifest.Url ?? string.Empty).Append("\",");
            sb.Append("\"version\":\"").Append(manifest.Version ?? string.Empty).Append('\"');
            sb.Append('}');
            return sb.ToString();
        }

        public static byte[] SerializeToBytes(UpdateManifest manifest)
        {
            return Encoding.UTF8.GetBytes(Serialize(manifest));
        }
    }
}
