using System;
using System.Text.RegularExpressions;

namespace PeekUpdate.Models
{
    public class SemVersion : IComparable<SemVersion>, IEquatable<SemVersion>
    {
        private static readonly Regex SemVerRegex = new(
            @"^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?:-(?<prerelease>[0-9A-Za-z.-]+))?(?:\+(?<build>[0-9A-Za-z.-]+))?$",
            RegexOptions.Compiled);

        public int Major { get; }
        public int Minor { get; }
        public int Patch { get; }
        public string? Prerelease { get; }
        public string? Build { get; }
        public string Raw { get; }

        public SemVersion(int major, int minor, int patch, string? prerelease = null, string? build = null, string? raw = null)
        {
            Major = major;
            Minor = minor;
            Patch = patch;
            Prerelease = string.IsNullOrWhiteSpace(prerelease) ? null : prerelease;
            Build = string.IsNullOrWhiteSpace(build) ? null : build;
            Raw = raw ?? $"{major}.{minor}.{patch}" + (Prerelease != null ? $"-{Prerelease}" : "") + (Build != null ? $"+{Build}" : "");
        }

        public static bool TryParse(string? input, out SemVersion? version)
        {
            version = null;
            if (string.IsNullOrWhiteSpace(input)) return false;

            var match = SemVerRegex.Match(input.Trim());
            if (!match.Success) return false;

            int major = int.Parse(match.Groups["major"].Value);
            int minor = int.Parse(match.Groups["minor"].Value);
            int patch = int.Parse(match.Groups["patch"].Value);
            string? pre = match.Groups["prerelease"].Success ? match.Groups["prerelease"].Value : null;
            string? bld = match.Groups["build"].Success ? match.Groups["build"].Value : null;

            version = new SemVersion(major, minor, patch, pre, bld, input.Trim());
            return true;
        }

        public static SemVersion Parse(string input)
        {
            if (TryParse(input, out var v) && v != null) return v;
            throw new FormatException($"Invalid Semantic Version string: '{input}'");
        }

        public int CompareTo(SemVersion? other)
        {
            if (other is null) return 1;

            int cmp = Major.CompareTo(other.Major);
            if (cmp != 0) return cmp;

            cmp = Minor.CompareTo(other.Minor);
            if (cmp != 0) return cmp;

            cmp = Patch.CompareTo(other.Patch);
            if (cmp != 0) return cmp;

            // Prerelease comparison:
            // 1.0.0 > 1.0.0-beta
            if (Prerelease == null && other.Prerelease != null) return 1;
            if (Prerelease != null && other.Prerelease == null) return -1;
            if (Prerelease == null && other.Prerelease == null) return 0;

            // Both have prereleases: compare dot-separated identifiers
            string[] parts1 = Prerelease!.Split('.');
            string[] parts2 = other.Prerelease!.Split('.');
            int minLen = Math.Min(parts1.Length, parts2.Length);

            for (int i = 0; i < minLen; i++)
            {
                string p1 = parts1[i];
                string p2 = parts2[i];

                bool isNum1 = long.TryParse(p1, out long n1);
                bool isNum2 = long.TryParse(p2, out long n2);

                if (isNum1 && isNum2)
                {
                    int nCmp = n1.CompareTo(n2);
                    if (nCmp != 0) return nCmp;
                }
                else if (isNum1)
                {
                    return -1; // numeric has lower precedence than non-numeric
                }
                else if (isNum2)
                {
                    return 1;
                }
                else
                {
                    int sCmp = string.CompareOrdinal(p1, p2);
                    if (sCmp != 0) return sCmp;
                }
            }

            return parts1.Length.CompareTo(parts2.Length);
        }

        public static bool operator >(SemVersion a, SemVersion b) => a.CompareTo(b) > 0;
        public static bool operator <(SemVersion a, SemVersion b) => a.CompareTo(b) < 0;
        public static bool operator >=(SemVersion a, SemVersion b) => a.CompareTo(b) >= 0;
        public static bool operator <=(SemVersion a, SemVersion b) => a.CompareTo(b) <= 0;
        public static bool operator ==(SemVersion? a, SemVersion? b) => a?.CompareTo(b) == 0;
        public static bool operator !=(SemVersion? a, SemVersion? b) => !(a == b);

        public bool Equals(SemVersion? other) => CompareTo(other) == 0;
        public override bool Equals(object? obj) => obj is SemVersion other && Equals(other);
        public override int GetHashCode() => HashCode.Combine(Major, Minor, Patch, Prerelease?.ToLowerInvariant());
        public override string ToString() => Raw;
    }
}
