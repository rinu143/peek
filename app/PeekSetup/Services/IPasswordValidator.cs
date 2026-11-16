using System.Security;

namespace PeekSetup.Services
{
    public interface IPasswordValidator
    {
        /// <summary>
        /// Validates a Windows account password using LogonUserW without allocating a managed String.
        /// Closes returned token handles immediately.
        /// </summary>
        bool Validate(string username, SecureString password, string? domain = null);

        /// <summary>
        /// Checks whether the user account has a password set.
        /// Returns false if the account has a blank/no password.
        /// </summary>
        bool HasPasswordSet(string username, string? domain = null);
    }
}
