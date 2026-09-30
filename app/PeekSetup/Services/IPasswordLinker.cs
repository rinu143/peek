using System.Security;

namespace PeekSetup.Services
{
    public interface IPasswordLinker
    {
        /// <summary>
        /// Validates the provided Windows password via IPasswordValidator and, if accepted,
        /// writes an encrypted DPAPI wrapped secret file to %LOCALAPPDATA%\Peek\Profiles\&lt;profile_id&gt;.secret.
        /// Returns true if successful, false if password was rejected or arguments were invalid.
        /// </summary>
        bool LinkPassword(string profileId, SecureString password, string? username = null, string? targetDirectory = null);
    }
}
