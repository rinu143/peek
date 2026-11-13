using System.Text;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public static class CanonicalJsonSerializer
    {
        public static string EscapeString(string? value)
        {
            if (string.IsNullOrEmpty(value)) return string.Empty;

            var sb = new StringBuilder(value.Length);
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"':
                        sb.Append("\\\"");
                        break;
                    case '\\':
                        sb.Append("\\\\");
                        break;
                    case '\b':
                        sb.Append("\\b");
                        break;
                    case '\f':
                        sb.Append("\\f");
                        break;
                    case '\n':
                        sb.Append("\\n");
                        break;
                    case '\r':
                        sb.Append("\\r");
                        break;
                    case '\t':
                        sb.Append("\\t");
                        break;
                    default:
                        if (c < 0x20)
                        {
                            sb.Append($"\\u{(int)c:x4}");
                        }
                        else
                        {
                            sb.Append(c);
                        }
                        break;
                }
            }
            return sb.ToString();
        }

        public static string Serialize(UpdateManifest manifest)
        {
            var sb = new StringBuilder();
            sb.Append('{');
            sb.Append("\"channel\":\"").Append(EscapeString(manifest.Channel)).Append("\",");
            sb.Append("\"critical\":").Append(manifest.Critical ? "true" : "false").Append(',');
            sb.Append("\"expires\":\"").Append(EscapeString(manifest.Expires)).Append("\",");
            sb.Append("\"min_supported_version\":\"").Append(EscapeString(manifest.MinSupportedVersion)).Append("\",");
            sb.Append("\"notes_url\":\"").Append(EscapeString(manifest.NotesUrl)).Append("\",");
            sb.Append("\"publisher_thumbprint\":\"").Append(EscapeString(manifest.PublisherThumbprint)).Append("\",");
            sb.Append("\"released\":\"").Append(EscapeString(manifest.Released)).Append("\",");
            sb.Append("\"schema\":").Append(manifest.Schema).Append(',');
            sb.Append("\"sha256\":\"").Append(EscapeString(manifest.Sha256)).Append("\",");
            sb.Append("\"size\":").Append(manifest.Size).Append(',');
            sb.Append("\"url\":\"").Append(EscapeString(manifest.Url)).Append("\",");
            sb.Append("\"version\":\"").Append(EscapeString(manifest.Version)).Append('\"');
            sb.Append('}');
            return sb.ToString();
        }

        public static string SerializeToString(UpdateManifest manifest) => Serialize(manifest);

        public static byte[] SerializeToBytes(UpdateManifest manifest)
        {
            return Encoding.UTF8.GetBytes(Serialize(manifest));
        }
    }
}
