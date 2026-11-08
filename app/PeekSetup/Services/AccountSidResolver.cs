using System;
using System.Security.Principal;

namespace PeekSetup.Services
{
    public class AccountSidResolver : IAccountSidResolver
    {
        public bool ValidateUserSid(string username, string expectedSid)
        {
            if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(expectedSid))
            {
                return false;
            }

            string? resolvedSid = ResolveUserToSid(username);
            if (resolvedSid == null)
            {
                return false;
            }

            return string.Equals(resolvedSid, expectedSid, StringComparison.OrdinalIgnoreCase);
        }

        public string? ResolveUserToSid(string username)
        {
            if (string.IsNullOrWhiteSpace(username))
            {
                return null;
            }

            try
            {
                var account = new NTAccount(username);
                var sid = (SecurityIdentifier)account.Translate(typeof(SecurityIdentifier));
                return sid.Value;
            }
            catch
            {
                return null;
            }
        }
    }
}
