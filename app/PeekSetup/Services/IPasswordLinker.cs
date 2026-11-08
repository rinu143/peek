using System.Security;

namespace PeekSetup.Services
{
    public interface IPasswordLinker
    {
        /// <summary>
        /// Validates the provided Windows password via IPasswordValidator and, if accepted,
        /// builds a binary payload and stores it securely in Windows LSA under L$PeekCred_&lt;sid&gt;.
        /// Returns true if successful, false if rejected or invalid.
        /// </summary>
        bool LinkPassword(string profileId, SecureString password, string? username = null, string? sid = null);

        /// <summary>
        /// Deletes the Windows LSA secret for the given SID. Idempotent.
        /// </summary>
        bool UnlinkPassword(string sid);
    }
}
