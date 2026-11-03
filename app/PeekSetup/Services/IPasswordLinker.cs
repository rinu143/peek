using System.Security;

namespace PeekSetup.Services
{
    public interface IPasswordLinker
    {
        /// <summary>
        /// Validates the provided Windows password, writes the v2 LSA secret,
        /// and merges Authorized=[profileId] in the authorization store (cap 5).
        /// Returns exit code: 0 ok, 2 wrong password, 3 error, 5 identity limit.
        /// </summary>
        int LinkPassword(string profileId, SecureString password, string? username = null, string? sid = null);

        /// <summary>
        /// Authorizes an identity for an existing linked account:
        /// Verifies link exists (exit 6 if not), checks cap of 5 (exit 5 if full),
        /// verifies password via LogonUserW (exit 2 if wrong), rewrites v2 secret,
        /// and appends profileId to Authorized list.
        /// Returns exit code: 0 ok, 2 wrong password, 3 error, 5 identity limit, 6 not linked yet.
        /// </summary>
        int AuthorizeIdentity(string profileId, SecureString password, string? username = null, string? sid = null);

        /// <summary>
        /// Revokes an identity from the Authorized list for the given SID.
        /// If the list becomes empty, removes the secret and deletes the Identities key.
        /// Returns exit code: 0 ok, 3 error.
        /// </summary>
        int RevokeIdentity(string profileId, string sid);

        /// <summary>
        /// Deletes the Windows LSA secret and the Identities key for the given SID. Idempotent.
        /// </summary>
        bool UnlinkPassword(string sid);
    }
}
